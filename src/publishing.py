from pathlib import Path
import json
from typing import Union, List, Dict, Callable, Tuple, Optional
from functools import lru_cache
import re
try:
	from omnibelt import md5
except ImportError:
	import hashlib
	def md5(s):
		if not isinstance(s, str):
			s = json.dumps(s, sort_keys=True)
		return hashlib.md5(s.encode('utf-8')).hexdigest()

import omnifig as fig
import requests
from dateutil import parser

from .util import Script_Manager, create_url, get_now
from .auth import ZoteroProcess
from .features import Attachment_Based


class Extractor(fig.Configurable):
	class ExtractionError(Exception):
		pass
	
	class SkipItem(Exception):
		pass
	
	def __call__(self, item, get_children=None):
		raise NotImplementedError
		

class SimpleExtractor(Extractor):
	_data_key = None
	def __call__(self, item, get_children=None):
		return item['data'].get(self._data_key, '')


for _property in ['title', 'abstractNote', 'url', 'dateAdded', 'libraryCatalog',
                  'accessDate', 'itemType', 'key', 'DOI', 'language']:
	fig.component(f'extractor/{_property}')(
		type(f'{_property.capitalize()}_SimpleExtractor', (SimpleExtractor,), {'_data_key': _property}))
del _property


@fig.component('extractor/nickname')
class Nickname(Extractor):
	def __call__(self, item, get_children=None):
		if 'shortTitle' in item['data'] and len(item['data']['shortTitle']):
			return item['data']['shortTitle']
		return item['data']['title']


@fig.component('extractor/date')
class Date(Extractor):
	def __call__(self, item, get_children=None):
		date = item['data'].get('date', '')
		try:
			return parser.parse(date).isoformat()
		except parser.ParserError:
			return None


@fig.component('extractor/zotero-link')
class ZoteroLink(Extractor):
	def __call__(self, item, get_children=None):
		return item['links'].get('alternate', {}).get('href')


@fig.component('extractor/creators')
class Creators(Extractor):
	def __call__(self, item, get_children=None):
		names = []
		for creator in item['data']['creators']:
			if 'name' in creator:
				names.append(creator['name'])
			elif 'lastName' in creator and 'firstName' in creator:
				names.append(f'{creator["firstName"]} {creator["lastName"]}')
			elif 'lastName' in creator:
				names.append(creator['lastName'])
			elif 'firstName' in creator:
				names.append(creator['firstName'])
			else:
				raise self.ExtractionError(f'No name found for creator {creator}')

		return '\n'.join(names[:100])[:1000]


@fig.component('extractor/tags')
class Tags(Extractor):
	def __init__(self, include_auto_tags=False, include_real_tags=True, **kwargs):
		super().__init__(**kwargs)
		self.include_auto_tags = include_auto_tags
		self.include_real_tags = include_real_tags
		assert self.include_auto_tags or self.include_real_tags, 'At least one of include-auto-tags ' \
		                                                         'or include-real-tags must be True'
	
	def __call__(self, item, get_children=None):
		return [tag['tag'].replace(',', '') for tag in item['data']['tags']
		        if ((self.include_real_tags and tag.get('type', 0) == 0)
		        or (self.include_auto_tags and tag.get('type', 0) == 1))]


@fig.component('extractor/collections')
class Collections(Extractor):
	@fig.silent_config_args('zotero')
	def __init__(self, zotero, item_key='name', path_delimiter=None, **kwargs):
		super().__init__(**kwargs)
		self._item_key = item_key
		self._path_delimiter = path_delimiter
		# zot = zotero#A.pull('zotero', silent=True)
		self.raw_collections = zotero.all_collections()
		self.collections = {c['key']: c for c in self.raw_collections}
		for collection in self.raw_collections:
			collection['path'] = self._collection_path(collection, self.collections)
			if self._path_delimiter is not None:
				collection['data']['path'] = self._path_delimiter.join(collection['path'])
	
	def _collection_path(self, collection, collections):
		if collection['data']['parentCollection'] in collections:
			return self._collection_path(collections[collection['data']['parentCollection']], collections) \
			       + [collection['data']['name']]
		return [collection['data']['name']]
	
	def __call__(self, item, get_children=None):
		return [self.collections[c]['data'][self._item_key]
		        for c in item['data']['collections'] if c in self.collections]


