import sys, os, shutil
import copy
import omnifig as fig
from pathlib import Path
from tqdm import tqdm
import backoff
from tabulate import tabulate
from collections import OrderedDict
from urllib.parse import urlparse
from wordcloud import WordCloud, STOPWORDS

import re
import fitz
import urllib.parse
import requests
import pdfkit
import PyPDF2
from fuzzywuzzy import fuzz

from .auth import get_onedrive
from .attachments import gen_entry_filename, add_link_attachment, filter_linked_pdfs, filter_wordcloud
from .util import create_file, create_url, create_note, print_new_errors


def filter_share_links(children):
	return [child for child in children
			if child['data']['itemType'] == 'attachment' and child['data']['title'] == 'OneDrive'
			and child['data'].get('linkMode') == 'linked_url'
			and child['data'].get('contentType') == ''
	        ]



def batch_share_links(file_ids, header, share=True):
	def generate_request(url, method='GET', **kwargs):
		req = {'method': method.upper(), 'url': url, **kwargs}
		return req
	
	permissions = {"type": "edit", "scope": "anonymous"} if share else {'type': 'embed', "scope": "anonymous"}
	reqs = [generate_request(f'/me/drive/items/{item_id}/createLink', method='POST',
	                         body=permissions,
	                         headers={'content-type': 'application/json'})
	        for item_id in file_ids]
	for i, req in enumerate(reqs):
		req['id'] = str(i + 1)
	
	body = {'requests': reqs}
	
	resp = requests.post('https://graph.microsoft.com/v1.0/$batch', json=body,
	                      headers={'content-type': 'application/json', **header})
	out = resp.json()
	links = [r['body']['link']['webUrl'] for r in sorted(out['responses'], key=lambda r: r['id'])]
	
	return links


