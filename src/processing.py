import omnifig as fig
from tqdm import tqdm
from datetime import datetime, timezone
from tabulate import tabulate

import re
import urllib.parse
import requests
from fuzzywuzzy import fuzz

from .util import print_new_errors, create_url
from .auth import get_zotero


def get_now():
	return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
	# return datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')





@fig.Component('default-url')
class Default_URL_Maker(fig.Configurable):
	def __init__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		
		
	def create_url(self, item):
		data = item['data']
		
		url = None
		if data['itemType'] == 'film' and data['extra'].startswith('IMDb'):
			url = 'https://www.imdb.com/title/{}/'.format(data['extra'].split('\n')[0].split('ID: ')[-1])
		if data['itemType'] == 'book' and len(data.get('ISBN', '')):
			url = 'https://isbnsearch.org/isbn/{}'.format(data['ISBN'].replace('-', ''))
			
		return url



@fig.Script('fill-in-urls')
def fill_in_urls(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	brand_tag = A.pull('brand-tag', 'url')
	ignore_brand_tag = A.pull('ignore-brand', False)
	brand_errors = A.pull('brand-errors', False)
	
	update_existing = A.pull('update-existing', False)
	
	marked = []
	new = []
	def add_new(item, msg):
		marked.append(item)
		new.append([item, msg])
	errors = []
	def add_error(item, msg):
		if brand_errors:
			marked.append(item)
		errors.append([item, msg])
	
	A.push('url-maker._type', 'default-url', overwrite=False, silent=True)
	url_maker = A.pull('url-maker')
	
	zot = A.pull('zotero')
	itr = tqdm(zot.top(brand_tag=brand_tag if ignore_brand_tag else None))
	
	for item in itr:
		data = item['data']
		current = data['url']
		itr.set_description('Filling in URLs {}'.format(data['key']))
		if current == '' or update_existing:
			try:
				url = url_maker.create_url(item)
			except Exception as e:
				add_error(item, f'{type(e)}: {str(e)}')
			else:
				msg = f'Unchanged: {current}'
				if url is not None and url != current:
					data['url'] = url
					msg = f'{repr(current)} -> {repr(url)}'
				add_new(item, msg)
				# marked.append(item)
	
	if not dry_run:
		zot.update_items(marked, brand_tag=brand_tag)
	
	if not silent:
		print_new_errors(new, errors)
	return new, errors



# def find_urls(string):
# 	regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
# 	url = re.findall(regex, string)
# 	return [x[0] for x in url]


@fig.Component('semantic-scholar-matcher')
class SemanticScholarMatcher(fig.Configurable):
	def __int__(self, A, **kwargs):
		super().__init__(A, **kwargs)
		self.match_ratio = A.pull('match-ratio', 92)
		self.base_url = 'http://api.semanticscholar.org/graph/v1/paper/search?query={}'
	
	
	def title_to_query(self, title):
		fixed = re.sub(r'[^a-zA-Z0-9 :-]', '', title)
		fixed = fixed.replace('-', ' ').replace(' ', '+')
		return urllib.parse.quote(fixed).replace('%2B', '+')
		
	
	def call_home(self, url):
		out = requests.get(url).json()
		return out
	
	
	def format_result(self, ssid):
		return f'https://www.semanticscholar.org/paper/{ssid}' if len(ssid) else ssid
	
	
	def find(self, item, dry_run=False):
		title = item['data']['title']
		clean = self.title_to_query(title)
		url = self.base_url.format(clean)
		
		if dry_run:
			return url
		
		out = self.call_home(url)
		
		for res in out.get('data', []):
			if fuzz.ratio(res.get('title', ''), title) >= self.match_ratio:
				return self.format_result(res.get('paperId', ''))
		return ''
	
	

@fig.Script('link-semantic-scholar')
def link_semantic_scholar(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	brand_tag = A.pull('brand-tag', 'sscholar')
	ignore_brand_tag = A.pull('ignore-brand', False)
	brand_errors = A.pull('brand-errors', False)
	
	update_existing = A.pull('update-existing', False)
	
	paper_types = A.pull('paper-types', ['conferencePaper', 'journalArticle', 'preprint'])
	if paper_types is not None and not isinstance(paper_types, str):
		paper_types = ' || '.join(paper_types)
	
	marked = []
	new = []
	def add_new(item, msg):
		marked.append(item)
		new.append([item, msg])
	errors = []
	def add_error(item, msg):
		if brand_errors:
			marked.append(item)
		errors.append([item, msg])
	
	A.push('semantic-scholar-matcher._type', 'semantic-scholar-matcher', overwrite=False, silent=True)
	matcher = A.pull('semantic-scholar-matcher')
	
	zot = A.pull('zotero')
	itr = tqdm(zot.top(itemType=paper_types, brand_tag=brand_tag if ignore_brand_tag else None))
	
	attachment_name = 'Semantic Scholar'
	
	updates = []
	ss = []
	
	for item in itr:
		data = item['data']
		itr.set_description('Linking Semantic Scholar {}'.format(data['key']))
		
		existing = zot.children(data['key'], q=attachment_name, itemType='attachment')
		
		if len(existing) > 1:
			add_error(item, f'Multiple {repr(attachment_name)} attachments')
		elif len(existing) == 0 or update_existing or not len(existing[0]['data']['url']):
			ssurl = matcher.find(item)
		
			if len(existing) == 1:
				old = existing[0]['data']['url']
				existing[0]['data']['url'] = ssurl
				add_new(item, f'{repr(old)} -> {repr(ssurl)}')
				updates.append(existing[0])
			else:
				add_new(item, ssurl)
				ss.append(create_url(attachment_name, ssurl, parentItem=data['key']))
		else:
			old = existing[0]['data']['url']
			add_new(item, f'Unchanged: {old}')
			
	if not dry_run:
		if len(marked):
			zot.update_items(marked, brand_tag=brand_tag)
		if len(updates):
			zot.update_items(updates)
		if len(ss):
			zot.create_items(ss)
	
	if not silent:
		print_new_errors(new, errors)
	return new, errors



# @fig.Script('process-papers')
def process_papers(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	match_ratio = A.pull('match-ratio', 92)
	update_existing = A.pull('update-existing', False)
	
	paper_types = A.pull('paper-types', ['conferencePaper', 'journalArticle', 'preprint'])
	paper_types = set(paper_types)
	
	zot = get_zotero(A)
	itr = tqdm(zot.top())
	
	new = []
	errors = []
	
	base = 'http://api.semanticscholar.org/graph/v1/paper/search?query={}'
	
	for item in itr:
		data = item['data']
		itr.set_description('Processing papers {}'.format(data['key']))
		if data['itemType'] not in paper_types:
			errors.append([data['key'], data['itemType'], data['title'], 'Bad item type'])
		elif update_existing or not any(line.startswith('SemanticScholar ID: ')
		                                for line in data.get('extra', '').split('\n')):
			query = clean_up_url(data['title'])
			url = base.format(query)
			
			if dry_run:
				out = url
			else:
				try:
					out = requests.get(url).json()
					# out = out['data'][0].get('paperId')
				except Exception as e:
					errors.append([data['key'], data['itemType'], data['title'], f'{type(e).__name__}: {e}'])
					out = None
				else:
					for res in out.get('data', []):
						if fuzz.ratio(res.get('title', ''), data['title']) >= match_ratio:
							out = res.get('paperId', '')
							break
					else:
						out = ''
			
			if out is not None:
				# data['semanticscholar'] = out
				if len(out):
					new.append([data['key'], data['itemType'], data['title'], out])
					
					extra = data['extra']
					
					if len(extra):
						lines = extra.split('\n')
						i = None
						for i, line in enumerate(lines):
							if line.startswith('SemanticScholar ID: '):
								old = line.split('SemanticScholar ID: ')[-1]
								lines[i] = f'SemanticScholar ID: {out}'
								errors.append(
									[data['key'], data['itemType'], data['title'], f'replacing {old} with {out}'])
								break
						else:
							lines.append(f'SemanticScholar ID: {out}')
							new.append([data['key'], data['itemType'], data['title'], out])
						data['extra'] = '\n'.join(lines)
					else:
						data['extra'] = f'SemanticScholar ID: {out}'
					
					if not dry_run:
						zot.update_item(data)
				else:
					errors.append([data['key'], data['itemType'], data['title'], out])
			
	if not silent:
		print('New')
		print(tabulate(new, headers=['Key', 'Type', 'Title', 'SemanticScholar ID']))
		
		print('Errors')
		print(tabulate(errors, headers=['Key', 'Type', 'Title', 'Error']))
	
	return new, errors