@fig.component('extractor/arxiv')
class Arxiv(Extractor):
	def __init__(self, arxiv_format='https://arxiv.org/abs/{ID}', **kwargs):
		super().__init__(**kwargs)
		self.arxiv_format = arxiv_format
		
	def __call__(self, item, get_children=None):
		ID = item['data'].get('archiveID','')
		if ID.startswith('arXiv:'):
			ID = ID[len('arXiv:'):]
			
			# if 'v' in ID:
			# 	ID = ID[:ID.index('v')]
		
			return self.arxiv_format.format(ID=ID)


class AttachmentExtractor(Extractor, Attachment_Based):
	def __init__(self, *, allow_multiple=False, **kwargs):
		super().__init__(**kwargs)
		self.allow_multiple = allow_multiple
		
	def select(self, children):
		raise NotImplementedError
	
	def __call__(self, item, get_children=None):
		if get_children is None:
			return
		children = get_children()
		selected = self.select(children)
		if self.allow_multiple:
			return selected
		if len(selected) == 0:
			return
		if len(selected) == 1:
			return selected[0]
		raise self.ExtractionError(f'Multiple attachments found: {len(selected)}')
		

class PDF(AttachmentExtractor):
	def select(self, children):
		return [child for child in children if child['data'].get('itemType') == 'attachment'
		        and child['data'].get('linkMode') == 'linked_file'
		        and child['data'].get('contentType') == 'application/pdf']


@fig.component('extractor/pdf/path')
class PDF_Path(PDF):
	def __init__(self, full_path=False, **kwargs):
		super().__init__(**kwargs)
		self.full_path = full_path
	
	def __call__(self, item, get_children=None):
		pdf = super().__call__(item, get_children)
		if pdf is None:
			return
		path = self.fix_path(pdf['data']['path'])
		return str(path) if self.full_path else path.stem


@fig.component('extractor/pdf/link')
class PDF_Link(PDF):
	def __init__(self, *, skip_if_missing=False, **kwargs):
		super().__init__(**kwargs)
		self.skip_if_missing = skip_if_missing
	
	def __call__(self, item, get_children=None):
		pdf = super().__call__(item, get_children)
		if self.skip_if_missing and (pdf is None or len(pdf['data']['url']) == 0):
			raise self.SkipItem('No PDF URL found')
		if pdf is None:
			return
		return pdf['data']['url']


class Wordcloud(AttachmentExtractor):
	def select(self, children):
		return [child for child in children if child['data'].get('title') == 'Wordcloud'
		        and child['data'].get('itemType') == 'attachment'
		        and child['data'].get('linkMode') == 'linked_file'
		        and child['data'].get('contentType') == 'image/jpg']


@fig.component('extractor/wordcloud/link')
class Wordcloud_Link(Wordcloud):
	def __init__(self, skip_if_missing=False, **kwargs):
		super().__init__(**kwargs)
		self.skip_if_missing = skip_if_missing
		
	def __call__(self, item, get_children=None):
		wc = super().__call__(item, get_children)
		if self.skip_if_missing and (wc is None or len(wc['data']['url']) == 0):
			raise self.SkipItem('No Wordcloud URL found')
		if wc is None:
			return
		return wc['data']['url']


@fig.component('extractor/wordcloud/words')
class Wordcloud_Words(Wordcloud):
	def __call__(self, item, get_children=None):
		wc = super().__call__(item, get_children)
		if wc is None:
			return
		return list(wc['data']['note'].replace('ﬁ', 'fi').split(';'))


@fig.component('extractor/semantic-scholar')
class SemanticScholar(AttachmentExtractor):
	def __init__(self, **kwargs):
		super().__init__(allow_multiple=False, **kwargs)
		
	def select(self, children):
		return [child for child in children if child['data'].get('title') == 'Semantic Scholar'
		        and child['data'].get('itemType') == 'attachment'
		        and child['data'].get('linkMode') == 'linked_url']

	def __call__(self, item, get_children=None):
		ss = super().__call__(item, get_children)
		if ss is None:
			return
		return ss['data']['url']


@fig.component('extractor/google-scholar')
class GoogleScholar(AttachmentExtractor):
	def __init__(self, **kwargs):
		super().__init__(allow_multiple=False, **kwargs)
		
	def select(self, children):
		return [child for child in children if child['data'].get('title') == 'Google Scholar'
		        and child['data'].get('itemType') == 'attachment'
		        and child['data'].get('linkMode') == 'linked_url']
	
	def __call__(self, item, get_children=None):
		attachment = super().__call__(item, get_children)
		if attachment is None:
			return
		return attachment['data']['url']


