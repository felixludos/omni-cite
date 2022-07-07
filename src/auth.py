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
		self.brand_tag = A.pull('brand-tag', None)
		self.ignore_brand_tag = A.pull('ignore-brand', False)
		exclusion_tags = A.pull('exclusion-tags', [])
		if exclusion_tags is not None:
			if isinstance(exclusion_tags, str):
				exclusion_tags = exclusion_tags.split(' AND ')
			exclusion_tags = [f'-{tag}' for tag in exclusion_tags]
		self.exclusion_tags = exclusion_tags
		self._full_top = None
	
	_zotero_obj = None
	
	@classmethod
	def _load_zotero(cls, A):
		if cls._zotero_obj is None:
			cls._zotero_obj = zotero.Zotero(A.pull('zotero-library', silent=True), A.pull('zotero-library-type', silent=True),
			                            A.pull('zotero-api-key', silent=True))
		return cls._zotero_obj
	
	_brand_tag_prefix = 'zz:omni-cite:'
	
	def brand_items(self, brand_tag, items):
		brand = f'{self._brand_tag_prefix}{brand_tag}'
		for item in items:
			if brand not in {tag['tag'] for tag in item.get('data', item)['tags']}:
				item.get('data', item)['tags'].append({'tag': brand_tag, 'type': 1})
	
	def update_items(self, items, use_brand_tag=True, brand_tag=None, **kwargs):
		if use_brand_tag and brand_tag is None:
			brand_tag = self.brand_tag
		if brand_tag is not None:
			self.brand_items(brand_tag, items)
		return self.zot.update_items(items, **kwargs)
	
	def create_items(self, items, use_brand_tag=True, brand_tag=None, **kwargs):
		if use_brand_tag and brand_tag is None:
			brand_tag = self.brand_tag
		if brand_tag is not None:
			self.brand_items(brand_tag, items)
		return self.zot.create_items(items, **kwargs)
	
	def top(self, brand_tag=None, **kwargs):
		if len(kwargs) or brand_tag is not None:
			return self.collect(top=True, brand_tag=brand_tag, **kwargs)
		if self._full_top is None:
			self._full_top = self.collect(top=True)
		return self._full_top
	
	def children(self, itemID, **kwargs):
		return self.zot.children(itemID, **kwargs)
	
	def collect(self, q=None, top=False, brand_tag=None, ignore_brand=None,
	            get_all=True, itemType=None, tag=None, **kwargs):
		if brand_tag is None:
			brand_tag = self.brand_tag
		if len(self.exclusion_tags) or brand_tag is not None:
			if tag is None:
				tag = self.exclusion_tags
			elif isinstance(tag, str):
				tag = [tag, *self.exclusion_tags]
			else:
				tag = [*tag, *self.exclusion_tags]
			if ignore_brand is None:
				ignore_brand = self.ignore_brand_tag
			if brand_tag is not None and not ignore_brand:
				tag = [*tag, f'-{self._brand_tag_prefix}{brand_tag}']
		
		# TODO: handle pagination
		
		return (self.zot.top if top else self.zot.items)(q=q, itemType=itemType, tag=tag, **kwargs)



