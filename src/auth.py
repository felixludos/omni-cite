import msal
import webbrowser
import requests
import pyperclip
from msal import PublicClientApplication
from pyzotero import zotero


_zotero = None
def get_zotero(A):
	global _zotero
	if _zotero is None:
		_zotero = zotero.Zotero(A.pull('zotero-library', silent=True), A.pull('zotero-library-type', silent=True),
		                    A.pull('zotero-api-key', silent=True))
	return _zotero


_onedrive = None
def get_onedrive(A):
	global _onedrive
	
	if _onedrive is None:
	
		app_id = A.pull('graph-app-id', silent=True)
		authority_url = 'https://login.microsoftonline.com/consumers'
		
		app = PublicClientApplication(app_id, authority=authority_url)
		
		flow = app.initiate_device_flow(scopes=list(A.pull('graph-scopes', [])))
		
		print(flow['message'])
		pyperclip.copy(flow['user_code'])
		webbrowser.open(flow['verification_uri'])
		
		input('(code copied!) Press enter to continue...')
		
		result = app.acquire_token_by_device_flow(flow)
		access_token_id = result['access_token']
		_onedrive = {'Authorization': f'Bearer {access_token_id}'}
		print('Success!')
	
	return _onedrive

