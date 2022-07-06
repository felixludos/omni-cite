import sys, os, shutil
import copy
import omnifig as fig
from pathlib import Path
from tqdm import tqdm
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

from .auth import get_zotero
from .processing import get_now


def convert_imported(parent, child, storage_root, cloud_root, dry_run):
	
	key = child['data']['key']
	fname = child['data']['filename']
	
	src = storage_root / key / fname
	assert src.exists(), f'not found: {str(src)}'
	
	name = gen_entry_filename(parent)
	dest = cloud_root / f'{name}.pdf'
	
	if not dry_run:
		shutil.copyfile(str(src), str(dest))
	
	return dest


def convert_snapshot(parent, child, storage_root, cloud_root, dry_run):
	key = child['data']['key']
	fname = child['data']['filename']
	
	src = storage_root / key / fname
	assert src.exists(), f'not found: {str(src)}'
	
	name = gen_entry_filename(parent)
	dest = cloud_root / f'{name}.pdf'
	
	if not dry_run and not dest.exists():
		pdfkit.from_file(str(src), str(dest))
	
	return dest


def convert_name(parent, child, dry_run, ext='pdf'):
	path = Path(child['data']['path'])

	name = gen_entry_filename(parent)
	dest = path.parent / f'{name}.{ext}'
	
	if not dry_run and str(path) != str(dest):
		shutil.move(str(path), str(dest))
	
	return dest


