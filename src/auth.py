import os, shutil
from pathlib import Path
import json
import omnifig as fig
import msal
import webbrowser
from datetime import datetime, timedelta
import time
import requests
import pyperclip
from msal import PublicClientApplication
from pyzotero import zotero


@fig.component('zotero')
class ZoteroProcess: # should be configurable
	def __init__(self, A, **kwargs):
		super().__init__(**kwargs)
		self.zot = self._load_zotero(A)
		self.brand_tag = A.pull('brand_tag', None)
		self.limit = A.pull('limit', None)
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
			cls._zotero_obj = zotero.Zotero(A.pull('zotero-library', silent=True), A.pull('zotero_library_type', silent=True),
			                            A.pull('zotero-api-key', silent=True))
		return cls._zotero_obj
	
	_brand_tag_prefix = 'omnicite:'
	
	def brand_items(self, brand_tag, items):
		brand = f'{self._brand_tag_prefix}{brand_tag}'
		for item in items:
			if brand not in {tag['tag'] for tag in item.get('data', item)['tags']}:
				item.get('data', item)['tags'].append({'tag': brand, 'type': 1})
	
	def update_items(self, items, use_brand_tag=True, brand_tag=None, **kwargs):
		if use_brand_tag and brand_tag is None:
			brand_tag = self.brand_tag
		if brand_tag is not None:
			self.brand_items(brand_tag, items)
		if len(items) > 50:
			batches = [items[i:i+50] for i in range(0, len(items), 50)]
			outs = []
			for batch in batches:
				out = self.zot.update_items(batch, **kwargs)
				outs.append(out)
			return all(outs)
		return self.zot.update_items(items, **kwargs)
	
	def create_items(self, items, use_brand_tag=True, brand_tag=None, **kwargs):
		if use_brand_tag and brand_tag is None:
			brand_tag = self.brand_tag
		if brand_tag is not None:
			self.brand_items(brand_tag, items)
		if len(items) > 50:
			batches = [items[i:i+50] for i in range(0, len(items), 50)]
			outs = []
			for batch in batches:
				out = self.zot.create_items(batch, **kwargs)
				outs.append(out)
			total = {}
			for i, out in enumerate(outs[1:]):
				for k, vs in out.items():
					if k not in total:
						total[k] = {}
					total[k].update({str(int(rid) + i*50): v for rid, v in vs.items()})
			return total
		return self.zot.create_items(items, **kwargs)
	
	def top(self, brand_tag=None, top=True, **kwargs):
		if len(kwargs) or brand_tag is not None:
			return self.collect(top=top, brand_tag=brand_tag, **kwargs)
		if self._full_top is None:
			self._full_top = self.collect(top=True)
		return self._full_top
	
	def children(self, itemID, **kwargs):
		return self.zot.children(itemID, **kwargs)
	
	def collect(self, q=None, top=False, collection=None, brand_tag=None, ignore_brand=None,
	            limit=None, itemType=None, tag=None, **kwargs):
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
		
		if limit is None:
			limit = self.limit
		
		if q is not None:
			kwargs['q'] = q
		if itemType is not None:
			kwargs['itemType'] = itemType
		if tag is not None:
			kwargs['tag'] = tag
		if limit is not None:
			kwargs['limit'] = limit
		
		if collection is not None:
			collect_fn = self.zot.collection_items_top if top else self.zot.collection_items
			return collect_fn(collection, **kwargs)
		collect_fn = self.zot.top if top else self.zot.items
		return collect_fn(**kwargs)

	def delete_items(self, items):
		return [self.zot.delete_item(item) for item in items]
		
	def find_collection(self, **kwargs):
		return self.zot.collections(**kwargs)

	def get_collection(self, collectionID, **kwargs):
		return self.zot.collection(collectionID, **kwargs)

	def all_collections(self, **kwargs):
		return self.zot.all_collections(**kwargs)


