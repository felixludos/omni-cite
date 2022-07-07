import copy
from datetime import datetime, timezone
from tabulate import tabulate

import re
import urllib.parse
import requests
from fuzzywuzzy import fuzz


def get_now():
	return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
	# return datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def print_new_errors(new, errors):
	
	new = [[item['data']['key'], item['data']['itemType'], item['data']['title'], msg]
	       for item, msg in sorted(new, key=lambda x: (x[0]['data']['itemType'], x[0]['data']['title']))]
	errors = [[item['data']['key'], item['data']['itemType'], item['data']['title'], msg]
	          for item, msg in sorted(errors, key=lambda x: (x[0]['data']['itemType'], x[0]['data']['title']))]
	
	print('New')
	print(tabulate(new, headers=['Key', 'Type', 'Title', 'New']))
	
	print('Errors')
	print(tabulate(errors, headers=['Key', 'Type', 'Title', 'Error']))
	
	pass




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









