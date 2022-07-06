
import omnifig as fig

from .auth import get_zotero


@fig.Component('zotero')
class ZoteroProcess(fig.Configurable):
	def __int__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.zot = get_zotero(A)
		exclusion_tags = A.pull('exclusion-tags', None)
		if exclusion_tags is not None:
			if isinstance(exclusion_tags, str):
				exclusion_tags = exclusion_tags.split(' AND ')
			exclusion_tags = [f'-{tag}' for tag in exclusion_tags]
		self.exclusion_tags = exclusion_tags
		self._full_top = None

	
	_brand_tag_prefix = 'zz:script:'
	
	def update_items(self, items, brand_tag=None, **kwargs):
		if brand_tag is not None:
			brand_tag = f'{self._brand_tag_prefix}{brand_tag}'
			for item in items:
				if brand_tag not in {tag['tag'] for tag in item['data']['tags']}: # add missing branding
					item['data']['tags'].append({'tag': brand_tag, 'type': 1})
		return self.zot.update_items(items, **kwargs)


	def top(self, brand_tag=None, **kwargs):
		if len(kwargs) or brand_tag is not None:
			return self.collect(top=True, brand_tag=brand_tag, **kwargs)
		if self._full_top is None:
			self._full_top = self.collect(top=True)
		return self._full_top
	
	
	def children(self, itemID, **kwargs):
		return self.zot.children(itemID, **kwargs)
	
	
	def collect(self, q=None, top=False, brand_tag=None, get_all=True, itemType=None, tags=None, **kwargs):
		if len(self.exclusion_tags) or brand_tag is not None:
			if tags is None:
				tags = self.exclusion_tags
			elif isinstance(tags, str):
				tags = [tags, *self.exclusion_tags]
			else:
				tags = [*tags, *self.exclusion_tags]
			if brand_tag is not None:
				tags = [*tags, f'-{self._brand_tag_prefix}{brand_tag}']
		
		# TODO: handle pagination
		
		return (self.zot.top if top else self.zot.items)(q=q, itemType=itemType, tags=tags, **kwargs)