@fig.Component('onedrive-auth')
class OneDriveProcess(fig.Configurable):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		
		self.auto_copy = A.pull('auto-copy', True)
		self.auto_open_browser = A.pull('auto-open-browser', True)
		
		self.app_id = A.pull('graph-app-id', silent=True)
		if self._onedrive_app is None:
			self.__class__._onedrive_app = PublicClientApplication(self.app_id, authority=self._authority_url)
		
		if self._onedrive_header is None:
			self.__class__._onedrive_header = A.pull('_header', None, silent=True)
		
		self.scopes = list(A.pull('graph-scopes', []))
		
		
	_authority_url = 'https://login.microsoftonline.com/consumers'
	
	_onedrive_app = None
	_onedrive_flow = None
	_onedrive_header = None
	
	def authorize(self): # TODO: setup (auto) refresh tokens
		if self._onedrive_header is None:
			self._onedrive_flow = self._onedrive_app.initiate_device_flow(scopes=self.scopes)
			print('OneDrive:', self._onedrive_flow['message'])
			if self.auto_copy:
				pyperclip.copy(self._onedrive_flow['user_code'])
				print('(code copied to clipboard!) Waiting for you to complete the sign in...')
			
			if self.auto_open_browser:
				webbrowser.open(self._onedrive_flow['verification_uri'])
			
			# input('(code copied!) Press enter to continue...')
			
			result = self._onedrive_app.acquire_token_by_device_flow(self._onedrive_flow)
			access_token_id = result['access_token']
			self.__class__._onedrive_header = {'Authorization': f'Bearer {access_token_id}'}
		print('OneDrive Authorization Success!')
		# return self._onedrive_header
	
	def is_expired(self):
		return self._onedrive_header is None
	
	endpoint = 'https://graph.microsoft.com/v1.0/me'
	
	def send_request(self, send_fn, retry=1):
		
		if self.is_expired():
			self.authorize()
		
		response = send_fn(self._onedrive_header)
		out = response.json()
		
		if 'error' in out and retry > 0:
			if out['error']['code'] == 'InvalidAuthenticationToken':
				print('Token Expired, re-authorizing now.')
				self.__class__._onedrive_header = None
				return self.send_request(send_fn, retry-1)
		return out
		
	
	def list_dir(self, path):
		if self.is_expired():
			self.authorize()
		
		out = self.send_request(lambda header:
		                        requests.get(self.endpoint + f'/drive/root:/{str(path)}/:/children',
		                                             headers=header))
		return out['value']
		
		
	def get_meta(self, paths):
		reqs = [self.generate_request(f'/me/drive/root:/{path}') for path in paths]
		out = self.batch_send(reqs)
		return out
	
	
	def share_files(self, paths, mode='view'):
		reqs = [self.generate_request(f'/me/drive/root:/{path}:/createLink',
		                                  method='POST', headers={'content-type': 'application/json'},
		                                  body={"type": {'download': 'embed'}.get(mode, mode),
		                                        "scope": "anonymous"},)
		            for path in paths]
		
		out = self.batch_send(reqs)
		
		if mode == 'download' and isinstance(out, list):
			for r in out:
				link = r.get('body', {}).get('link', {})
				if 'webUrl' in link:
					link['webUrl'] = link['webUrl'].replace('embed', 'download')
		return out
	
	
	@staticmethod
	def generate_request(url, method='GET', **kwargs):
		return {'method': method.upper(), 'url': url, **kwargs}
	
	
	def batch_send(self, reqs):
		for i, req in enumerate(reqs):
			req['id'] = str(i + 1)
		out = self.send_request(lambda header:
		                         requests.post('https://graph.microsoft.com/v1.0/$batch',
		                                       json={'requests': reqs},
		                                       headers={'content-type': 'application/json', **header}))
		if 'responses' not in out:
			return out
		return sorted(out['responses'], key=lambda r: r['id'])
	
	# def share_links(self, file_ids, mode='view'):
	# 	if self.is_expired():
	# 		self.authorize()
	#
	# 	permissions = {"type": {'download': 'embed'}.get(mode, mode), "scope": "anonymous"}
	# 	reqs = [self.generate_request(f'/me/drive/items/{item_id}/createLink', method='POST',
	# 	                         body=permissions,
	# 	                         headers={'content-type': 'application/json'})
	# 	        for item_id in file_ids]
	# 	for i, req in enumerate(reqs):
	# 		req['id'] = str(i + 1)
	#
	# 	body = {'requests': reqs}
	#
	# 	out = self.send_request(lambda header:
	# 	                        requests.post('https://graph.microsoft.com/v1.0/$batch', json=body,
	# 	                     headers={'content-type': 'application/json', **header}))
	# 	links = [r['body']['link']['webUrl'] for r in sorted(out['responses'], key=lambda r: r['id'])]
	# 	return links

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

