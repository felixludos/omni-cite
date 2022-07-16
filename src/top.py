import omnifig as fig
from copy import deepcopy

from . import processing
from . import sharing
from . import publishing


@fig.Script('process', description='Process zotero attachments')
def process(A: fig.ConfigType):
	silent = A.pull('silent', False, silent=True)
	silence_config = A.pull('silence-config', silent, silent=True)
	silence_scripts = A.pull('silence-scripts', silent, silent=True)
	A.silence(silence_config)
	
	fix_urls = A.pull('fix-urls', True)
	if fix_urls:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		A.push('extractor._type', 'url-fixer', silent=True, overwrite=False)
		fig.run('item-feature', A)
		A.abort()
	elif not silent:
		print('Skipping URL fixer')
	
	link_semantic_scholar = A.pull('link-semantic-scholar', True)
	if link_semantic_scholar:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		A.push('extractor._type', 'semantic-scholar', silent=True, overwrite=False)
		fig.run('item-feature', A)
		A.abort()
	elif not silent:
		print('Skipping Semantic Scholar linking')

	link_google_scholar = A.pull('link-google-scholar', True)
	if link_google_scholar:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		A.push('extractor._type', 'google-scholar', silent=True, overwrite=False)
		fig.run('item-feature', A)
		A.abort()
	elif not silent:
		print('Skipping Google Scholar linking')
	
	process_pdfs = A.pull('process-pdfs', True)
	if process_pdfs:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		A.push('brand-errors', A.pull('brand-missing-pdfs', True, silent=True), silent=True, overwrite=False)
		fig.run('process-attachments', A)
		A.abort()
	elif not silent:
		print('Skipping PDFs processing')
	
	extract_code_links = A.pull('extract-code-links', True)
	if extract_code_links:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		github_processor_type = A.pull('github-processor-type', 'github-extractor')
		A.push('feature-processor._type', github_processor_type, silent=True)
		fig.run('extract-attachment-feature', A)
		A.push('feature-processor._type', '_x_', silent=True)
		A.abort()
	elif not silent:
		print('Skipping code links extraction')
	
	generate_wordcloud = A.pull('generate-wordcloud', True)
	if generate_wordcloud:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		wordcloud_processor_type = A.pull('wordcloud-processor-type', 'wordcloud')
		A.push('feature-processor._type', wordcloud_processor_type, silent=True)
		fig.run('extract-attachment-feature', A)
		A.push('feature-processor._type', '_x_', silent=True)
		A.abort()
	elif not silent:
		print('Skipping Wordcloud generation')
	
	
	pass



@fig.Script('sharing', description='Add sharing OneDrive links')
def sharing(A):
	silent = A.pull('silent', False, silent=True)
	silence_config = A.pull('silence-config', silent, silent=True)
	silence_scripts = A.pull('silence-scripts', silent, silent=True)
	A.silence(silence_config)
	
	limit = A.pull('onedrive-limit', None)

	file_links = A.pull('file-links', True)
	if file_links:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		A.push('source-name', A.pull('file-source-name', 'PDF'), silent=True)
		A.push('share-type', A.pull('file-share-type', None, silent=True), silent=True)
		A.push('limit', limit, silent=True)
		fig.run('onedrive-links', A)
		A.abort()
	elif not silent:
		print('Skipping OneDrive file links')

	wordcloud_links = A.pull('wordcloud-links', True)
	if wordcloud_links:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		A.push('source-name', A.pull('wordcloud-source-name', 'Wordcloud'), silent=True)
		A.push('share-type', A.pull('wordcloud-share-type', 'download'), silent=True)
		A.push('limit', limit, silent=True)
		fig.run('onedrive-links', A)
		A.abort()
	elif not silent:
		print('Skipping OneDrive wordcloud download links')

	view_links = A.pull('view-links', False)
	if view_links:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		A.push('source-name', A.pull('view-source-name', 'PDF'), silent=True)
		A.push('share-type', A.pull('view-share-type', 'view'), silent=True)
		A.push('limit', limit, silent=True)
		fig.run('onedrive-links', A)
		A.abort()
	elif not silent:
		print('Skipping OneDrive view links')

	edit_links = A.pull('edit-links', False)
	if edit_links:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		A.push('source-name', A.pull('edit-source-name', 'PDF'), silent=True)
		A.push('share-type', A.pull('edit-share-type', 'edit'), silent=True)
		A.push('limit', limit, silent=True)
		fig.run('onedrive-links', A)
		A.abort()
	elif not silent:
		print('Skipping OneDrive edit links')



@fig.Script('publish', description='Publish zotero attachments on Notion')
def publish(A):
	silent = A.pull('silent', False, silent=True)
	silence_config = A.pull('silence-config', silent, silent=True)
	silence_scripts = A.pull('silence-scripts', silent, silent=True)
	A.silence(silence_config)

	sync_notion = A.pull('sync-notion', True)
	if sync_notion:
		A.begin()
		A.push('silent', silence_scripts, silent=True, overwrite=False)
		fig.run('sync-notion', A)
		A.abort()
	elif not silent:
		print('Skipping sync to Notion')














