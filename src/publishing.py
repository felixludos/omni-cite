from pathlib import Path
from typing import Union, List, Dict, Callable, Tuple, Optional
from functools import lru_cache
import omnifig as fig
import requests

from .util import Script_Manager
from .auth import ZoteroProcess


class Extractor(fig.Configurable):
	class ExtractionError(Exception):
		pass
	
	def __call__(self, item, get_children=None):
		raise NotImplementedError
		

class SimpleExtractor(Extractor):
	_data_key = None
	def __call__(self, item, get_children=None):
		return item['data'].get(self._data_key, '')


for _property in ['title', 'abstract', 'url', 'dateAdded', 'libraryCatalog',
                  'accessDate', 'itemType', 'key', 'DOI', 'language']:
	fig.Component(f'extractor/{_property}')(
		type(f'{_property.capitalize()}_SimpleExtractor', (SimpleExtractor,), {'_data_key': _property}))
del _property


@fig.Component('extractor/nickname')
class NickName(Extractor):
	def __call__(self, item, get_children=None):
		if 'shortTitle' in item['data'] and len(item['data']['shortTitle']):
			return item['data']['shortTitle']
		return item['data']['title']


@fig.Component('extractor/date')
class Date(Extractor):
	def __call__(self, item, get_children=None):
		raise NotImplementedError
		pass
		if 'shortTitle' in item['data'] and len(item['data']['shortTitle']):
			return item['data']['shortTitle']
		return item['data']['title']


@fig.Component('extractor/zotero-link')
class ZoteroLink(Extractor):
	def __call__(self, item, get_children=None):
		return item['links'].get('self', {}).get('alternate', {}).get('href')


@fig.Component('extractor/creators')
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

		return '\n'.join(names)


@fig.Component('extractor/tags')
class Tags(Extractor):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.keep_auto_tags = A.pull('keep-auto-tags', False)
	
	def __call__(self, item, get_children=None):
		return [tag['tag'] for tag in item['data']['tags'] if tag.get('type', 0) == 0 or self.keep_auto_tags]
		tags = []
		for tag in item['data']['tags']:
			if tag.get('type', 0) == 0 or self.keep_auto_tags:
				tags.append(tag['tag'])
		return tags


@fig.Component('extractor/collections')
class Collections(Extractor):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		zot = A.pull('zotero', silent=True)
		self.raw_collections = zot.all_collections()
		self.collections = {c['key']: c for c in self.raw_collections}
	
	def __call__(self, item, get_children=None):
		return [self.collections[c] for c in item['data']['collections'] if c in self.collections]


@fig.Component('extractor/arxiv')
class Arxiv(Extractor):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.arxiv_format = A.pull('arxiv-format', 'https://arxiv.org/abs/{ID}')
		
	def __call__(self, item, get_children=None):
		ID = item['data'].get('archiveID','')
		if ID.startswith('arXiv:'):
			ID = ID[len('arXiv:'):]
			
			# if 'v' in ID:
			# 	ID = ID[:ID.index('v')]
		
			return self.arxiv_format.format(ID=ID)


class PDF(Extractor):
	def __init__(self, A, select_single=None, **kwargs):
		if select_single is None:
			select_single = A.pull('select-single', True)
		super().__init__(A, **kwargs)
		self.select_single = select_single

	def select(self, children):
		return [child for child in children if child['data'].get('itemType') == 'attachment'
		        and child['data'].get('linkMode') == 'linked_file'
		        and child['data'].get('contentType') == 'application/pdf']

	def __call__(self, item, get_children=None):
		children = get_children()
		pdfs = self.select(children)
		if self.select_single:
			if len(pdfs) == 0:
				return
			if len(pdfs) == 1:
				return pdfs[0]
			raise self.ExtractionError(f'Multiple PDFs found: {len(pdfs)}')
		return pdfs


@fig.Component('extractor/pdf/path')
class PDF_Path(PDF):
	def __init__(self, A, full_path=None, select_single=True, **kwargs):
		if full_path is None:
			full_path = A.pull('full-path', False)
		super().__init__(A, select_single=select_single, **kwargs)
		self.full_path = full_path
	
	def __call__(self, item, get_children=None):
		pdf = super().__call__(item, get_children)
		if pdf is None:
			return
		path = Path(pdf['data']['path'])
		return str(path) if self.full_path else path.stem


@fig.Component('extractor/pdf/link')
class PDF_Link(PDF):
	def __init__(self, A, select_single=True, **kwargs):
		super().__init__(A, select_single=select_single, **kwargs)
	
	def __call__(self, item, get_children=None):
		pdf = super().__call__(item, get_children)
		if pdf is None:
			return
		return pdf['data']['url']


class Wordcloud(Extractor):
	def select(self, children):
		return [child for child in children if child['data'].get('title') == 'Wordcloud'
		        and child['data'].get('itemType') == 'attachment'
		        and child['data'].get('linkMode') == 'linked_file'
		        and child['data'].get('contentType') == 'image/jpg']

	def __call__(self, item, get_children=None):
		children = get_children()
		wcs = self.select(children)
		if len(wcs) == 0:
			return
		if len(wcs) == 1:
			return wcs[0]
		raise self.ExtractionError(f'Multiple Wordclouds found: {len(wcs)}')