@fig.component('extractor/code-links')
class CodeLinks(AttachmentExtractor):
	def __init__(self, **kwargs):
		super().__init__(allow_multiple=False, **kwargs)
		
	def select(self, children):
		return [child for child in children if child['data'].get('itemType') == 'note'
		        and child['data'].get('note', '').startswith('<p>Code Links')]

	def __call__(self, item, get_children=None):
		cl = super().__call__(item, get_children)
		if cl is None:
			return
		note = cl['data']['note']
		lines = note.split('\n')
		return [line.split('"')[1] for line in lines[1:]]


@fig.modifier('links-to-rich-text')
class LinksToRichText(Extractor):
	def __init__(self, without_domain=True, **kwargs):
		super().__init__(**kwargs)
		self.without_domain = without_domain

	def _remove_domain(self, url):
		return '/'.join(url.split('://')[-1].split('/')[1:])

	def _wrap_link(self, text, url):
		return {'type': 'text', 'text': {'content': text, 'link': {'url': url}},
		        'plain_text': text, 'href': url}
		# return {'type': 'rich_text', 'rich_text': [{}]}

	def _new_line_obj(self):
		return {'type': 'text', 'text': {'content': '\n'}, 'plain_text': '\n'}

	def __call__(self, item, get_children=None):
		
		links = super().__call__(item, get_children)
		
		if links is None or len(links) == 0:
			return
		
		terms = []
		for link in links:
			name = self._remove_domain(link) if self.without_domain else link
			terms.append(self._wrap_link(name, link))
			terms.append(self._new_line_obj())
		terms.pop()
		return {#'type': 'rich_text',
		       'rich_text': terms}


class ExtrationPackager(Extractor):
	def package(self, data):
		raise NotImplementedError
	
	def __call__(self, item, get_children=None):
		data = super().__call__(item, get_children)
		if data is None:
			return
		return self.package(data)
	

@fig.modifier('to-rich-text')
class ToRichText(ExtrationPackager):
	_rich_text_key = 'rich_text'
	def package(self, data):
		data = str(data)
		if len(data):
			return {self._rich_text_key: [{'type': 'text', 'text': {'content': data}}]}


@fig.modifier('to-title')
class ToTitle(ToRichText):
	_rich_text_key = 'title'


@fig.modifier('to-multi-select')
class ToMultiSelect(ExtrationPackager):
	def package(self, data):
		tags = [{'name': tag} for tag in data if len(tag)]
		if len(tags):
			return {'multi_select': tags}


@fig.modifier('to-url')
class ToURL(ExtrationPackager):
	def package(self, data):
		if len(data):
			return {'url': data}


@fig.modifier('to-date')
class ToDate(ExtrationPackager):
	def __init__(self, include_time=True, **kwargs):
		super().__init__(**kwargs)
		self.include_time = include_time
	
	def package(self, data):
		if len(data):
			if isinstance(data, (list, tuple)):
				start, end = data
				if not self.include_time:
					start = start.split('T')[0]
					end = end.split('T')[0]
				return {'date': {'start': start, 'end': end}}
			assert isinstance(data, str), f'Date is not a string: {data}'
			if not self.include_time:
				data = data.split('T')[0]
			return {'date': {'start': data}}


@fig.modifier('to-number')
class ToNumber(ExtrationPackager):
	def package(self, data):
		return {'number': data}


@fig.modifier('to-select')
class ToSelect(ExtrationPackager):
	def __init__(self, select_type='select', **kwargs):
		super().__init__(**kwargs)
		assert select_type in {'select', 'status'}, f'Invalid select_type: {select_type}'
		self.select_type = select_type
	
	def package(self, data):
		if len(data):
			return {self.select_type: {'name': data}}


class Publisher(fig.Configurable):
	@property
	def ident(self):
		raise NotImplementedError
	
	def prepare(self, zot):
		raise NotImplementedError
	
	def process(self, item, get_children=None, manager=None):
		raise NotImplementedError
	
	def publish(self, manager):
		raise NotImplementedError


