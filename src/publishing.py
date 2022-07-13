from typing import Union, List, Dict, Callable, Tuple, Optional
from functools import lru_cache
import omnifig as fig

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


for _property in ['title', 'abstract', 'url', 'date', 'dateAdded', 'libraryCatalog',
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


# # @fig.Component('extractor/pdf')
# class PDF(Extractor):
# 	# def __init__(self, A, **kwargs):
# 	# 	super().__init__(A, **kwargs)
# 	# 	self.arxiv_format = A.pull('arxiv-format', 'https://arxiv.org/abs/{ID}')
#
#
# 	def __call__(self, item, get_children=None):
# 		children = get_children()




class Publisher(fig.Configurable):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.notion_link_attachment = A.pull('notion-link-attachment', 'Notion')
		
		self.extractors: Dict[Extractor] = A.pull('extractors', {})


	@property
	def ident(self):
		return 'default'


	def extract(self, item, get_children, manager):
		# manage Notion attachment (and check if a page already exists)
		
		# extract data from item
		data = {name: extractor(item, get_children) for name, extractor in self.extractors.items()}
		
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
