@fig.Component('extractor/wordcloud/link')
class Wordcloud_Link(Wordcloud):
	def __call__(self, item, get_children=None):
		wc = super().__call__(item, get_children)
		if wc is None:
			return
		return wc['data']['url']


@fig.Component('extractor/wordcloud/words')
class Wordcloud_Words(Wordcloud):
	def __call__(self, item, get_children=None):
		wc = super().__call__(item, get_children)
		if wc is None:
			return
		return list(wc['data']['note'].replace('ﬁ', 'fi').split(';'))


@fig.Component('extractor/semantic-scholar')
class SemanticScholar(Extractor):
	def select(self, children):
		return [child for child in children if child['data'].get('title') == 'Semantic Scholar'
		        and child['data'].get('itemType') == 'attachment'
		        and child['data'].get('linkMode') == 'linked_url']

	def __call__(self, item, get_children=None):
		children = get_children()
		sss = self.select(children)
		if len(sss) == 0:
			return
		if len(sss) == 1:
			return sss[0]['data']['url']
		raise self.ExtractionError(f'Multiple Semantic Scholar links found: {len(sss)}')


@fig.Component('extractor/code-links')
class CodeLinks(Extractor):
	def select(self, children):
		return [child for child in children if child['data'].get('itemType') == 'note'
		        and child['data'].get('note', '').startswith('<p>Code Links')]

	def __call__(self, item, get_children=None):
		children = get_children()
		notes = self.select(children)
		if len(notes) == 0:
			return
		if len(notes) == 1:
			note = notes[0]['data']['note']
			links = [line.split('"')[1] for line in note.split('\n')[1:]]
			return links
		raise self.ExtractionError(f'Multiple Code link notes found: {len(notes)}')


@fig.AutoModifier('links-to-rich-text')
class LinksToRichText(Extractor):
	def __init__(self, A, without_domain=None, **kwargs):
		if without_domain is None:
			without_domain = A.pull('without-domain', True)
		super().__init__(A, **kwargs)
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
		return {'type': 'rich_text', 'rich_text': terms}
	


class Publisher(fig.Configurable):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.notion_link_attachment = A.pull('notion-link-attachment', 'Notion')
		self.notion_database_id = A.pull('notion-database-id')
		self._notion_header = {
			'Content-Type': 'application/json',
			'Accept': 'application/json',
			'Notion-Version': A.pull('notion-version', '2022-06-28'),
			'Authorization': f'Bearer {A.pull("notion-secret", silent=True)}',
		}
		
		self.extractors: Dict[Extractor] = A.pull('extractors', {})
		self.ignore_failed_extractors = A.pull('ignore-failed-extractors', False)


	@property
	def ident(self):
		return 'default'

	
	def send_request(self, method, url, data=None, headers=None):
		if headers is None:
			headers = self._notion_header
		else:
			headers = {**headers, **self._notion_header}
		
		resp = requests.request(method.upper(), url, json=data, headers=headers)
		return resp
	
	
	def publish_page(self, pageID=None, properties=None, icon=None, cover=None):
		payload = {}
		if properties is not None:
			payload['properties'] = properties
		if icon is not None:
			payload['icon'] = {'type': 'emoji', 'emoji': icon}
		if cover is not None:
			payload['cover'] = {'type': 'external', 'external': {'url': cover}}
		
		if pageID is None:
			return self.send_request('POST', 'https://api.notion.com/v1/pages', data=payload)
		payload['parent'] = self.notion_database_id
		return self.send_request('PATCH', f'https://api.notion.com/v1/pages/{pageID}', data=payload)


	def extract(self, item, get_children, manager):
		# manage Notion attachment (and check if a page already exists)
		
		# extract data from item
		data = {}
		for name, extractor in self.extractors.items():
			try:
				data[name] = extractor(item, get_children)
			except Extractor.ExtractionError:
				if not self.ignore_failed_extractors:
					raise
		# data = {name: extractor(item, get_children) for name, extractor in self.extractors.items()}
		return data
	
	
	def publish(self):
		# Notion API request/s
		pass


@fig.Script('sync-notion', description='Sync Zotero items with Notion database.')
def sync_notion(A):
	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar-desc', 'Sync with Notion', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	publisher: Publisher = A.pull('publisher')
	
	A.push('brand-tag', f'notion:{publisher.ident}', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	zot_query = A.pull('zotero-query', {})
	
	manager.preamble(zot=zot)
	
	todo = zot.top(**zot_query)
	manager.log(f'Found {len(todo)} new items to process.')
	
	for item in manager.iterate(todo):
		@lru_cache
		def get_children(**kwargs):
			return zot.children(item['key'], **kwargs)
		try:
			rawinfo = publisher.extract(item, get_children=get_children, manager=manager)
		except Exception as e:
			manager.log_error(e, item=item)
		else:
			if rawinfo is None:
				manager.add_failed(item)
			else:
				manager.add_update(item, msg=f'Syncing {len(rawinfo)} items: {", ".join(rawinfo)}')
	
	if manager.is_real_run:
		publisher.publish()
	return manager.finish()
















