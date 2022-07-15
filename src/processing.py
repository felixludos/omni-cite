import sys, os, shutil
from pathlib import Path
import omnifig as fig
from tqdm import tqdm
from datetime import datetime, timezone
from tabulate import tabulate
from collections import OrderedDict

import re
import fitz
from urllib.parse import urlparse, quote
import requests
import pdfkit
import PyPDF2
from fuzzywuzzy import fuzz

from .util import create_url, create_file, get_now, Script_Manager
from .features import Feature_Extractor
from .auth import ZoteroProcess


class Item_Fixer(fig.Configurable):
	@property
	def fixer_name(self):
		raise NotImplementedError
	
	def fix(self, item):
		raise NotImplementedError


@fig.Component('url-fixer')
class Default_URL_Fixer(Item_Fixer):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		
		self.update_existing = A.pull('update-existing', False)

	@property
	def fixer_name(self):
		return 'url'

	def create_url(self, item):
		data = item['data']
		
		url = None
		if data['itemType'] == 'film' and data['extra'].startswith('IMDb'):
			url = 'https://www.imdb.com/title/{}/'.format(data['extra'].split('\n')[0].split('ID: ')[-1])
		if data['itemType'] == 'book' and len(data.get('ISBN', '')):
			url = 'https://isbnsearch.org/isbn/{}'.format(data['ISBN'].replace('-', ''))
			
		return url


	def fix(self, item, manager):
		current = item['data']['url']
		if current == '' or self.update_existing:
			url = self.create_url(item)
			if url is not None and url != current:
				manager.add_update(item, msg=url)
				item['data']['url'] = url
				return url
		manager.add_failed(item, msg=f'Unchanged: "{current}"')



