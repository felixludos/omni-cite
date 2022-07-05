import omnifig as fig
from tqdm import tqdm
from datetime import datetime, timezone
from tabulate import tabulate

import re
import urllib.parse
import requests
from fuzzywuzzy import fuzz

from .auth import get_zotero


def get_date():
	return datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@fig.Script('process-urls')
def fill_in_urls(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	zot = get_zotero(A)
	itr = tqdm(zot.top())
	
	new = []
	errors = []
	
	for item in itr:
		data = item['data']
		itr.set_description('Processing URLs {}'.format(data['key']))
		if data['url'] == '':
			url = None
			if data['itemType'] == 'film' and data['extra'].startswith('IMDb'):
				url = 'https://www.imdb.com/title/{}/'.format(data['extra'].split('\n')[0].split('ID: ')[-1])
			if data['itemType'] == 'book' and len(data.get('ISBN', '')):
				url = 'https://isbnsearch.org/isbn/{}'.format(data['ISBN'].replace('-', ''))
			
			if url is None:
				errors.append([data['key'], data['itemType'], data['title']])
				# print('Failed to generate URL for {} - {}'.format(data['itemType'], data['title']))
			else:
				new.append([data['key'], data['itemType'], data['title'], url])
				# print('Set {} - {}'.format(data['title'], data['url']))
				data['url'] = url
				if not dry_run:
					zot.update_item(data)
	
	if not silent:
		print('New')
		print(tabulate(new, headers=['Key', 'Type', 'Title', 'URL']))
		
		print('Errors')
		print(tabulate(errors, headers=['Key', 'Type', 'Title']))
	
	return new, errors


def clean_up_url(name):
	fixed = re.sub(r'[^a-zA-Z0-9 :-]', '', name)
	fixed = fixed.replace('-', ' ').replace(' ', '+')
	return urllib.parse.quote(fixed).replace('%2B', '+')


# def find_urls(string):
# 	regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
# 	url = re.findall(regex, string)
# 	return [x[0] for x in url]
	
	
@fig.Script('process-papers')
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




