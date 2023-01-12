import omnifig as fig
from copy import deepcopy

from . import processing
from . import sharing
from . import publishing


@fig.script('process', description='Process zotero items (including PDFs, code links, wordclouds, etc.).')
def process(A: fig.Node):
	silent = A.pull('silent', False, silent=True)
	silence_config = A.pull('silence-config', silent, silent=True)
	silence_scripts = A.pull('silence-scripts', silent, silent=True)
	if silence_config:
		A.silent = silence_config
	
	fix_urls = A.pull('fix-urls', True)
	if fix_urls:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		cfg.push('extractor._type', 'url-fixer', silent=True, overwrite=False)
		fig.run_script('item-feature', cfg)
		# A.abort()
	elif not silent:
		print('Skipping URL fixer')
	
	link_semantic_scholar = A.pull('link-semantic-scholar', True)
	if link_semantic_scholar:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		cfg.push('extractor._type', 'semantic-scholar', silent=True, overwrite=False)
		fig.run_script('item-feature', cfg)
		# A.abort()
	elif not silent:
		print('Skipping Semantic Scholar linking')

	link_google_scholar = A.pull('link-google-scholar', True)
	if link_google_scholar:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		cfg.push('extractor._type', 'google-scholar', silent=True, overwrite=False)
		fig.run_script('item-feature', cfg)
		# A.abort()
	elif not silent:
		print('Skipping Google Scholar linking')
	
	process_pdfs = A.pull('process-pdfs', True)
	if process_pdfs:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		cfg.push('brand_errors', A.pull('brand-missing-pdfs', True, silent=True), silent=True, overwrite=False)
		fig.run_script('process-attachments', cfg)
		# A.abort()
	elif not silent:
		print('Skipping PDFs processing')
	
	extract_code_links = A.pull('extract-code-links', True)
	if extract_code_links:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		github_processor_type = cfg.pull('github-processor-type', 'github-extractor')
		cfg.push('feature-processor._type', github_processor_type, silent=True)
		fig.run_script('extract-attachment-feature', cfg)
		cfg.push('feature-processor._type', '_x_', silent=True)
		# A.abort()
	elif not silent:
		print('Skipping code links extraction')
	
	generate_wordcloud = A.pull('generate-wordcloud', True)
	if generate_wordcloud:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		wordcloud_processor_type = cfg.pull('wordcloud-processor-type', 'wordcloud')
		cfg.push('feature-processor._type', wordcloud_processor_type, silent=True)
		fig.run_script('extract-attachment-feature', cfg)
		cfg.push('feature-processor._type', '_x_', silent=True)
		# A.abort()
	elif not silent:
		print('Skipping Wordcloud generation')
	
	
	pass



@fig.script('sharing', description='Add sharing OneDrive links to PDFs and Wordclouds.')
def sharing(A):
	silent = A.pull('silent', False, silent=True)
	silence_config = A.pull('silence-config', silent, silent=True)
	silence_scripts = A.pull('silence-scripts', silent, silent=True)
	if silence_config:
		A.silent = silence_config
	
	limit = A.pull('onedrive-limit', None)

	file_links = A.pull('file-links', True)
	if file_links:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		cfg.push('source-name', cfg.pull('file-source-name', 'PDF'), silent=True)
		cfg.push('share-type', cfg.pull('file-share-type', None, silent=True), silent=True)
		cfg.push('limit', limit, silent=True)
		fig.run_script('onedrive-links', cfg)
		# A.abort()
	elif not silent:
		print('Skipping OneDrive file links')

	wordcloud_links = A.pull('wordcloud-links', True)
	if wordcloud_links:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		cfg.push('source-name', cfg.pull('wordcloud-source-name', 'Wordcloud'), silent=True)
		cfg.push('share-type', cfg.pull('wordcloud-share-type', 'download'), silent=True)
		cfg.push('limit', limit, silent=True)
		fig.run_script('onedrive-links', cfg)
		# A.abort()
	elif not silent:
		print('Skipping OneDrive wordcloud download links')

	view_links = A.pull('view-links', False)
	if view_links:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		cfg.push('source-name', cfg.pull('view-source-name', 'PDF'), silent=True)
		cfg.push('share-type', cfg.pull('view-share-type', 'view'), silent=True)
		cfg.push('limit', limit, silent=True)
		fig.run_script('onedrive-links', cfg)
		# A.abort()
	elif not silent:
		print('Skipping OneDrive view links')

	edit_links = A.pull('edit-links', False)
	if edit_links:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		cfg.push('source-name', cfg.pull('edit-source-name', 'PDF'), silent=True)
		cfg.push('share-type', cfg.pull('edit-share-type', 'edit'), silent=True)
		cfg.push('limit', limit, silent=True)
		fig.run_script('onedrive-links', cfg)
		# A.abort()
	elif not silent:
		print('Skipping OneDrive edit links')



@fig.script('publish', description='Upload Zotero items on Notion database.')
def publish(A):
	silent = A.pull('silent', False, silent=True)
	silence_config = A.pull('silence-config', silent, silent=True)
	silence_scripts = A.pull('silence-scripts', silent, silent=True)
	if silence_config:
		A.silent = silence_config

	sync_notion = A.pull('sync-notion', True)
	if sync_notion:
		cfg = deepcopy(A)
		# A.begin()
		cfg.push('silent', silence_scripts, silent=True, overwrite=False)
		fig.run_script('sync-notion', cfg)
		# A.abort()
	elif not silent:
		print('Skipping sync to Notion')