@fig.Script('onedrive-links')
def onedrive_links(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	brand_tag = A.pull('brand-tag', 'code')
	ignore_brand_tag = A.pull('ignore-brand', False)
	brand_errors = A.pull('brand-errors', False)
	
	cloud_root = Path(A.pull('zotero-cloud-storage', str(Path.home() / 'OneDrive/Papers/zotero')))
	if not cloud_root.exists():
		os.makedirs(str(cloud_root))
	
	onedrive_root = Path(A.pull('onedrive-root', str(Path.home() / 'OneDrive')))
	
	source_name = A.pull('source-name', 'PDF')
	link_name = 'OneDrive'
	# link_name = {None: 'OneDrive', 'view': 'OneDrive View', 'edit': 'OneDrive Edit'}
	
	marked = []
	new = []
	def add_new(item, msg):
		marked.append(item)
		new.append([item, msg])
	errors = []
	def add_error(item, msg):
		if brand_errors:
			marked.append(item)
		errors.append([item, msg])
	
	A.push('onedrive._type', 'onedrive-auth', overwrite=False, silent=True)
	auth = A.pull('onedrive')
	auth.authorize()

	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot = A.pull('zotero')
	
	connection = cloud_root.relative_to(onedrive_root)
	
	file_info = auth.list_dir(connection)
	file_id_table = {item['name']: item['webUrl'] for item in file_info}
	
	updated_links = []
	new_links = []

	itr = tqdm(zot.top(brand_tag=brand_tag if ignore_brand_tag else None))
	for item in itr:
		data = item['data']
		itr.set_description('OneDrive Links {}'.format(data['key']))
		
		attachments = zot.children(data['key'], itemType='attachment')
		
		existing = [entry for entry in attachments
		            if entry['data']['title'] == link_name
		            and entry['data'].get('linkMode') == 'linked_url']
		
		sources = [source for source in attachments
		           if source['data']['itemType'] == 'attachment'
		           and (source_name is None or source['data']['title'] == source_name)
		           and source['data'].get('contentType') == 'application/pdf']
		missing = [source for source in sources if 'path' not in source['data']
		           or Path(source['data']['path']).name not in file_id_table]
		
		if len(existing) > 1:
			add_error(item, 'Found multiple code link notes')
		else:
			if len(sources) > 1:
				found = '\n'.join([' - {}'.format(entry['data']['title']) for entry in missing])
				add_error(item, f'Too many sources found: \n{found}')
			elif len(sources) == 0:
				add_error(item, 'No source/s found')
			elif len(missing):
				found = '\n'.join([' - {}'.format(entry['data']['title']) for entry in missing])
				add_error(item, f'File not found: \n{found}')
			else:
				url = file_id_table[Path(sources[0]['data']['path']).name]
				add_new(item, url)
				if len(existing) == 1:
					existing[0]['data']['url'] = url
					updated_links.append(existing[0])
				else:
					url_obj = create_url(link_name, url, parentItem=data['key'])
					new_links.append(url_obj)
	
	if not dry_run:
		if len(marked):
			zot.update_items(marked, brand_tag=brand_tag)
		if len(updated_links):
			zot.update_items(updated_links)
		if len(new_links):
			zot.create_items(new_links)
	
	if not silent:
		print_new_errors(new, errors)
	return new, errors


def share_pdfs(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	# overwrite_existing = A.pull('overwrite-existing', False)
	
	cloud_root = Path(A.pull('zotero-cloud-storage', str(Path.home() / 'OneDrive/Papers/zotero')))
	if not cloud_root.exists():
		os.makedirs(str(cloud_root))
	
	onedrive_root = Path(A.pull('onedrive-root', str(Path.home() / 'OneDrive')))
	
	update_existing = A.pull('update-existing', False)

	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot = A.pull('zotero')
	
	_header = get_onedrive(A)
	
	endpoint = 'https://graph.microsoft.com/v1.0/me'
	connection = cloud_root.relative_to(onedrive_root)
	
	# response = get_url(endpoint + f'/drive/root:/{str(connection)}/:/children', headers=_header)
	response = requests.get(endpoint + f'/drive/root:/{str(connection)}/:/children', headers=_header)
	out = response.json()
	file_id_table = {item['name']: item['id'] for item in out['value']}
	
	new = []
	errors = []
	
	def extract_warning(item, msg):
		errors.append([item['data']['key'], item['data']['itemType'], item['data']['title'], msg])
	
	file_ids = {}
	
	itr = tqdm(zot.top())
	for item in itr:
		data = item['data']
		itr.set_description('Checking Share Links {}'.format(data['key']))
		children = zot.children(data['key'])
		
		links = filter_share_links(children)
		pdfs = filter_linked_pdfs(children)
	
		if len(links) == 0 or (len(links) == 1 and update_existing):
			if len(pdfs) == 1:
				dest = Path(pdfs[0].get('data', {}).get('path'))
				if is_in_dir(dest, onedrive_root):
					file_id = file_id_table[dest.name]
					file_ids[file_id] = (item, links[0] if len(links) else None)
				
				else:
					extract_warning(item, f'Bad PDFs location: {str(dest)}')
			
			else:  # multiple links
				extract_warning(item, 'Too many PDFs' if len(pdfs) else 'No PDFs found')
		
		elif len(links) == 1:
			pass
		
		else:
			extract_warning(item, 'Too many shared links')
	
	order = list(file_ids.keys())
	shares = batch_share_links(order, _header)
	
	itr = tqdm(zip(order, shares), total=len(order))
	for fid, slink in itr:
		item, child = file_ids[fid]
		data = item['data']
		itr.set_description('Creating Share Links {}'.format(data['key']))
		# children = zot.children(data['key'])
	
		new.append([item['data']['key'], item['data']['itemType'], item['data']['title'], slink])
		
		if not dry_run:
			if child is None:
				add_link_attachment(zot, data['key'], 'OneDrive', slink)
			else:
				child['url'] = slink
				zot.update_item(child)
	
	if not silent:
		print('New')
		print(tabulate(new, headers=['Key', 'Type', 'Title', 'Share Link']))
		
		print('Errors')
		print(tabulate(sorted(errors, key=lambda x: (x[1], x[2])), headers=['Key', 'Type', 'Title', 'Msg']))
	
	return new, errors


@fig.Script('embed-images')
def embed_images(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	# overwrite_existing = A.pull('overwrite-existing', False)
	
	wordcloud_root = Path(A.pull('wordcloud-root', str(Path.home() / 'OneDrive/Papers/wordclouds')))
	if not wordcloud_root.exists():
		os.makedirs(str(wordcloud_root))
	
	onedrive_root = Path(A.pull('onedrive-root', str(Path.home() / 'OneDrive')))
	
	update_existing = A.pull('update-existing', False)
	
	zot = get_zotero(A)
	
	_header = get_onedrive(A)
	
	endpoint = 'https://graph.microsoft.com/v1.0/me'
	connection = wordcloud_root.relative_to(onedrive_root)
	
	# response = get_url(endpoint + f'/drive/root:/{str(connection)}/:/children', headers=_header)
	response = requests.get(endpoint + f'/drive/root:/{str(connection)}/:/children', headers=_header)
	out = response.json()
	file_id_table = {item['name']: item['id'] for item in out['value']}
	
	new = []
	errors = []
	
	def extract_warning(item, msg):
		errors.append([item['data']['key'], item['data']['itemType'], item['data']['title'], msg])
	
	file_ids = {}
	
	itr = tqdm(zot.top())
	for item in itr:
		data = item['data']
		itr.set_description('Checking Embed Links {}'.format(data['key']))
		children = zot.children(data['key'])
		
		wc = filter_wordcloud(children)
		
		if len(wc) == 0:
			extract_warning(item, 'No wordcloud')
		elif len(wc) == 1 and (len(wc[0]['data']['url']) == 0 or update_existing):
			dest = Path(wc[0].get('data', {}).get('path'))
			if is_in_dir(dest, onedrive_root):
				file_id = file_id_table[dest.name]
				file_ids[file_id] = (item, wc[0])
			
			else:
				extract_warning(item, f'Bad wordcloud location: {str(dest)}')
			
		elif len(wc[0]['data']['url']):
			pass
		
		else:
			extract_warning(item, 'Too many embed links')
	
	order = list(file_ids.keys())
	shares = batch_share_links(order, _header, share=False)
	
	# hack from https://bydik.com/onedrive-direct-link/
	shares = [share.replace('embed', 'download') for share in shares]
	
	itr = tqdm(zip(order, shares), total=len(order))
	for fid, slink in itr:
		item, child = file_ids[fid]
		data = item['data']
		itr.set_description('Creating Embed Links {}'.format(data['key']))
		# children = zot.children(data['key'])
		
		new.append([item['data']['key'], item['data']['itemType'], item['data']['title'], slink])
		
		if not dry_run:
			child['data']['url'] = slink
			zot.update_item(child)
	
	if not silent:
		print('New')
		print(tabulate(new, headers=['Key', 'Type', 'Title', 'Embed Link']))
		
		print('Errors')
		print(tabulate(sorted(errors, key=lambda x: (x[1], x[2])), headers=['Key', 'Type', 'Title', 'Msg']))
	
	return new, errors


def is_in_dir(path, base):
	try:
		Path(path).relative_to(base)
	except ValueError:
		return False
	else:
		return True

















