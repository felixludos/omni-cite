import sys, os, shutil
from pathlib import Path
import omnifig as fig
from tqdm import tqdm
from functools import lru_cache
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
from .features import Attachment_Feature, Item_Feature
from .auth import ZoteroProcess


@fig.script('item-feature', description='Extract feature from a Zotero entries')
def item_feature(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar_desc', '--', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	extractor: Item_Feature = A.pull('extractor', None)
	if manager.pbar_desc == '--':
		manager.pbar_desc = f'Extracting {extractor.feature_name}'
	
	A.push('brand_tag', f'feature:{extractor.feature_name}', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	manager.preamble(zot=zot)
	
	todo = zot.top(**extractor.get_zotero_kwargs())
	manager.log(f'Found {len(todo)} new items to process.')

	for item in manager.iterate(todo):
		@lru_cache
		def get_children(**kwargs):
			return zot.children(item['key'], **kwargs)
		try:
			extractor.extract(manager, item, get_children=get_children)
		except Exception as e:
			manager.log_error(e, item=item)
			# raise
		
	return manager.finish()


@fig.component('file-processor')
class File_Processor(fig.Configurable):
	def __init__(self, zotero_storage=str(Path.home() / 'Zotero/storage'),
	             cloud_root=str(Path.home() / 'OneDrive/Papers/zotero'),
	             attachment_name='PDF', snapshot_name='Snapshot', snapshot_to_pdf=True, remove_imports=False,
	             extension='pdf', suffix='', **kwargs):
		super().__init__(**kwargs)

		self.attachment_name = attachment_name
		self.snapshot_name = snapshot_name
		self.snapshot_to_pdf = snapshot_to_pdf
		self.remove_imports = remove_imports
		self.extension = extension
		self.suffix = suffix
		
		zotero_storage = Path(zotero_storage)
		assert zotero_storage.exists(), f'Missing zotero storage directory: {zotero_storage}'
		self.zotero_storage = zotero_storage
		
		cloud_root = Path(cloud_root)
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
				
				manager.add_update(linked_file, item, msg=f'Renamed to {dest.name}')
				
			else:
				manager.add_failed(item, msg=f'Unchanged: {dest.name}')
			
			return dest
			
		imports = [entry for entry in attachments
		           if entry['data'].get('linkMode') in {'imported_url', 'imported_file'}
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
	


@fig.script('process-attachments', description='Converts imported (local) PDFs and/or HTML Snapshots to linked PDFs.')
def process_pdfs(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar_desc', 'Processing Attachments', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')

	A.push('attachment-processor._type', 'file-processor', overwrite=False, silent=True)
	processor: File_Processor = A.pull('attachment-processor')
	
	A.push('brand_tag', 'attachments', overwrite=False, silent=True)
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


@fig.script('extract-attachment-feature',
            description='Generates a word cloud and list of key words from given source (linked) PDFs.')
def extract_attachment_feature(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar_desc', '--', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	extractor: Attachment_Feature = A.pull('feature-processor')
	
	if manager.pbar_desc == '--':
		manager.pbar_desc = f'Extracting {extractor.feature_name}'
	
	source_name = A.pull('source-name', 'PDF')
	source_type = A.pull('source-type', 'attachment')
	source_kwargs = A.pull('source-kwargs', {})
	
	A.push('brand_tag', f'feature:{extractor.feature_name}', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	manager.preamble(zot=zot)
	
	todo = zot.collect(q=source_name, itemType=source_type, **source_kwargs)
	atts = {}
	bad = []
	for item in todo:
		if 'parentItem' not in item['data']:
			bad.append(item)
		else:
			if item['data']['parentItem'] not in atts:
				atts[item['data']['parentItem']] = []
			atts[item['data']['parentItem']].append(item)
	
	if len(bad):
		manager.add_failed(*bad, msg=f'Missing parentItem for {len(bad)} items.')
	
	manager.log(f'Found {len(todo)} new attachments to extract {extractor.feature_name}.')
	
	for items in manager.iterate(atts.values()):
		try:
			extractor.extract(items, lambda: zot.item(item['data']['parentItem']), manager)
		except Exception as e:
			for item in items:
				manager.log_error(e, item=item)
		
	return manager.finish()



