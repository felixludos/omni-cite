import sys, os, shutil
from pathlib import Path
import omnifig as fig
from tqdm import tqdm
from datetime import datetime, timezone
from tabulate import tabulate

import re
import urllib.parse
import requests
from fuzzywuzzy import fuzz

from .util import create_url, get_now, Script_Manager, split_by_filter
from .auth import ZoteroProcess


@fig.Component('default-url')
class Default_URL_Maker(fig.Configurable):
	def create_url(self, item):
		data = item['data']
		
		url = None
		if data['itemType'] == 'film' and data['extra'].startswith('IMDb'):
			url = 'https://www.imdb.com/title/{}/'.format(data['extra'].split('\n')[0].split('ID: ')[-1])
		if data['itemType'] == 'book' and len(data.get('ISBN', '')):
			url = 'https://isbnsearch.org/isbn/{}'.format(data['ISBN'].replace('-', ''))
			
		return url



@fig.Script('fill-in-urls')
def fill_in_urls(A):
	A.push('manager._type', 'script-manager', overwrite=False, silent=True)
	A.push('manager.pbar-desc', 'Filling in URLs', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	update_existing = A.pull('update-existing', False)
	
	A.push('url-maker._type', 'default-url', overwrite=False, silent=True)
	url_maker: Default_URL_Maker = A.pull('url-maker')

	A.push('brand-tag', 'url', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')

	brand_errors = A.pull('brand-errors', False)
	
	manager.preamble()
	
	todo = zot.top()
	manager.log(f'Found {len(todo)} new items to process.')
	
	changed_items = []
	
	for item in manager.iterate(todo):
		current = item['data']['url']
		if current == '' or update_existing:
			try:
				url = url_maker.create_url(item)
			except Exception as e:
				manager.add_error(item, f'{type(e)}: {str(e)}')
				if brand_errors:
					changed_items.append(item)
			else:
				msg = f'Unchanged: {current}'
				if url is not None and url != current:
					item['data']['url'] = url
					msg = f'{repr(current)} -> {repr(url)}'
				manager.add_new(item, msg)
				changed_items.append(item)
	
	if manager.is_real_run:
		zot.update_items(changed_items)
	
	manager.finish()
	return manager



@fig.Component('semantic-scholar-matcher')
class Semantic_Scholar_Matcher(fig.Configurable):
	def __int__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.match_ratio = A.pull('match-ratio', 92)
		
	query_url = 'http://api.semanticscholar.org/graph/v1/paper/search?query={}'
	
	
	def title_to_query(self, title):
		fixed = re.sub(r'[^a-zA-Z0-9 :-]', '', title)
		fixed = fixed.replace('-', ' ').replace(' ', '+')
		return urllib.parse.quote(fixed).replace('%2B', '+')
		
	
	def call_home(self, url):
		out = requests.get(url).json()
		return out
	
	
	def format_result(self, ssid):
		return f'https://www.semanticscholar.org/paper/{ssid}' if len(ssid) else ssid
	
	
	def find(self, item, dry_run=False):
		title = item['data']['title']
		clean = self.title_to_query(title)
		url = self.query_url.format(clean)
		
		if dry_run:
			return url
		
		out = self.call_home(url)
		
		for res in out.get('data', []):
			if fuzz.ratio(res.get('title', ''), title) >= self.match_ratio:
				return self.format_result(res.get('paperId', ''))
		return ''

	

@fig.Script('link-semantic-scholar')
def link_semantic_scholar(A):
	A.push('manager._type', 'script-manager', overwrite=False, silent=True)
	A.push('manager.pbar-desc', 'Linking Semantic Scholar', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	paper_types = A.pull('paper-types', ['conferencePaper', 'journalArticle', 'preprint'])
	if paper_types is not None and not isinstance(paper_types, str):
		paper_types = ' || '.join(paper_types)
	
	A.push('semantic-scholar-matcher._type', 'semantic-scholar-matcher', overwrite=False, silent=True)
	matcher: Semantic_Scholar_Matcher = A.pull('semantic-scholar-matcher')

	A.push('brand-tag', 'semantic-scholar', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	brand_errors = A.pull('brand-errors', False)
	
	attachment_name = A.pull('semantic-scholar-name', 'Semantic Scholar')
	
	manager.preamble()
	
	timestamp = get_now()
	
	todo = zot.top(itemType=paper_types)
	manager.log(f'Found {len(todo)} new items to process.')
	
	new_items = []
	updated_items = []
	
	for item in manager.iterate(todo):
		url = matcher.find(item, dry_run=manager.dry_run)
		
		if url is not None and len(url):
			new = create_url(attachment_name, url, parentItem=item['data']['key'], accessDate=timestamp)
			new_items.append(new)
			updated_items.append(item)
			manager.add_change(item, url)
		else:
			manager.add_error(item, 'No Semantic Scholar entry found')
			if brand_errors:
				updated_items.append(item)
		
	if manager.is_real_run:
		if len(new_items):
			zot.create_items(new_items)
		if len(updated_items):
			zot.update_items(updated_items)
	
	manager.finish()
	return manager



@fig.Script('process-pdfs')
def process_pdfs(A):
	A.push('manager._type', 'script-manager', overwrite=False, silent=True)
	A.push('manager.pbar-desc', 'Processing PDFs', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')

	remove_imported = A.pull('remove-imported', False)
	snapshot_to_pdf = A.pull('convert-snapshots', True)
	
	zotero_storage = Path(A.pull('zotero-storage', str(Path.home() / 'Zotero/storage')))
	assert zotero_storage.exists(), f'Missing zotero storage directory: {str(zotero_storage)}'
	
	cloud_root = Path(A.pull('zotero-cloud-storage', str(Path.home() / 'OneDrive/Papers/zotero')))
	if not cloud_root.exists():
		os.makedirs(str(cloud_root))
	
	A.push('brand-tag', 'pdf', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	brand_errors = A.pull('brand-errors', False)

	attachment_name = A.pull('pdf-attachment-name', 'PDF')
	snapshot_name = A.pull('snapshot-name', 'Snapshot')

	manager.preamble()

	new_items = []
	updated_items = []
	removed_items = []

	# snapshots = zot.collect(q=snapshot_name, itemType='attachment')
	# snapshots, unused = split_by_filter(snapshots,
	#                                     lambda item: item['data']['linkMode'] == 'imported_url'
	#                                                  and item['data'].get('contentType') == 'text/html')
	# if zot.brand_tag is not None:
	# 	updated_items.extend(unused) # skip (and brand)
	#
	# snapshots = {item['data']['parentItem']: item for item in snapshots}
	
	todo = zot.top()
	manager.log(f'Found {len(todo)} new items to process.')
	
	raise NotImplementedError

	




