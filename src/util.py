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
	

@fig.Component('script-manager')
class Script_Manager(fig.Configurable):
	def __init__(self, A, dry_run=None, silent=None, pbar=None, pbar_desc=None, **kwargs):
		
		if dry_run is None:
			dry_run = A.pull('dry-run', False)
		
		if silent is None:
			silent = A.pull('silent', False)
		
		if pbar is None:
			pbar = A.pull('pbar', not silent)
		
		if pbar_desc is None:
			pbar_desc = A.pull('pbar-desc', None)
		
		super().__init__(A, **kwargs)
		self.dry_run = dry_run
		self.silent = silent
		self.pbar = pbar
		self.pbar_desc = pbar_desc
		
		self._itr = None
		
		self.changes = []
		self.errors = []
		
	def preamble(self):
		pass
	
	def log(self, msg, **kwargs):
		if not self.silent:
			print(msg, **kwargs)
	
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
		
	def add_error(self, item, msg):
		self.errors.append([item, msg])
	
	def add_change(self, item, msg):
		self.changes.append([item, msg])

	@property
	def is_real_run(self):
		return not self.dry_run


	def finish(self):
		if self._itr is not None:
			self._itr.close()
		if not self.silent:
			self.print()
			

	def print(self, changes=True, errors=True):
		
		if changes:
			new = [[item['data']['key'], item['data']['itemType'], item['data']['title'], msg]
			       for item, msg in sorted(self.changes,
			                               key=lambda x: (x[0]['data']['itemType'], x[0]['data']['title']))]
			
			print('New')
			print(tabulate(new, headers=['Key', 'Type', 'Title', 'New']))
		
		if errors:
		
			errors = [[item['data']['key'], item['data']['itemType'], item['data']['title'], msg]
			          for item, msg in sorted(self.errors,
			                                  key=lambda x: (x[0]['data']['itemType'], x[0]['data']['title']))]
		
			print('Errors')
			print(tabulate(errors, headers=['Key', 'Type', 'Title', 'Error']))


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