@fig.component('notion-publisher')
class NotionPublisher(Publisher):
	@fig.silent_config_args('notion_secret')
	def __init__(self, notion_database_id, notion_secret,
	             extractors=None, cover_extractor=None, icon_extractor=None,
	             ignore_failed_extractors=False, filter_extractors=False,
	             notion_link_attachment='Notion',
	             notion_version='2022-06-28',
	             **kwargs):
		super().__init__(**kwargs)
		self.notion_link_attachment = notion_link_attachment
		self.notion_database_id = notion_database_id
		self.notion_parent = {'database_id': self.notion_database_id, 'type': 'database_id'}
		self._notion_header = {
			# 'Content-Type': 'application/json',
			# 'Accept': 'application/json',
			'Notion-Version': notion_version,
			'Authorization': f'Bearer {notion_secret}',
		}
		
		self.timestamp = get_now()
		
		if extractors is None:
			print('WARNING: No extractors specified')
			extractors = {}
		self.extractors: Dict[str,Extractor] = extractors
		# assert '!cover' not in self.extractors and '!icon' not in self.extractors, \
		# 	'!cover and !icon are reserved extractor names, sorry'
		self.cover_extractor = cover_extractor
		self.icon_extractor = icon_extractor
		self.ignore_failed_extractors = ignore_failed_extractors
		self._filter_extractors = filter_extractors
		
		self.publish_todo = []

	_on_notion_brand = 'synced-with-notion'
	
	def prepare(self, zot):
		
		if self._filter_extractors:
			database_url = f"https://api.notion.com/v1/databases/{self.notion_database_id}"
			
			db_info = self.send_request('GET', database_url)
			
			props = db_info.get('properties')
			
			if props is not None:
				bad = []
				for key in self.extractors:
					if key not in props:
						bad.append(key)
				for key in bad:
					del self.extractors[key]
				if len(bad):
					print(f'Removed {len(bad)} extractors {", ".join(bad)} because they were not in the database')

	
	def send_request(self, method, url, data=None, headers=None):
		if headers is None:
			headers = self._notion_header
		else:
			headers = {**headers, **self._notion_header}
		
		resp = requests.request(method.upper(), url, json=data, headers=headers)
		return resp.json()
	
	
	def publish_page(self, pageID=None, properties=None, icon=None, cover=None):
		payload = {}
		if properties is not None:
			payload['properties'] = properties
		if icon is not None:
			payload['icon'] = {'type': 'emoji', 'emoji': icon}
		if cover is not None:
			if isinstance(cover, str):
				cover = {'url': cover}
			payload['cover'] = {'type': 'external', 'external': cover}
		
		if pageID is None:
			payload['parent'] = self.notion_parent
			return self.send_request('POST', 'https://api.notion.com/v1/pages', data=payload)
		return self.send_request('PATCH', f'https://api.notion.com/v1/pages/{pageID}', data=payload)


	def select_notion_attachment(self, children):
		return [child for child in children if child['data'].get('title') == self._attachment_name
		        and child['data'].get('itemType') == 'attachment'
		        and child['data'].get('linkMode') == 'linked_url']
	
	
	def find_notion_attachment(self, item, get_children):
		if any(tag['tag'] == self._on_notion_brand for tag in item['data'].get('tags', [])):
			link_items = self.select_notion_attachment(get_children())
			if len(link_items) > 1:
				raise Exception(f'Found multiple Notion attachments for {item["data"].get("title")}')
			if len(link_items) == 1:
				return link_items[0]
		

	_attachment_name = 'Notion'
	_attachment_note_title = 'Notion Page Info'
	def create_notion_attachment(self, item, fingerprint, notion_response=None, **kwargs):
		url = '' if notion_response is None else notion_response.get('url', '')
		link = create_url(self._attachment_name, url=url, accessDate=self.timestamp,
		                  note=self.notion_attachment_note(fingerprint),
		                  parentItem=item['key'], **kwargs)
		return link
	
	
	def notion_attachment_note(self, fingerprint):
		timestamp = parser.parse(self.timestamp)
		timestamp = timestamp.strftime('%d %b %Y, %H:%M') # '%Y-%m-%d %H:%M:%S'
		
		lines = [self._attachment_note_title,
		         f'Last Synced: {timestamp}',
		         f'Fingerprint (do not change): {fingerprint}']
		return '\n'.join(f'<p>{line}</p>' for line in lines)
	
	
	class PublishTodo:
		def __init__(self, item, data=None, attachment=None):
			self.item = item
			self.attachment = attachment
			self.data = data


	def process(self, item, get_children=None, manager=None):
		# extract data
		try:
			data, errors = self.extract(item, get_children)
		except Extractor.SkipItem as e:
			manager.log_error(e, item=item)
		else:
			for name, error in errors.items():
				manager.log_error(f'{name}: {type(error).__name__}', str(error), item)
			
			# find notion page
			notion_attachment = self.find_notion_attachment(item, get_children)
			
			todo = self.PublishTodo(item, data, notion_attachment)
			
			self.publish_todo.append(todo)
			return todo

	
	def fingerprint(self, props):
		obj = json.dumps(props, sort_keys=True, indent=4)
		return md5(obj)


	def extract(self, item, get_children):
		errors = {}
		props = {}
		for name, extractor in self.extractors.items():
			try:
				val = extractor(item, get_children)
				if val is not None:
					props[name] = val
			except Extractor.ExtractionError as e:
				errors[name] = e
				if not self.ignore_failed_extractors:
					raise e
		
		data = {'properties': props}
		
		if self.icon_extractor is not None:
			try:
				icon = self.icon_extractor(item, get_children)
			except Extractor.ExtractionError as e:
				errors['[page icon]'] = e
				if not self.ignore_failed_extractors:
					raise e
			else:
				data['icon'] = icon

		if self.cover_extractor is not None:
			try:
				cover = self.cover_extractor(item, get_children)
			except Extractor.ExtractionError as e:
				errors['[page cover]'] = e
				if not self.ignore_failed_extractors:
					raise e
			else:
				data['cover'] = cover
		
		return data, errors
	
	
	def complete_todo(self, todo, manager):
		attachment = todo.attachment
		fingerprint = self.fingerprint(todo.data)
		
		pageID = None
		if attachment is not None:
			pageID = attachment['data']['url'].split('-')[-1]
			note = attachment['data'].get('note')
			if note is not None:
				prev_fingerprint = re.search(r'Fingerprint \(do not change\): (.*)', note)
			
				if prev_fingerprint is not None and prev_fingerprint.group(1) == fingerprint:
					manager.add_failed(todo.item, msg='Fingerprints match - no update necessary')
					return
				
			attachment['data']['note'] = self.notion_attachment_note(fingerprint)
			manager.add_update(attachment, msg='Updated Notion attachment')
		
		if manager.is_real_run:
			resp = self.publish_page(pageID, **todo.data)
		else:
			resp = None
			verb = 'update' if pageID is not None else 'create'
			manager.log(f'Would {verb} notion page for {todo.item["data"].get("title")}')
		
		if resp is not None and resp.get('status', 200) != 200:
			manager.log_error(f'{resp.get("status")}: {resp.get("code")}', resp.get('message'), item=todo.item)
		else:
			if attachment is None:
				attachment = self.create_notion_attachment(todo.item, fingerprint, resp)
				manager.add_new(attachment, msg='Created Notion attachment')
			
			if not any(tag['tag'] == self._on_notion_brand for tag in todo.item['data']['tags']):
				todo.item['data']['tags'].append({'tag': self._on_notion_brand, 'type': 1})
				manager.add_update(todo.item, msg=f'Added {self._on_notion_brand} tag')
		
			verb = 'Updated' if pageID is not None else 'Created'
			if manager.is_real_run:
				manager.log(f'{verb} notion page for {todo.item["data"].get("title")}')
		return resp
		
		
	def publish(self, manager: Script_Manager):
		for todo in self.publish_todo:
			self.complete_todo(todo, manager)
		if manager.is_real_run:
			self.publish_todo.clear()