@fig.Script('fix-items', description='Fix missing or wrong properties of zotero items')
def fix_items(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar-desc', '--', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	fixer = A.pull('fixer')
	if manager.pbar_desc == '--':
		manager.pbar_desc = f'Fixing {fixer.fixer_name}'

	A.push('brand-tag', f'props:{fixer.fixer_name}', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')

	zot_query = A.pull('zotero-query', {})

	manager.preamble(zot=zot)
	
	todo = zot.top(**zot_query)
	manager.log(f'Found {len(todo)} new items to process.')
	
	for item in manager.iterate(todo):
		try:
			fixer.fix(item, manager)
		except Exception as e:
			manager.log_error(e, item=item)
	
	return manager.finish()
	


@fig.Component('semantic-scholar-matcher')
class Semantic_Scholar_Matcher(fig.Configurable):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.match_ratio = A.pull('match-ratio', 92)
		
	query_url = 'http://api.semanticscholar.org/graph/v1/paper/search?query={}'
	
	
	def title_to_query(self, title):
		fixed = re.sub(r'[^a-zA-Z0-9 :-]', '', title)
		fixed = fixed.replace('-', ' ').replace(' ', '+')
		return quote(fixed).replace('%2B', '+')
		
	
	def call_home(self, url):
		out = requests.get(url).json()
		return out
	
	
	def format_result(self, ssid):
		return f'https://api.semanticscholar.org/{ssid}' if len(ssid) else ssid
		# return f'https://www.semanticscholar.org/paper/{ssid}' if len(ssid) else ssid
	
	
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



@fig.Script('link-semantic-scholar', description='Find papers on Zotero on Semantic Scholar')
def link_semantic_scholar(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
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
	
	attachment_name = A.pull('semantic-scholar-name', 'Semantic Scholar')
	
	manager.preamble(zot=zot)
	
	timestamp = get_now()
	
	todo = zot.top(itemType=paper_types)
	manager.log(f'Found {len(todo)} new items to process.')
	
	for item in manager.iterate(todo):
		url = matcher.find(item, dry_run=manager.dry_run)
		
		if url is not None and len(url):
			new = create_url(attachment_name, url, parentItem=item['data']['key'], accessDate=timestamp)
			manager.add_new(new, msg=f'Found {url}')
			manager.add_update(item, msg=f'Found {url}')
		else:
			manager.log_error('Matching Error', 'No match found', item=item)
	
	return manager.finish()
	


@fig.Component('file-processor')
class File_Processor(fig.Configurable):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)

		self.attachment_name = A.pull('attachment-name', 'PDF')
		self.snapshot_name = A.pull('snapshot-name', 'Snapshot')
		self.snapshot_to_pdf = A.pull('snapshot-to-pdf', True)
		self.remove_imports = A.pull('remove-imports', False)
		self.extension = A.pull('extension', 'pdf')
		self.suffix = A.pull('suffix', '')
		
		zotero_storage = Path(A.pull('zotero-storage', str(Path.home() / 'Zotero/storage')))
		assert zotero_storage.exists(), f'Missing zotero storage directory: {str(zotero_storage)}'
		self.zotero_storage = zotero_storage
		
		cloud_root = Path(A.pull('zotero-cloud-storage', str(Path.home() / 'OneDrive/Papers/zotero')))
		if not cloud_root.exists():
			os.makedirs(str(cloud_root))
		self.cloud_root = cloud_root
		
	
	def generate_file_path(self, item):
		name = self.gen_file_name(item)
		path = self.cloud_root / f'{name}{self.suffix}.{self.extension}'
		return path
		
	@staticmethod
	def force_unique_file_path(self, path):
		i = 1
		while path.exists():
			root, name, ext = path.parent, path.stem, path.suffix
			path = root / f'{name} ({i}){ext}'
			i += 1
		return path
	
	
	def export_as_pdf(self, src, dest):
		pdfkit.from_file(str(src), str(dest))
	
	
	def find_import_path(self, item):
		key = item['data']['key']
		fname = item['data']['filename']
		src = self.zotero_storage / key / fname
		if not src.exists():
			raise FileNotFoundError(str(src))
		return src
		
	
	class TooManyEntries(Exception):
		def __init__(self, children):
			# found = '\n'.join([' - {}'.format(entry['data']['title']) for entry in children])
			# super().__init__(found)
			super().__init__(', '.join([f'{entry.get("data", {}).get("key")}:{entry.get("data", {}).get("title")}'
			                            for entry in children]))
			self.children = children
	
	
	class NoEntryFound(Exception):
		pass
	
	
	def process(self, item, attachments, manager):
		existing = [entry for entry in attachments
		            if entry['data'].get('linkMode') == 'linked_file'
		            and entry['data'].get('contentType') == 'application/pdf']
		
		dest = self.generate_file_path(item)
		
		if len(existing) > 1:
			raise self.TooManyEntries(existing)
		
		if len(existing) == 1:
			linked_file = existing[0]
			old = Path(linked_file['data']['path'])
			
			if dest != old:
				linked_file['data']['path'] = str(dest)
				linked_file['data']['title'] = self.attachment_name
				if manager.is_real_run:
					shutil.move(str(old), str(dest))
				
				manager.add_update(linked_file, msg=f'Renamed to {dest.name}')
				
			else:
				manager.add_failed(item, msg=f'Unchanged: {dest.name}')
			
			return dest
			
		imports = [entry for entry in attachments
		           if entry['data'].get('linkMode') == 'imported_url'
		           and entry['data'].get('contentType') == 'application/pdf']
		
		if len(imports) > 1:
			raise self.TooManyEntries(imports)
		
		if len(imports) == 1:
			old = self.find_import_path(imports[0])
			
			if manager.is_real_run:
				if self.remove_imports:
					shutil.move(str(old), str(dest))
					manager.add_remove(imports[0], msg=f'Removed {old}')
				else:
					shutil.copy(str(old), str(dest))

			msg = f'Imported {old}'
			
		else:
			if not self.snapshot_to_pdf:
				raise self.NoEntryFound()
				
			snapshots = [entry for entry in attachments
			             if entry['data']['title'] == 'Snapshot'
			             and entry['data'].get('linkMode') == 'imported_url'
			             and entry['data'].get('contentType') == 'text/html']
			
			if len(snapshots) > 1:
				raise self.TooManyEntries(snapshots)
			
			if len(snapshots) == 0:
				raise self.NoEntryFound()
			
			old = self.find_import_path(snapshots[0])
			
			if manager.is_real_run:
				self.export_as_pdf(old, dest)
			
			msg = f'Converted {old}'
			
		linked_file = create_file(self.attachment_name, dest, parentItem=item['data']['key'],
		                          contentType='application/pdf')
		
		manager.add_new(linked_file, msg=msg)
		manager.add_update(item, msg=msg)
		
	
	def gen_file_name(self, item):
		meta = item['meta']
		
		title = re.sub('<.*?>', '', item['data']['title']).replace(' - ', ' ')
		authors = meta.get('creatorSummary', '').replace('.', '').replace(' et al', '+').replace(' and ', '+')
		year = meta.get('parsedDate', '').split('-')[0]
		if len(year):
			year = f' ({year})'
		
		if len(authors) and not len(year):
			prefix = f'{authors} - '
		else:
			prefix = f'{authors}{year} '
		
		value = f'{prefix}{title}'.replace('  ', ' ')
		value = re.sub(r'[^\w\s\-_()+]', '', value).strip()
		return value
	


@fig.Script('process-attachments', description='Converts imported (local) PDFs and/or HTML Snapshots to linked PDFs.')
def process_pdfs(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar-desc', 'Processing Attachments', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')

	A.push('attachment-processor._type', 'file-processor', overwrite=False, silent=True)
	processor: File_Processor = A.pull('attachment-processor')
	
	A.push('brand-tag', 'attachments', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	manager.preamble(zot=zot)

	todo = zot.top()
	manager.log(f'Found {len(todo)} new items to process.')
	
	for item in manager.iterate(todo):
		attachments = zot.children(item['data']['key'], itemType='attachment')
		try:
			processor.process(item, attachments, manager)
		except Exception as e:
			manager.log_error(e, item=item)
	
	return manager.finish()



@fig.Script('extract-attachment-feature', description='Generates a word cloud and list of key words from given source (linked) PDFs.')
def extract_attachment_feature(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar-desc', '--', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	extractor: Feature_Extractor = A.pull('feature-processor')
	
	if manager.pbar_desc == '--':
		manager.pbar_desc = f'Extracting {extractor.feature_name}'
	
	source_name = A.pull('source-name', 'PDF')
	source_type = A.pull('source-type', 'attachment')
	source_kwargs = A.pull('source-kwargs', {})
	
	A.push('brand-tag', f'feature:{extractor.feature_name}', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	manager.preamble(zot=zot)
	
	todo = zot.collect(q=source_name, itemType=source_type, **source_kwargs)
	atts = {}
	for item in todo:
		if item['data']['parentItem'] not in atts:
			atts[item['data']['parentItem']] = []
		atts[item['data']['parentItem']].append(item)
	
	manager.log(f'Found {len(todo)} new attachments to extract {extractor.feature_name}.')
	
	for items in manager.iterate(atts.values()):
		try:
			extractor.extract(items, lambda: zot.item(item['data']['parentItem']), manager)
		except Exception as e:
			for item in items:
				manager.log_error(e, item=item)
		
	return manager.finish()



