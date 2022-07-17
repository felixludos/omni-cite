import sys, os, shutil
import copy
import omnifig as fig
from pathlib import Path
from tqdm import tqdm
import backoff
from tabulate import tabulate
from collections import OrderedDict
from urllib.parse import urlparse
from wordcloud import WordCloud, STOPWORDS

import re
import fitz
import urllib.parse
import requests
import pdfkit
import PyPDF2
from fuzzywuzzy import fuzz

from .auth import ZoteroProcess, OneDriveProcess
from .util import create_url, get_now, split_by_filter, Script_Manager


@fig.Script('onedrive-links', description='Create OneDrive share links of zotero attachments')
def onedrive_sharing(A):
	silence_config = A.pull('silence-config', A.pull('silent', silent=True), silent=True)
	A.silence(silence_config)

	A.push('manager._type', 'zotero-manager', overwrite=False, silent=True)
	A.push('manager.pbar-desc', 'OneDrive Links', overwrite=False, silent=True)
	manager: Script_Manager = A.pull('manager')
	
	share_type = A.pull('share-type', None)  # {'view', 'edit', 'download', 'embed'}
	source_name = A.pull('source-name', 'PDF')
	attachment_name = A.pull('attachment-name', None)
	if attachment_name is None and A.pull('use-attachment', False):
		attachment_name = 'OneDrive' if share_type is None else f'OneDrive {share_type.capitalize()}'
	
	link_type = 'file' if share_type is None else f'share ({share_type})'
	out_type = 'the attachment URL' if attachment_name is None else f'a separate attachment "{attachment_name}"'
	manager.log(f'Creating {link_type} links for attachments named "{source_name}" '
	            f'and storing them as {out_type}.')
	
	onedrive_root = Path(A.pull('onedrive-root', str(Path.home() / 'OneDrive')))
	
	A.push('onedrive._type', 'onedrive-auth', overwrite=False, silent=True)
	auth: OneDriveProcess = A.pull('onedrive')
	if manager.is_real_run:
		auth.authorize()

	A.push('brand-tag', 'onedrive' if share_type is None else f'onedrive-{share_type}', overwrite=False, silent=True)
	A.push('zotero._type', 'zotero', overwrite=False, silent=True)
	zot: ZoteroProcess = A.pull('zotero')
	
	manager.preamble(zot=zot)
	
	timestamp = get_now()
	
	attachments = zot.collect(q=source_name, itemType='attachment')
	attachments, unused = split_by_filter(attachments, lambda item: item['data']['linkMode'] == 'linked_file')
	manager.add_failed(*unused, msg='linkMode != "linked_file"')
	attachments = [item for item in attachments if item['data']['linkMode'] == 'linked_file']
	manager.log(f'Found {len(attachments)} new linked file attachments named "{source_name}".')
	
	paths = {}
	for item in attachments:
		path = Path(item['data']['path'])
		
		try:
			loc = path.relative_to(onedrive_root)
		except ValueError:
			manager.log_error('Invalid Path', f'{path} is not in OneDrive', item)
		else:
			paths[loc] = item

	if manager.is_real_run:
		if len(paths):
			if share_type is None:
				resps = auth.get_meta(list(paths.keys()))
				links = [(r.get('body', {}).get('webUrl') if r.get('status', 0) in {200, 201} else None)
				         for r in resps]
			
			else:
				resps = auth.share_files(list(paths.keys()), mode=share_type)
				links = [(r.get('body', {}).get('link', {}).get('webUrl')
				          if r.get('status', 0) in {200, 201} else None)
				         for r in resps]
			
			for (path, item), resp, link in manager.iterate(zip(paths.items(), resps, links), total=len(links)):
				if link is None:
					if 'error' in resp.get('body', {}):
						etype = f'{resp["status"]} {resp["body"]["error"]["code"]}'
						emsg = resp["body"]["error"]["message"]
						if resp['status'] == 429:
							sec = int(resp["headers"]["Retry-After"])
							emsg += f' (retry in {sec//60}:{str(sec%60).zfill(2)} min)'
					else:
						etype = f'Response {resp["status"]}'
						emsg = str(resp)
					manager.log_error(etype, emsg, item)
		
				elif attachment_name is None:
					old = item['data']['url']
					item['data']['url'] = link
					item['data']['accessDate'] = timestamp
					manager.add_update(item, msg=f'{old} -> {link}')
					
				else:
					child = create_url(attachment_name, link, accessDate=timestamp,
					                   parentItem=item['data']['parentItem'])
					manager.add_new(child, msg=link)
		
	else:
		manager.log(f'Dry Run: OneDrive request to get the {len(paths)} links.')
		for path, item in paths.items():
			manager.log_success('OneDrivePath', str(path), item)
	
	return manager.finish()
	










