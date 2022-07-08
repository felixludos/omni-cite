import omnifig as fig
import msal
import webbrowser
import requests
import pyperclip
from msal import PublicClientApplication
from pyzotero import zotero

# from .auth import get_zotero


@fig.Component('zotero')
class ZoteroProcess(fig.Configurable):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.zot = self._load_zotero(A)
		exclusion_tags = A.pull('exclusion-tags', None)
		if exclusion_tags is not None:
			if isinstance(exclusion_tags, str):
				exclusion_tags = exclusion_tags.split(' AND ')
			exclusion_tags = [f'-{tag}' for tag in exclusion_tags]
		self.exclusion_tags = exclusion_tags
		self._full_top = None
	
	_zotero_obj = None
	
	@classmethod
	def _load_zotero(cls, A):
		global _zotero_obj
		if _zotero_obj is None:
			_zotero_obj = zotero.Zotero(A.pull('zotero-library', silent=True), A.pull('zotero-library-type', silent=True),
			                            A.pull('zotero-api-key', silent=True))
		return _zotero_obj
	
	_brand_tag_prefix = 'zz:omni-cite:'
	
	def update_items(self, items, brand_tag=None, **kwargs):
		if brand_tag is not None:
			brand_tag = f'{self._brand_tag_prefix}{brand_tag}'
			for item in items:
				if brand_tag not in {tag['tag'] for tag in item['data']['tags']}:  # add missing branding
					item['data']['tags'].append({'tag': brand_tag, 'type': 1})
		return self.zot.update_items(items, **kwargs)
	
	def create_items(self, items, **kwargs):
		return self.zot.create_items(items, **kwargs)
	
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



@fig.Component('onedrive-auth')
class OneDriveProcess(fig.Configurable):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		
		self.auto_copy = A.pull('auto-copy', True)
		self.auto_open_browser = A.pull('auto-open-browser', True)
		
		self.app_id = A.pull('graph-app-id', silent=True)
		if self._onedrive_app is None:
			self._onedrive_app = PublicClientApplication(self.app_id, authority=self._authority_url)
		
		self.scopes = list(A.pull('graph-scopes', []))
		
		if self._onedrive_flow is None:
			self._onedrive_flow = self._onedrive_app.initiate_device_flow(scopes=self.scopes)
		
	_authority_url = 'https://login.microsoftonline.com/consumers'
	
	_onedrive_app = None
	_onedrive_flow = None
	_onedrive_header = None
	
	def authorize(self):
		if self._onedrive_header is None:
			print(self._onedrive_flow['message'])
			if self.auto_copy:
				pyperclip.copy(self._onedrive_flow['user_code'])
			if self.auto_open_browser:
				webbrowser.open(self._onedrive_flow['verification_uri'])
			
			input('(code copied!) Press enter to continue...')
			
			result = self._onedrive_app.acquire_token_by_device_flow(self._onedrive_flow)
			access_token_id = result['access_token']
			self._onedrive_header = {'Authorization': f'Bearer {access_token_id}'}
			print('Success!')
		# return self._onedrive_header
	
	def is_expired(self):
		return self._onedrive_header is None
	
	endpoint = 'https://graph.microsoft.com/v1.0/me'
	
	def list_dir(self, path):
		if self.is_expired():
			self.authorize()
		
		# response = get_url(endpoint + f'/drive/root:/{str(connection)}/:/children', headers=_header)
		response = requests.get(self.endpoint + f'/drive/root:/{str(path)}/:/children', headers=self._onedrive_header)
		out = response.json()
		return out['value']
		# file_id_table = {item['name']: item['id'] for item in out['value']}
	
	@classmethod
	def share_links(cls, file_ids, mode='view'):
		def generate_request(url, method='GET', **kwargs):
			return {'method': method.upper(), 'url': url, **kwargs}
			
		permissions = {"type": {'download': 'embed'}.get(mode, mode), "scope": "anonymous"}
		reqs = [generate_request(f'/me/drive/items/{item_id}/createLink', method='POST',
		                         body=permissions,
		                         headers={'content-type': 'application/json'})
		        for item_id in file_ids]
		for i, req in enumerate(reqs):
			req['id'] = str(i + 1)
		
		body = {'requests': reqs}
		
		resp = requests.post('https://graph.microsoft.com/v1.0/$batch', json=body,
		                     headers={'content-type': 'application/json', **cls._onedrive_header})
		out = resp.json()
		links = [r['body']['link']['webUrl'] for r in sorted(out['responses'], key=lambda r: r['id'])]
		return links
	

# _onedrive = None
# def get_onedrive(A):
# 	global _onedrive
#
# 	if _onedrive is None:
#
# 		app_id = A.pull('graph-app-id', silent=True)
# 		authority_url = 'https://login.microsoftonline.com/consumers'
#
# 		app = PublicClientApplication(app_id, authority=authority_url)
#
# 		flow = app.initiate_device_flow(scopes=list(A.pull('graph-scopes', [])))
#
# 		print(flow['message'])
# 		pyperclip.copy(flow['user_code'])
# 		webbrowser.open(flow['verification_uri'])
#
# 		input('(code copied!) Press enter to continue...')
#
# 		result = app.acquire_token_by_device_flow(flow)
# 		access_token_id = result['access_token']
# 		_onedrive = {'Authorization': f'Bearer {access_token_id}'}
# 		print('Success!')
#
# 	return _onedrive