@fig.script('sync-notion', description='Sync Zotero items with a Notion database.')
def sync_notion(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar_desc', 'Sync with Notion', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	publisher_ident = A.pull('publisher_ident', 'default')

	A.push('update-existing', False, overwrite=False, silent=True)
	A.push('ignore-brand', '<>update_existing', overwrite=False, silent=True)
	A.push('brand_tag', f'notion:{publisher_ident}', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	publisher: Publisher = A.pull('publisher')

	zot_query = A.pull('zotero-query', {})
	
	collection_name = A.pull('collection', None)
	if collection_name is not None:
		res = zot.find_collection(q=collection_name)
		if len(res) == 0:
			raise Exception(f'Collection {collection_name} not found')
		elif len(res) > 1:
			raise Exception(f'Multiple collections found for {collection_name}')
		zot_query['collection'] = res[0]['key']
	
	manager.preamble(zot=zot)
	publisher.prepare(zot)
	
	todo = zot.top(**zot_query)
	# if A.pull('skip-computer-programs', True):
	# 	todo = [item for item in todo if item.get('data', {}).get('itemType') not in {'computerProgram', ''}]
	manager.log(f'Found {len(todo)} new items to process.')
	
	for item in manager.iterate(todo):
		@lru_cache
		def get_children(**kwargs):
			return zot.children(item['key'], **kwargs)
		try:
			publisher.process(item, get_children=get_children, manager=manager)
		except Exception as e:
			manager.log_error(e, item=item)
			# raise
			
	publisher.publish(manager)
	return manager.finish()
















