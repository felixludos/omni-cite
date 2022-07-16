from typing import Union, List, Dict
import copy
from datetime import datetime, timezone
from tqdm import tqdm
from tabulate import tabulate

import omnifig as fig

import re
import urllib.parse
import requests
from fuzzywuzzy import fuzz


def get_now():
	return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
	# return datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def split_by_filter(options, filter_fn):
	good, bad = [], []
	for option in options:
		(good if filter_fn(option) else bad).append(option)
	return good, bad
	

@fig.Component('zotero-manager')
class Script_Manager(fig.Configurable):
	def __init__(self, A, dry_run=None, silent=None, brand_errors=None,
	             pbar=None, pbar_desc=None, **kwargs):
		
		if dry_run is None:
			dry_run = A.pull('dry-run', False)
		
		if silent is None:
			silent = A.pull('silent', False)
		
		if pbar is None:
			pbar = A.pull('pbar', not silent)
		
		if pbar_desc is None:
			pbar_desc = A.pull('pbar-desc', None)
		
		if brand_errors is None:
			brand_errors = A.pull('brand-errors', False)
		
		super().__init__(A, **kwargs)
		self.dry_run = dry_run
		self.silent = silent
		self.pbar = pbar
		self.pbar_desc = pbar_desc
		
		self.brand_errors = brand_errors
		
		self._itr = None
		
		self.successes = []
		self.errors = []
		
		
	class ManagerError(Exception):
		pass
		
	def preamble(self, zot=None):
		self.zot = zot
		if self.is_real_run and self.zot is None:
			raise self.ManagerError('Missing zotero instance.')
		
		self.new_items = []
		self.updated_items = []
		self.remove_items = []
		self.failed_items = []
		
		
	def log(self, msg, **kwargs):
		if not self.silent:
			print(msg, **kwargs)
	
	
	class SafetyMonitor:
		def __init__(self, ignoreable=[], reportable=[], log=None, item=None):
			self._ignorable = tuple(ignoreable)
			self._reportable = tuple(reportable)
			self._log = log
			self._log_item = item
		
		def __enter__(self):
			pass
		
		def __exit__(self, exc_type, exc_val, exc_tb):
			if issubclass(exc_type, self._reportable):
				if self._log is not None:
					self._log.log_error(exc_type.__name__, exc_val, self._log_item)
				return True
			if issubclass(exc_type, self._ignorable):
				return True
			
			
	def safety(self, *etypes, item=None, log_exceptions=True):
		ignorable, reportable = ([], etypes) if log_exceptions else (etypes, [])
		return self.SafetyMonitor(ignoreable=ignorable, reportable=reportable, log=self, item=item)
		
		
	def iterate(self, itr, desc=None, total=None, **kwargs):
		if self.pbar:
			if self._itr is not None:
				self._itr.close()
			
			if desc is None:
				desc = self.pbar_desc
			
			itr = tqdm(itr, total=total, desc=desc, **kwargs)
			self._itr = itr
		return itr
	
	def set_description(self, desc):
		if self._itr is not None:
			self._itr.set_description(desc)
		
	
	def add_new(self, *items, msg='New item added.'):
		for item in items:
			self.new_items.append(item)
			self.successes.append(['new', msg, item])
		
	def add_update(self, *items, msg='Item updated.'):
		for item in items:
			self.updated_items.append(item)
			self.successes.append(['updated', msg, item])
	
	def add_remove(self, *items, msg='Item removed.'):
		for item in items:
			self.remove_items.append(item)
			self.successes.append(['removed', msg, item])
		
	def add_failed(self, *items, msg='Item failed.'):
		for item in items:
			self.failed_items.append(item)
			self.successes.append(['failed', msg, item])
	
	def log_error(self, etype: Union[str, Exception], emsg: str = None, item: Dict = {}):
		assert emsg is not None or isinstance(etype, Exception), 'Must provide an error message.'
		self.errors.append([etype, emsg, item])
		if self.brand_errors and len(item):
			self.failed_items.append(item)
	
	def log_success(self, stype: str, smsg: str, item: Dict = {}):
		self.successes.append([stype, smsg, item])


	@property
	def is_real_run(self):
		return not self.dry_run

	
	def write_zotero(self):
		self.log('Writing to Zotero library now.')
		
		if len(self.new_items):
			out = self.zot.create_items(self.new_items)
			keys = [item.get('key') for item in out.get('successful', {}).values()]
			self.log(f'Zotero: Created {len(keys)}/{len(self.new_items)} new items: {", ".join(keys)}')
			self.out_new = out
		
		todo = self.updated_items
		fmsg = ''
		if self.zot.brand_tag is not None:
			todo = todo + self.failed_items
			fmsg = f' (+{len(self.failed_items)} bad)'
			
		if len(todo):
			worked = self.zot.update_items(todo)
			if worked:
				self.log(f'Zotero: Updated {len(self.updated_items)}{fmsg} items: '
				         f'{", ".join([item.get("key") for item in self.updated_items])}')
			else:
				self.log(f'Zotero: Updating {len(self.updated_items)}{fmsg} items failed.')
			self.out_updated = worked
			
		if len(self.remove_items):
			worked = self.zot.delete_items(self.remove_items)
			if worked:
				self.log(f'Zotero: Removed {len(self.remove_items)} items: '
				         f'{", ".join([item.get("key") for item in self.remove_items])}')
			else:
				self.log(f'Zotero: Removing {len(self.remove_items)} items failed.')
			self.out_removed = worked
		
		
	def write_dry_run(self):
		self.log('Dry run, not writing to Zotero.')
		self.log(f'Would create {len(self.new_items)} new items.')
		self.log(f'Would update {len(self.updated_items)} items.')
		self.log(f'Would remove {len(self.remove_items)} items.')
	

	def finish(self):
		if self._itr is not None:
			self._itr.close()
		
		if self.is_real_run:
			self.write_zotero()
		else:
			self.write_dry_run()
		
		self.print()
			
		return self
			

	def print(self, successes=True, errors=True):
		if not self.silent and successes:
			print()
			success = [[str(typ), str(msg),
			        item.get('data', item).get('key', '--'),
			        item.get('data', item).get('itemType', '--'),
			        item.get('data', item).get('title', '--'), ]
			       for typ, msg, item in self.successes]
			success.sort(key=lambda x: (x[0], x[1], x[3], x[4]))
			print(tabulate(success, headers=['Success', 'Message', 'Key', 'Type', 'Title']))
			
		if errors:
			if len(self.errors):
				print()
				errs = [[type(typ).__name__ if msg is None else str(typ),
				            str(typ) if msg is None else str(msg),
				        item.get('data', item).get('key', '--'),
				        item.get('data', item).get('itemType', '--'),
				        item.get('data', item).get('title', '--'), ]
				       for typ, msg, item in self.errors]
				errs.sort(key=lambda x: (x[0], x[1], x[3], x[4]))
				print(tabulate(errs, headers=['Error', 'Message', 'Key', 'Type', 'Title']))
			else:
				print('No Errors')


_note_template = {'itemType': 'note',
 'note': '',
 'tags': [],
 'collections': [],
 'relations': {}}


def create_note(note, **kwargs):
	data = copy.deepcopy(_note_template)
	data.update(kwargs)
	data['note'] = note
	return data
	

_file_template = {
   'itemType': 'attachment',
   'linkMode': 'linked_file',
   'title': '',
   'accessDate': '',
   'url': '',
   'note': '',
   'contentType': '',
   'charset': '',
   'path': '',
   'tags': [],
   'relations': {},}


def create_file(title, path, **kwargs):
	data = copy.deepcopy(_file_template)
	data.update(kwargs)
	data['title'] = title
	data['path'] = str(path)
	return data


_link_template = {'itemType': 'attachment',
 'linkMode': 'linked_url',
 'title': '',
 'accessDate': '',
 'url': '',
 'note': '',
 'tags': [],
 'collections': [],
 'relations': {},
 'contentType': '',
 'charset': ''}


def create_url(title, url, **kwargs):
	data = copy.deepcopy(_link_template)
	data.update(kwargs)
	data['title'] = title
	data['url'] = url
	return data