@fig.Script('process-pdfs')
def find_pdf(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	remove_imported = A.pull('remove-imported', False)
	
	zotero_storage = Path(A.pull('zotero-storage', str(Path.home()/'Zotero/storage')))
	assert zotero_storage.exists(), f'Missing zotero storage directory: {str(zotero_storage)}'
	
	cloud_root = Path(A.pull('zotero-cloud-storage', str(Path.home()/'OneDrive/Papers/zotero')))
	if not cloud_root.exists():
		os.makedirs(str(cloud_root))
	
	zot = get_zotero(A)
	itr = tqdm(zot.top())
	
	new = []
	errors = []
	
	def extract_warning(item, msg):
		errors.append([item['data']['key'], item['data']['itemType'], item['data']['title'], msg])
		
	for item in itr:
		data = item['data']
		itr.set_description('Process PDFs {}'.format(data['key']))
		children = zot.children(data['key'])
		
		linked = filter_linked_pdfs(children)
		
		pdf = None
		if len(linked) == 1:
			dest = linked[0].get('data', {}).get('path')
			pdf = str(convert_name(item, linked[0], dry_run, 'pdf'))
			if not dry_run and (pdf != dest or linked[0]['data']['title'] != 'pdf'):
				linked[0]['data']['title'] = 'pdf'
				linked[0]['data']['path'] = str(pdf)
				out = zot.update_item(linked[0])
			if remove_imported:
				imported = filter_imported_pdfs(children)
				for imp in imported:
					zot.delete_item(imp)
			
		elif len(linked) == 0:
			pdf = None
			imported = filter_imported_pdfs(children)
			
			if len(imported) > 1:
				found = '\n'.join([' - {}'.format(entry['data']['title']) for entry in imported])
				extract_warning(item, f'Found multiple imported PDFs: \n{found}')
			if len(imported) > 0:
				dest = convert_imported(item, imported[0], zotero_storage, cloud_root, dry_run)
				if not dry_run:
					out = add_file_attachment(zot, data['key'], 'pdf', str(dest), contentType='application/pdf', accessDate=get_now())
					
				pdf = str(dest)
			
			if remove_imported:
				for imp in imported:
					zot.delete_item(imp)
			
			if pdf is None:
				snapshots = filter_snapshots(children)
				if len(snapshots) > 1:
					found = '\n'.join([' - {}'.format(entry['data']['title']) for entry in imported])
					extract_warning(item, f'Found multiple snapshots: \n{found}')
				if len(snapshots) > 0:
					dest = convert_snapshot(item, snapshots[0], zotero_storage, cloud_root, dry_run)
					if not dry_run:
						out = add_file_attachment(zot, data['key'], 'pdf', str(dest), contentType='application/pdf', accessDate=get_now())
					pdf = str(dest)
			
			if pdf is None:
				extract_warning(item, f'No PDF found')
		
		else: # multiple links
			found = '\n'.join([' - {}'.format(entry['data']['title']) for entry in linked])
			extract_warning(item, f'Found multiple linked PDFs: \n{found}')
		
		if pdf is not None:
			new.append([item['data']['key'], item['data']['itemType'], item['data']['title'], pdf])
	
	if not silent:
		print('New')
		print(tabulate(new, headers=['Key', 'Type', 'Title', 'Path']))
		
		print('Errors')
		print(tabulate(sorted(errors, key=lambda x: (x[1], x[2])), headers=['Key', 'Type', 'Title', 'Msg']))
	
	return new, errors



def filter_linked_pdfs(children):
	return [child for child in children
			if child['data']['itemType'] == 'attachment'
			and child['data'].get('linkMode') == 'linked_file'
			and child['data'].get('contentType') == 'application/pdf']

def filter_imported_pdfs(children):
	return [child for child in children
			if child['data']['itemType'] == 'attachment'
			and child['data'].get('linkMode') == 'imported_url'
			and child['data'].get('contentType') == 'application/pdf']

def filter_snapshots(children):
	return [child for child in children
			if child['data']['itemType'] == 'attachment' and child['data']['title'] == 'Snapshot'
			and child['data'].get('linkMode') == 'imported_url'
			and child['data'].get('contentType') == 'text/html']

def filter_wordcloud(children):
	return [child for child in children
			if child['data']['itemType'] == 'attachment' and child['data']['title'] == 'wordcloud'
			and child['data'].get('linkMode') == 'linked_file'
			and child['data'].get('contentType') == 'image/jpg']

def filter_code_mentions(children):
	return [child for child in children
			if child['data']['itemType'] == 'note' and child['data']['note'].startswith('<p>code mentions')]

def filter_semantic_scholar_links(children):
	return [child for child in children
			if child['data']['itemType'] == 'attachment' and child['data']['title'] == 'Semantic Scholar'
			and child['data'].get('linkMode') == 'linked_url']



def gen_entry_filename(item):
	meta = item['meta']
	
	title = re.sub('<.*?>', '', item['data']['title']).replace(' - ', ' ')
	authors = meta.get('creatorSummary', '').replace('.', '').replace(' et al', '+').replace(' and ', '+')
	year = meta.get('parsedDate', '').split('-')[0]
	if len(year):
		year = f' ({year})'
	
	if len(authors) and not len(year):
		prefix = f'{authors} - '
	else:
		prefix = f'{authors}{year} '
	
	value = f'{prefix}{title}'.replace('  ', ' ')
	value = re.sub(r'[^\w\s\-_()+]', '', value).strip()
	return value
	# return re.sub(r'[-\s]+', '-', value).strip('-_')



_link_template = {'itemType': 'attachment',
 'linkMode': 'linked_url',
 'title': '',
 'accessDate': '',
 'url': '',
 'note': '',
 'tags': [],
 'collections': [],
 'relations': {},
 'contentType': '',
 'charset': ''}


def add_link_attachment(zot, parent_key, title, url, **data):
	# template = zot.item_template('attachment', 'linked_url')
	template = copy.deepcopy(_link_template)

	template.update(data)
	template['title'] = title
	template['url'] = url
	
	return zot.create_items([template], parentid=parent_key)


_file_template = {
   'itemType': 'attachment',
   'linkMode': 'linked_file',
   'title': '',
   'accessDate': '',
   'url': '',
   'note': '',
   'contentType': '',
   'charset': '',
   'path': '',
   'tags': [],
   'relations': {},}


def add_file_attachment(zot, parent_key, title, path, **data):
	# template = zot.item_template('attachment', 'linked_file')
	template = copy.deepcopy(_file_template)
	
	template.update(data)
	template['title'] = title
	template['path'] = path
	
	return zot.create_items([template], parentid=parent_key)



_note_template = {'itemType': 'note',
 'note': '',
 'tags': [],
 'collections': [],
 'relations': {}}


def add_note_attachment(zot, parent_key, note, **data):
	# template = zot.item_template('attachment', 'linked_file')
	template = copy.deepcopy(_note_template)
	
	template.update(data)
	template['note'] = note
	
	return zot.create_items([template], parentid=parent_key)


gh_stopwords = ["0o", "0s", "3a", "3b", "3d", "6b", "6o", "a", "A", "a1", "a2", "a3", "a4", "ab", "able", "about", "above", "abst", "ac", "accordance", "according", "accordingly", "across", "act", "actually", "ad", "added", "adj", "ae", "af", "affected", "affecting", "after", "afterwards", "ag", "again", "against", "ah", "ain", "aj", "al", "all", "allow", "allows", "almost", "alone", "along", "already", "also", "although", "always", "am", "among", "amongst", "amoungst", "amount", "an", "and", "announce", "another", "any", "anybody", "anyhow", "anymore", "anyone", "anyway", "anyways", "anywhere", "ao", "ap", "apart", "apparently", "appreciate", "approximately", "ar", "are", "aren", "arent", "arise", "around", "as", "aside", "ask", "asking", "at", "au", "auth", "av", "available", "aw", "away", "awfully", "ax", "ay", "az", "b", "B", "b1", "b2", "b3", "ba", "back", "bc", "bd", "be", "became", "been", "before", "beforehand", "beginnings", "behind", "below", "beside", "besides", "best", "between", "beyond", "bi", "bill", "biol", "bj", "bk", "bl", "bn", "both", "bottom", "bp", "br", "brief", "briefly", "bs", "bt", "bu", "but", "bx", "by", "c", "C", "c1", "c2", "c3", "ca", "call", "came", "can", "cannot", "cant", "cc", "cd", "ce", "certain", "certainly", "cf", "cg", "ch", "ci", "cit", "cj", "cl", "clearly", "cm", "cn", "co", "com", "come", "comes", "con", "concerning", "consequently", "consider", "considering", "could", "couldn", "couldnt", "course", "cp", "cq", "cr", "cry", "cs", "ct", "cu", "cv", "cx", "cy", "cz", "d", "D", "d2", "da", "date", "dc", "dd", "de", "definitely", "describe", "described", "despite", "detail", "df", "di", "did", "didn", "dj", "dk", "dl", "do", "does", "doesn", "doing", "don", "done", "down", "downwards", "dp", "dr", "ds", "dt", "du", "due", "during", "dx", "dy", "e", "E", "e2", "e3", "ea", "each", "ec", "ed", "edu", "ee", "ef", "eg", "ei", "eight", "eighty", "either", "ej", "el", "eleven", "else", "elsewhere", "em", "en", "end", "ending", "enough", "entirely", "eo", "ep", "eq", "er", "es", "especially", "est", "et", "et-al", "etc", "eu", "ev", "even", "ever", "every", "everybody", "everyone", "everything", "everywhere", "ex", "exactly", "example", "except", "ey", "f", "F", "f2", "fa", "far", "fc", "few", "ff", "fi", "fifteen", "fifth", "fify", "fill", "find", "fire", "five", "fix", "fj", "fl", "fn", "fo", "followed", "following", "follows", "for", "former", "formerly", "forth", "forty", "found", "four", "fr", "from", "front", "fs", "ft", "fu", "full", "further", "furthermore", "fy", "g", "G", "ga", "gave", "ge", "get", "gets", "getting", "gi", "give", "given", "gives", "giving", "gj", "gl", "go", "goes", "going", "gone", "got", "gotten", "gr", "greetings", "gs", "gy", "h", "H", "h2", "h3", "had", "hadn", "happens", "hardly", "has", "hasn", "hasnt", "have", "haven", "having", "he", "hed", "hello", "help", "hence", "here", "hereafter", "hereby", "herein", "heres", "hereupon", "hes", "hh", "hi", "hid", "hither", "hj", "ho", "hopefully", "how", "howbeit", "however", "hr", "hs", "http", "hu", "hundred", "hy", "i2", "i3", "i4", "i6", "i7", "i8", "ia", "ib", "ibid", "ic", "id", "ie", "if", "ig", "ignored", "ih", "ii", "ij", "il", "im", "immediately", "in", "inasmuch", "inc", "indeed", "index", "indicate", "indicated", "indicates", "information", "inner", "insofar", "instead", "interest", "into", "inward", "io", "ip", "iq", "ir", "is", "isn", "it", "itd", "its", "iv", "ix", "iy", "iz", "j", "J", "jj", "jr", "js", "jt", "ju", "just", "k", "K", "ke", "keep", "keeps", "kept", "kg", "kj", "km", "ko", "l", "L", "l2", "la", "largely", "last", "lately", "later", "latter", "latterly", "lb", "lc", "le", "least", "les", "less", "lest", "let", "lets", "lf", "like", "liked", "likely", "line", "little", "lj", "ll", "ln", "lo", "look", "looking", "looks", "los", "lr", "ls", "lt", "ltd", "m", "M", "m2", "ma", "made", "mainly", "make", "makes", "many", "may", "maybe", "me", "meantime", "meanwhile", "merely", "mg", "might", "mightn", "mill", "million", "mine", "miss", "ml", "mn", "mo", "more", "moreover", "most", "mostly", "move", "mr", "mrs", "ms", "mt", "mu", "much", "mug", "must", "mustn", "my", "n", "N", "n2", "na", "name", "namely", "nay", "nc", "nd", "ne", "near", "nearly", "necessarily", "neither", "nevertheless", "new", "next", "ng", "ni", "nine", "ninety", "nj", "nl", "nn", "no", "nobody", "non", "none", "nonetheless", "noone", "nor", "normally", "nos", "not", "noted", "novel", "now", "nowhere", "nr", "ns", "nt", "ny", "o", "O", "oa", "ob", "obtain", "obtained", "obviously", "oc", "od", "of", "off", "often", "og", "oh", "oi", "oj", "ok", "okay", "ol", "old", "om", "omitted", "on", "once", "one", "ones", "only", "onto", "oo", "op", "oq", "or", "ord", "os", "ot", "otherwise", "ou", "ought", "our", "out", "outside", "over", "overall", "ow", "owing", "own", "ox", "oz", "p", "P", "p1", "p2", "p3", "page", "pagecount", "pages", "par", "part", "particular", "particularly", "pas", "past", "pc", "pd", "pe", "per", "perhaps", "pf", "ph", "pi", "pj", "pk", "pl", "placed", "please", "plus", "pm", "pn", "po", "poorly", "pp", "pq", "pr", "predominantly", "presumably", "previously", "primarily", "probably", "promptly", "proud", "provides", "ps", "pt", "pu", "put", "py", "q", "Q", "qj", "qu", "que", "quickly", "quite", "qv", "r", "R", "r2", "ra", "ran", "rather", "rc", "rd", "re", "readily", "really", "reasonably", "recent", "recently", "ref", "refs", "regarding", "regardless", "regards", "related", "relatively", "research-articl", "respectively", "resulted", "resulting", "results", "rf", "rh", "ri", "right", "rj", "rl", "rm", "rn", "ro", "rq", "rr", "rs", "rt", "ru", "run", "rv", "ry", "s", "S", "s2", "sa", "said", "saw", "say", "saying", "says", "sc", "sd", "se", "sec", "second", "secondly", "section", "seem", "seemed", "seeming", "seems", "seen", "sent", "seven", "several", "sf", "shall", "shan", "shed", "shes", "show", "showed", "shown", "showns", "shows", "si", "side", "since", "sincere", "six", "sixty", "sj", "sl", "slightly", "sm", "sn", "so", "some", "somehow", "somethan", "sometime", "sometimes", "somewhat", "somewhere", "soon", "sorry", "sp", "specifically", "specified", "specify", "specifying", "sq", "sr", "ss", "st", "still", "stop", "strongly", "sub", "substantially", "successfully", "such", "sufficiently", "suggest", "sup", "sure", "sy", "sz", "t", "T", "t1", "t2", "t3", "take", "taken", "taking", "tb", "tc", "td", "te", "tell", "ten", "tends", "tf", "th", "than", "thank", "thanks", "thanx", "that", "thats", "the", "their", "theirs", "them", "themselves", "then", "thence", "there", "thereafter", "thereby", "thered", "therefore", "therein", "thereof", "therere", "theres", "thereto", "thereupon", "these", "they", "theyd", "theyre", "thickv", "thin", "think", "third", "this", "thorough", "thoroughly", "those", "thou", "though", "thoughh", "thousand", "three", "throug", "through", "throughout", "thru", "thus", "ti", "til", "tip", "tj", "tl", "tm", "tn", "to", "together", "too", "took", "top", "toward", "towards", "tp", "tq", "tr", "tried", "tries", "truly", "try", "trying", "ts", "tt", "tv", "twelve", "twenty", "twice", "two", "tx", "u", "U", "u201d", "ue", "ui", "uj", "uk", "um", "un", "under", "unfortunately", "unless", "unlike", "unlikely", "until", "unto", "uo", "up", "upon", "ups", "ur", "us", "used", "useful", "usefully", "usefulness", "using", "usually", "ut", "v", "V", "va", "various", "vd", "ve", "very", "via", "viz", "vj", "vo", "vol", "vols", "volumtype", "vq", "vs", "vt", "vu", "w", "W", "wa", "was", "wasn", "wasnt", "way", "we", "wed", "welcome", "well", "well-b", "went", "were", "weren", "werent", "what", "whatever", "whats", "when", "whence", "whenever", "where", "whereafter", "whereas", "whereby", "wherein", "wheres", "whereupon", "wherever", "whether", "which", "while", "whim", "whither", "who", "whod", "whoever", "whole", "whom", "whomever", "whos", "whose", "why", "wi", "widely", "with", "within", "without", "wo", "won", "wonder", "wont", "would", "wouldn", "wouldnt", "www", "x", "X", "x1", "x2", "x3", "xf", "xi", "xj", "xk", "xl", "xn", "xo", "xs", "xt", "xv", "xx", "y", "Y", "y2", "yes", "yet", "yj", "yl", "you", "youd", "your", "youre", "yours", "yr", "ys", "yt", "z", "Z", "zero", "zi", "zz"]
EXPANDED_STOPWORDS = {'arXiv', 'preprint', 'arxiv', 'proceedings', 'advances', 'model', 'sample', 'samples', 'images',
								 'using', 'image', 'set', 'models', 'journal', 'international', 'conference', 'article',
								 'method', 'outcome', 'data', 'section', 'pages',
					  'ICLR', 'ICML', 'ICCV', 'CVPR', 'NIPS', 'sciencemag', 'researchgate', 'journal', 'conference',
					  'IEEE', 'Max Planck', 'thus', #'institute',
					  'really', 'think', 'thing', 'know', 'need', 'going', 'maybe', 'want', 'something',
					  'will', 'make', 'may', 'another', 'much', 'many',
								 'mathbf', 'mathbb', 'nabla', 'nabla_', 'mathrm',
								 'Neural Information Processing Systems', 'use', 'Figure', 'Fig', 'Table', 'Equation',
								 *STOPWORDS, *gh_stopwords}


def generate_wordcloud(text, w=800, h=400, max_words=50, min_font_size=10, min_word_length=3,
					   background_color='black', colormap='Pastel2', stopwords=EXPANDED_STOPWORDS, **kwargs):
	wordcloud = WordCloud(width=w, height=h, max_words=max_words, min_font_size=min_font_size,
						  background_color=background_color, colormap=colormap,
					  stopwords=stopwords, min_word_length=min_word_length, **kwargs).generate(text)
	return wordcloud


@fig.Component('wordcloud')
class WordcloudMaker(fig.Configurable):
	
	def __init__(self, A, height=400, width=800, max_words=50, min_font_size=10, min_word_length=3,
					   background_color='black', colormap='Pastel2', stopwords=[], **kwargs):
		height = A.pull('height', '<>H', height)
		width = A.pull('width', '<>W', width)
		max_words = A.pull('max-words', max_words)
		min_font_size = A.pull('min-font-size', min_font_size)
		min_word_length = A.pull('min-word-length', min_word_length)
		background_color = A.pull('background-color', background_color)
		colormap = A.pull('colormap', colormap)
		use_stopwords = A.pull('use-stopwords', True)
		stopwords = set(A.pull('extra-stopwords', stopwords))
		if use_stopwords:
			stopwords = {*stopwords, *EXPANDED_STOPWORDS}
		
		super().__init__(A, **kwargs)
		
		self.size = height, width
		self.max_words = max_words
		self.min_font_size = min_font_size
		self.min_word_length = min_word_length
		self.background_color = background_color
		self.colormap = colormap
		self.stopwords = stopwords
		
		
	def generate(self, text, **kwargs):
		return WordCloud(width=self.size[1], height=self.size[0], max_words=self.max_words,
		                 min_font_size=self.min_font_size, background_color=self.background_color,
		                 colormap=self.colormap, stopwords=self.stopwords, min_word_length=self.min_word_length,
		                 **kwargs).generate(text)



def extract_text(path):
	pdf = fitz.open(path)
	full_text = []
	for n in range(pdf.page_count):
		full_text.append(pdf.get_page_text(n))
	return full_text


def extract_transcript(path):
	full_text = extract_text(path)
	transcript = '\n'.join(full_text)
	return transcript


def find_urls(string):
#     regex = r'[A-Za-z0-9]+://[A-Za-z0-9%-_]+(/[A-Za-z0-9%-_])*(#|\\?)[A-Za-z0-9%-_&=]*'
	regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
#     regex = r'''(?i)\b((?:https?:(?:/{1,3}|[a-z0-9%])|[a-z0-9.\-]+[.](?:com|net|org|edu|gov|mil|aero|asia|biz|cat|coop|info|int|jobs|mobi|museum|name|post|pro|tel|travel|xxx|ac|ad|ae|af|ag|ai|al|am|an|ao|aq|ar|as|at|au|aw|ax|az|ba|bb|bd|be|bf|bg|bh|bi|bj|bm|bn|bo|br|bs|bt|bv|bw|by|bz|ca|cc|cd|cf|cg|ch|ci|ck|cl|cm|cn|co|cr|cs|cu|cv|cx|cy|cz|dd|de|dj|dk|dm|do|dz|ec|ee|eg|eh|er|es|et|eu|fi|fj|fk|fm|fo|fr|ga|gb|gd|ge|gf|gg|gh|gi|gl|gm|gn|gp|gq|gr|gs|gt|gu|gw|gy|hk|hm|hn|hr|ht|hu|id|ie|il|im|in|io|iq|ir|is|it|je|jm|jo|jp|ke|kg|kh|ki|km|kn|kp|kr|kw|ky|kz|la|lb|lc|li|lk|lr|ls|lt|lu|lv|ly|ma|mc|md|me|mg|mh|mk|ml|mm|mn|mo|mp|mq|mr|ms|mt|mu|mv|mw|mx|my|mz|na|nc|ne|nf|ng|ni|nl|no|np|nr|nu|nz|om|pa|pe|pf|pg|ph|pk|pl|pm|pn|pr|ps|pt|pw|py|qa|re|ro|rs|ru|rw|sa|sb|sc|sd|se|sg|sh|si|sj|Ja|sk|sl|sm|sn|so|sr|ss|st|su|sv|sx|sy|sz|tc|td|tf|tg|th|tj|tk|tl|tm|tn|to|tp|tr|tt|tv|tw|tz|ua|ug|uk|us|uy|uz|va|vc|ve|vg|vi|vn|vu|wf|ws|ye|yt|yu|za|zm|zw)/)(?:[^\s()<>{}\[\]]+|\([^\s()]*?\([^\s()]+\)[^\s()]*?\)|\([^\s]+?\))+(?:\([^\s()]*?\([^\s()]+\)[^\s()]*?\)|\([^\s]+?\)|[^\s`!()\[\]{};:'".,<>?«»“”‘’])|(?:(?<!@)[a-z0-9]+(?:[.\-][a-z0-9]+)*[.](?:com|net|org|edu|gov|mil|aero|asia|biz|cat|coop|info|int|jobs|mobi|museum|name|post|pro|tel|travel|xxx|ac|ad|ae|af|ag|ai|al|am|an|ao|aq|ar|as|at|au|aw|ax|az|ba|bb|bd|be|bf|bg|bh|bi|bj|bm|bn|bo|br|bs|bt|bv|bw|by|bz|ca|cc|cd|cf|cg|ch|ci|ck|cl|cm|cn|co|cr|cs|cu|cv|cx|cy|cz|dd|de|dj|dk|dm|do|dz|ec|ee|eg|eh|er|es|et|eu|fi|fj|fk|fm|fo|fr|ga|gb|gd|ge|gf|gg|gh|gi|gl|gm|gn|gp|gq|gr|gs|gt|gu|gw|gy|hk|hm|hn|hr|ht|hu|id|ie|il|im|in|io|iq|ir|is|it|je|jm|jo|jp|ke|kg|kh|ki|km|kn|kp|kr|kw|ky|kz|la|lb|lc|li|lk|lr|ls|lt|lu|lv|ly|ma|mc|md|me|mg|mh|mk|ml|mm|mn|mo|mp|mq|mr|ms|mt|mu|mv|mw|mx|my|mz|na|nc|ne|nf|ng|ni|nl|no|np|nr|nu|nz|om|pa|pe|pf|pg|ph|pk|pl|pm|pn|pr|ps|pt|pw|py|qa|re|ro|rs|ru|rw|sa|sb|sc|sd|se|sg|sh|si|sj|Ja|sk|sl|sm|sn|so|sr|ss|st|su|sv|sx|sy|sz|tc|td|tf|tg|th|tj|tk|tl|tm|tn|to|tp|tr|tt|tv|tw|tz|ua|ug|uk|us|uy|uz|va|vc|ve|vg|vi|vn|vu|wf|ws|ye|yt|yu|za|zm|zw)\b/?(?!@)))'''
#     regex = "https?:\\/\\/(?:www\\.)?[-a-zA-Z0-9@:%._\\+~#=]{1,256}\\.[a-zA-Z0-9()]{1,6}\\b(?:[-a-zA-Z0-9()@:%_\\+.~#?&\\/=]*)"
#     regex = r'\b((?:https?://)?(?:(?:www\.)?(?:[\da-z\.-]+)\.(?:[a-z]{2,6})|(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)|(?:(?:[0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}|(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:(?:(?::[0-9a-fA-F]{1,4}){1,6})|:(?:(?::[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(?::[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(?:ffff(?::0{1,4}){0,1}:){0,1}(?:(?:25[0-5]|(?:2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(?:25[0-5]|(?:2[0-4]|1{0,1}[0-9]){0,1}[0-9])|(?:[0-9a-fA-F]{1,4}:){1,4}:(?:(?:25[0-5]|(?:2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(?:25[0-5]|(?:2[0-4]|1{0,1}[0-9]){0,1}[0-9])))(?::[0-9]{1,4}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])?(?:/[\w\.-]*)*/?)\b'
	url = re.findall(regex, string)
	return [x[0] for x in url]


def extract_links(path):
	PDF = PyPDF2.PdfFileReader(str(path))
	# pages = PDF.getNumPages()
	pages = PDF.pages
	key = '/Annots'
	uri = '/URI'
	ank = '/A'

	urls = []

	for page in pages:
	#     print("Current Page: {}".format(page))
		pageSliced = page #PDF.getPage(page)
		pageObject = pageSliced.getObject()
		if key in pageObject.keys():
			ann = pageObject[key]
			for a in ann:
				u = a.getObject()
				if ank in u and uri in u[ank].keys():
	#                 print(u[ank][uri])
					urls.append(u[ank][uri])

	return urls


def extract_urls(path, transcript=None):
	if transcript is None:
		transcript = extract_transcript(path)
	
	urls = extract_links(path) + find_urls(transcript)
	urls = [(url if url.startswith('http') else 'http://' + url) for url in urls]
	return urls


def find_github_projects(urls):
	domains = [urlparse(url).netloc for url in urls]
	domains = [domain[4:] if domain.startswith('www.') else domain for domain in domains]
	
	githubs = [url for url, domain in zip(urls, domains) if domain.lower() == 'github.com']
	
	projs = []
	for gh in githubs:
		terms = gh.lower().split('#')[0].split('?')[0].split('github.com/')
		if len(terms) == 2:
			terms = terms[1].split('/')
			if len(terms) == 2 and len(terms[0]) and len(terms[1]):
				projs.append('/'.join(terms))
	projs = list(OrderedDict.fromkeys(projs))
	return [f'http://github.com/{proj}' for proj in projs]


@fig.Script('extract-features')
def extract_features(A):
	dry_run = A.pull('dry-run', False)
	silent = A.pull('silent', False)
	
	# overwrite_existing = A.pull('overwrite-existing', False)
	
	zotero_storage = Path(A.pull('zotero-storage', str(Path.home() / 'Zotero/storage')))
	assert zotero_storage.exists(), f'Missing zotero storage directory: {str(zotero_storage)}'
	
	cloud_root = Path(A.pull('zotero-cloud-storage', str(Path.home() / 'OneDrive/Papers/zotero')))
	if not cloud_root.exists():
		os.makedirs(str(cloud_root))
	
	wordcloud_root = Path(A.pull('wordcloud-root', str(Path.home() / 'OneDrive/Papers/wordclouds')))
	if not wordcloud_root.exists():
		os.makedirs(str(wordcloud_root))
	
	wordcloud_maker = A.pull('wordcloud', None)
	include_code_mentions = A.pull('include-code-mentions', True)
	
	update_existing = A.pull('update-existing', False)
	
	zot = get_zotero(A)
	itr = tqdm(zot.top())
	
	new = []
	errors = []
	
	def extract_warning(item, msg):
		errors.append([item['data']['key'], item['data']['itemType'], item['data']['title'], msg])
	
	for item in itr:
		data = item['data']
		itr.set_description('Extract Features {}'.format(data['key']))
		
		name = gen_entry_filename(item)
		
		children = zot.children(data['key'])
		pdfs = filter_linked_pdfs(children)
		
		transcript = None
		added_error = False
		
		wc = filter_wordcloud(children)
		if wordcloud_maker is not None and len(wc) == 0:
			if len(pdfs) == 1:
				pdf = pdfs[0]
				path = Path(pdf['data']['path'])
				transcript = extract_transcript(path)
				
				wordcloud = wordcloud_maker.generate(transcript)
				dest = wordcloud_root / f'{name}.jpg'
				words = ';'.join([word for word in sorted(wordcloud.words_.keys(),
				                                          key=lambda w: wordcloud.words_[w], reverse=True)])
				if not dry_run:
					wordcloud.to_image().save(str(dest), "JPEG")
					out = add_file_attachment(zot, data['key'], 'wordcloud', str(dest), contentType='image/jpg',
					                          note=words, accessDate=get_now())
			else:
				added_error = True
				extract_warning(item, 'Too many PDFs' if len(pdfs) else 'No PDFs found')
		elif len(wc) == 1:
			if update_existing:
				dest = wc[0].get('data', {}).get('path')
				path = str(convert_name(item, wc[0], dry_run, ext='jpg'))
				if not dry_run and (path != dest or wc[0]['data']['title'] != 'wordcloud'):
					wc[0]['data']['title'] = 'wordcloud'
					wc[0]['data']['path'] = str(path)
					out = zot.update_item(wc[0])
		else:
			found = '\n'.join([' - {}'.format(entry['data']['title']) for entry in wc])
			extract_warning(item, f'Found multiple wordclouds: \n{found}')
		
		mentions = filter_code_mentions(children)
		if include_code_mentions and len(mentions) == 0:
			if len(pdfs) == 1:
				pdf = pdfs[0]
				path = Path(pdf['data']['path'])
				if transcript is None:
					transcript = extract_transcript(path)
				
				urls = extract_urls(path, transcript)
				code_links = find_github_projects(urls)
				code_links = [f'<a href="{link}" rel="noopener noreferrer nofollow">{link}</a>'
				              for link in code_links]
				if len(code_links) and not dry_run:
					out = add_note_attachment(zot, data['key'],
					                          '\n'.join(f'<p>{line}</p>' for line in ['code mentions', *code_links]))
			elif not added_error:
				extract_warning(item, 'Too many PDFs' if len(pdfs) else 'No PDFs found')
		elif len(mentions) == 1:
			if update_existing:
				if len(pdfs) == 1:
					pdf = pdfs[0]
					path = Path(pdf['data']['path'])
					if transcript is None:
						transcript = extract_transcript(path)
					
					urls = extract_urls(path, transcript)
					code_links = find_github_projects(urls)
					code_links = [f'<a href="{link}" rel="noopener noreferrer nofollow">{link}</a>'
					              for link in code_links]
					if len(code_links) and not dry_run:
						mentions[0]['data']['note'] = '\n'.join(f'<p>{line}</p>' for line in ['code mentions', *code_links])
						out = zot.update_item(mentions[0])
				elif not added_error:
					extract_warning(item, 'Too many PDFs' if len(pdfs) else 'No PDFs found')
				
		else:
			found = '\n'.join([' - {}'.format(entry['data']['title']) for entry in mentions])
			extract_warning(item, f'Found multiple notes: \n{found}')
		
	if not silent:
		print('New')
		print(tabulate(new, headers=['Key', 'Type', 'Title', 'Path']))
		
		print('Errors')
		print(tabulate(sorted(errors, key=lambda x: (x[1], x[2])), headers=['Key', 'Type', 'Title', 'Msg']))

	return new, errors


# @fig.Script('semantic-scholar-links')
# def process_papers(A):
# 	dry_run = A.pull('dry-run', False)
# 	silent = A.pull('silent', False)
#
# 	match_ratio = A.pull('match-ratio', 92)
# 	update_existing = A.pull('update-existing', False)
#
# 	paper_types = A.pull('paper-types', ['conferencePaper', 'journalArticle', 'preprint'])
# 	paper_types = set(paper_types)
#
# 	zot = get_zotero(A)
# 	itr = tqdm(zot.top())
#
# 	new = []
# 	errors = []
#
# 	base = 'http://api.semanticscholar.org/graph/v1/paper/search?query={}'
#
# 	for item in itr:
# 		data = item['data']
# 		itr.set_description('Processing papers {}'.format(data['key']))
# 		if data['itemType'] not in paper_types:
# 			errors.append([data['key'], data['itemType'], data['title'], 'Bad item type'])
# 		elif update_existing or not any(line.startswith('SemanticScholar ID: ')
# 		                                for line in data.get('extra', '').split('\n')):
# 			query = clean_up_url(data['title'])
# 			url = base.format(query)
#
# 			if dry_run:
# 				out = url
# 			else:
# 				try:
# 					out = requests.get(url).json()
# 				# out = out['data'][0].get('paperId')
# 				except Exception as e:
# 					errors.append([data['key'], data['itemType'], data['title'], f'{type(e).__name__}: {e}'])
# 					out = None
# 				else:
# 					for res in out.get('data', []):
# 						if fuzz.ratio(res.get('title', ''), data['title']) >= match_ratio:
# 							out = res.get('paperId', '')
# 							break
# 					else:
# 						out = ''
#
# 			if out is not None:
# 				# data['semanticscholar'] = out
# 				if len(out):
# 					new.append([data['key'], data['itemType'], data['title'], out])
#
# 					extra = data['extra']
#
# 					if len(extra):
# 						lines = extra.split('\n')
# 						i = None
# 						for i, line in enumerate(lines):
# 							if line.startswith('SemanticScholar ID: '):
# 								old = line.split('SemanticScholar ID: ')[-1]
# 								lines[i] = f'SemanticScholar ID: {out}'
# 								errors.append(
# 									[data['key'], data['itemType'], data['title'], f'replacing {old} with {out}'])
# 								break
# 						else:
# 							lines.append(f'SemanticScholar ID: {out}')
# 							new.append([data['key'], data['itemType'], data['title'], out])
# 						data['extra'] = '\n'.join(lines)
# 					else:
# 						data['extra'] = f'SemanticScholar ID: {out}'
#
# 					if not dry_run:
# 						zot.update_item(data)
# 				else:
# 					errors.append([data['key'], data['itemType'], data['title'], out])
#
# 	if not silent:
# 		print('New')
# 		print(tabulate(new, headers=['Key', 'Type', 'Title', 'SemanticScholar ID']))
#
# 		print('Errors')
# 		print(tabulate(errors, headers=['Key', 'Type', 'Title', 'Error']))
#
# 	return new, errors