@fig.component('onedrive-auth')
class OneDriveProcess(fig.Configurable):
	@fig.silent_config_args('graph_app_id', '_header')
	def __init__(self, graph_app_id, _header=None, graph_scopes=(),
	             auto_copy=True, auto_open_browser=True, onedrive_info_path='onedrive-info.json',
	             
	             **kwargs):
		super().__init__(**kwargs)
		
		self.auto_copy = auto_copy
		self.auto_open_browser = auto_open_browser
		self.storage_path = onedrive_info_path
		if self.storage_path is not None:
			self.storage_path = Path(self.storage_path)
		
		self.app_id = graph_app_id
		if self._onedrive_app is None:
			self.__class__._onedrive_app = PublicClientApplication(self.app_id, authority=self._authority_url)
		
		if self._onedrive_header is None:
			self.__class__._onedrive_header = _header
		
		self.scopes = list(graph_scopes)
		
		
	_authority_url = 'https://login.microsoftonline.com/consumers'
	
	_onedrive_app = None
	_onedrive_flow = None
	_onedrive_header = None
	
	def authorize(self): # TODO: setup (auto) refresh tokens
		if self._onedrive_header is None:
			new = True
			if self.storage_path is not None and self.storage_path.exists():
				with self.storage_path.open('r') as f:
					result = json.load(f)
				new = False
				print('Using existing onedrive auth info (', self.storage_path, ')')
			else:
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
			
			if self.storage_path is not None and new:
				with self.storage_path.open('w') as f:
					json.dump(result, f)
				print(f'OneDrive: Saved token to {self.storage_path}')
			
		print('OneDrive Authorization Success!')
		# return self._onedrive_header
	
	def is_expired(self):
		return self._onedrive_header is None
	
	endpoint = 'https://graph.microsoft.com/v1.0/me'
	
	def send_request(self, send_fn, retry=1, auto_wait=False):
		if self.is_expired():
			self.authorize()
		
		response = send_fn(self._onedrive_header)
		out = response.json()
		
		if 'error' in out and retry > 0:
			if out['error']['code'] == 'InvalidAuthenticationToken':
				print('Token Expired, re-authorizing now.')
				self.__class__._onedrive_header = None
				if self.storage_path is not None and self.storage_path.exists():
					os.remove(str(self.storage_path))
				return self.send_request(send_fn, retry-1, auto_wait=auto_wait)
		
		if retry > 0 and auto_wait and out.get('responses', [{}])[0].get('status') == 429:
			if 'responses' in out:
				resp = out['responses'][0]
				wait_time = resp.get('headers', {}).get('Retry-After')

				etype = f'{resp["status"]} {resp["body"]["error"]["code"]}'
				emsg = resp["body"]["error"]["message"]
			else:
				wait_time = out.get('headers', {}).get('Retry-After')

				etype = f'{out["status"]} {out["body"]["error"]["code"]}'
				emsg = out["body"]["error"]["message"]
			
			print(f'OneDrive: {etype}: {emsg}')
			if wait_time is not None:
				# wait_time = int(wait_time) + 1
				# print(f'Waiting {wait_time // 60}:{str(wait_time % 60).zfill(2)} min and then retrying...')
				
				done = datetime.now() + timedelta(seconds=wait_time)
				print(f'Waiting {wait_time // 60}:{str(wait_time % 60).zfill(2)} min '
				      f'until {done.strftime("%H:%M:%S")} and then retrying...')
				
				time.sleep(wait_time)
				return self.send_request(send_fn, retry=retry-1, auto_wait=auto_wait)
		
		return out
		
	
	def list_dir(self, path):
		if self.is_expired():
			self.authorize()
		
		out = self.send_request(lambda header:
		                        requests.get(self.endpoint + f'/drive/root:/{str(path)}/:/children'.replace('\\', '/'),
		                                             headers=header))
		return out['value']
		
		
	def get_meta(self, paths):
		reqs = [self.generate_request(f'/me/drive/root:/{path}'.replace('\\', '/')) for path in paths]
		out = self.batch_send(reqs)
		return out

	
	def share_files(self, paths, mode='view'):
		reqs = [self.generate_request(f'/me/drive/root:/{path}:/createLink'.replace('\\', '/'),
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

	
	_batch_size = 15
	
	def batch_send(self, reqs):
		req_order = {id(req): i for i, req in enumerate(reqs)}
		
		resps = [None] * len(reqs)
		remaining = list(reqs)
		while len(remaining):
			batch = [remaining.pop() for _ in range(min(len(remaining), self._batch_size))]
			for i, req in enumerate(batch):
				req['id'] = str(i + 1)
			
			out = self.send_request(lambda header:
			                        requests.post('https://graph.microsoft.com/v1.0/$batch',
			                                      json={'requests': batch},
			                                      headers={'content-type': 'application/json', **header}))
			bad = []
			for i, resp in enumerate(out['responses']):
				if resp['status'] < 300:
					resps[req_order[id(batch[int(resp['id'])-1])]] = resp
				else:
					# print(f'OneDrive: {resp["status"]} {resp["body"]["error"]["code"]}: {resp["body"]["error"]["message"]}')
					bad.append(resp)
			
			if len(bad):
				resp = bad[0]
				etype = f'{resp["status"]} {resp["body"]["error"]["code"]}'
				emsg = resp["body"]["error"]["message"]

				wait_times = [int(resp['headers']['Retry-After'])
				              for resp in bad if 'Retry-After' in resp.get('headers', {})]
				
				print(f'OneDrive: {etype}: {emsg} ({len(bad)}/{len(batch)} failed)')
				if len(wait_times):
					sec = max(wait_times)
				
					done = datetime.now() + timedelta(seconds=sec)
					print(f'Waiting {sec // 60}:{str(sec % 60).zfill(2)} min '
					      f'until {done.strftime("%H:%M:%S")} and then retrying (safe to exit)...')
					time.sleep(sec)
				
				else:
					raise Exception(f'OneDrive: {etype}: {emsg} (and no retry times given)')
			
			remaining.extend(batch[int(resp['id'])-1] for resp in bad)
		
		return resps
	