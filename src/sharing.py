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

from .auth import get_zotero, get_onedrive
from .attachments import gen_entry_filename, add_link_attachment, filter_linked_pdfs, filter_wordcloud


def filter_share_links(children):
	return [child for child in children
			if child['data']['itemType'] == 'attachment' and child['data']['title'] == 'OneDrive'
			and child['data'].get('linkMode') == 'linked_url'
			and child['data'].get('contentType') == ''
	        ]


# @backoff.on_exception(backoff.expo,
#                       requests.exceptions.RequestException,
#                       max_tries=8,
#                       jitter=None)
# def get_url(url, req_type='get', **kwargs):
# 	out = requests.post(url, **kwargs) if req_type == 'post' else requests.get(url, **kwargs)
#
# 	if out.status_code == 429:
# 		raise requests.exceptions.RequestException
#
# 	return out


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


@fig.Script('share-pdfs')
def share_pdfs(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	# overwrite_existing = A.pull('overwrite-existing', False)
	
	cloud_root = Path(A.pull('zotero-cloud-storage', str(Path.home() / 'OneDrive/Papers/zotero')))
	if not cloud_root.exists():
		os.makedirs(str(cloud_root))
	
	onedrive_root = Path(A.pull('onedrive-root', str(Path.home() / 'OneDrive')))
	
	update_existing = A.pull('update-existing', False)
	
	zot = get_zotero(A)
	
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

















