import sys, os, shutil
from typing import List, Dict, Tuple, Union, Optional, Callable
from pathlib import Path
import omnifig as fig
from tqdm import tqdm
from datetime import datetime, timezone
from tabulate import tabulate
from collections import OrderedDict

import re
import fitz
from urllib.parse import urlparse, quote
import requests
import pdfkit
import PyPDF2
from fuzzywuzzy import fuzz
from wordcloud import WordCloud, STOPWORDS

from .util import create_note, create_file, create_url, get_now, Script_Manager


class Item_Feature(fig.Configurable):
	@property
	def feature_name(self):
		raise NotImplementedError
	
	def get_zotero_kwargs(self):
		return {}
	
	def extract(self, manager, item, get_children=None):
		raise NotImplementedError


class Paper_Feature(Item_Feature):
	def __init__(self, paper_types='conferencePaper || journalArticle || preprint', **kwargs):
		super().__init__(**kwargs)
		if paper_types is not None and not isinstance(paper_types, str):
			paper_types = ' || '.join(paper_types)
		self.paper_types = paper_types
	
	def get_zotero_kwargs(self):
		kwargs = super().get_zotero_kwargs()
		if self.paper_types is not None:
			kwargs['itemType'] = self.paper_types
		return kwargs


@fig.component('url-fixer')
class Default_URL_Fixer(Item_Feature):
	def __init__(self, update_existing=False, **kwargs):
		super().__init__(**kwargs)
		self.update_existing = update_existing

	@property
	def feature_name(self):
		return 'url'
	
	def create_url(self, item):
		data = item['data']
		
		url = None
		if data['itemType'] == 'film' and data['extra'].startswith('IMDb'):
			url = 'https://www.imdb.com/title/{}/'.format(data['extra'].split('\n')[0].split('ID: ')[-1])
		if data['itemType'] == 'book' and len(data.get('ISBN', '')):
			url = 'https://isbnsearch.org/isbn/{}'.format(data['ISBN'].replace('-', ''))
		
		return url
	
	def extract(self, manager, item, get_children=None):
		current = item['data']['url']
		if current == '' or self.update_existing:
			url = self.create_url(item)
			if url is not None and url != current:
				manager.add_update(item, msg=url)
				item['data']['url'] = url
				return url
		manager.add_failed(item, msg=f'Unchanged: "{current}"')


@fig.component('google-scholar')
class Google_Scholar(Paper_Feature):
	def __init__(self, attachment_name='Google Scholar', **kwargs):
		super().__init__(**kwargs)
		self.attachment_name = attachment_name
		self.timestamp = get_now()

	@property
	def feature_name(self):
		return 'googlescholar'
	
	google_scholar_url_base = 'https://scholar.google.com/scholar?as_q={url_title}'
	
	def extract(self, manager, item, get_children=None):
		title = item['data'].get('title')
		
		if title is not None:
			url = self.google_scholar_url_base.format(url_title=quote(title))
			new = create_url(self.attachment_name, url, parentItem=item['key'], accessDate=self.timestamp)
			manager.add_new(new, msg=f'Using {url}')
			manager.add_update(item, msg=f'Using {url}')
		else:
			manager.add_failed(item, msg='No title')


@fig.component('semantic-scholar')
class Semantic_Scholar(Paper_Feature):
	def __init__(self, match_ratio=92, attachment_name='Semantic Scholar', **kwargs):
		super().__init__(**kwargs)
		self.match_ratio = match_ratio
		self.attachment_name = attachment_name
		self.timestamp = get_now()
	
	@property
	def feature_name(self):
		return 'semanticscholar'  # TODO: fix brand
	
	query_url = 'http://api.semanticscholar.org/graph/v1/paper/search?query={}'
	
	def title_to_query(self, title):
		fixed = re.sub(r'[^a-zA-Z0-9 :-]', '', title)
		fixed = fixed.replace('-', ' ').replace(' ', '+')
		return quote(fixed).replace('%2B', '+')
	
	def call_home(self, url):
		out = requests.get(url).json()
		return out
	
	def format_result(self, ssid):
		return f'https://api.semanticscholar.org/{ssid}' if len(ssid) else ssid
	
	# return f'https://www.semanticscholar.org/paper/{ssid}' if len(ssid) else ssid
	
	def find(self, item, dry_run=False):
		title = item['data']['title']
		clean = self.title_to_query(title)
		url = self.query_url.format(clean)
		
		if dry_run:
			return url
		
		out = self.call_home(url)
		
		for res in out.get('data', []):
			if fuzz.ratio(res.get('title', ''), title) >= self.match_ratio:
				return self.format_result(res.get('paperId', ''))
		return ''
	
	def extract(self, manager, item, get_children=None):
		url = self.find(item, dry_run=manager.dry_run)
		
		if url is not None and len(url):
			new = create_url(self.attachment_name, url, parentItem=item['key'], accessDate=self.timestamp)
			manager.add_new(new, msg=f'Found {url}')
			manager.add_update(item, msg=f'Found {url}')
		else:
			manager.add_failed(item, msg='No match found')


@fig.component('attachment-path')
class Attachment_Based(fig.Configurable):
	def __init__(self, attachment_base_root=str(Path.home() / 'OneDrive'), **kwargs):
		if attachment_base_root is not None:
			attachment_base_root = Path(attachment_base_root)
		super().__init__(**kwargs)
		self.attachment_base_root = Path(attachment_base_root)
	
	def fix_path(self, path):
		if path.startswith('attachments:'):
			path = path.replace('attachments:', '')
			path = Path(path)
			if self.attachment_base_root is not None:
				path = self.attachment_base_root / path
		return path


class Attachment_Feature(Attachment_Based):
	def __init__(self, feature_title, **kwargs):
		# if feature_title is None:
		# 	feature_title = A.pull('feature-title')
		super().__init__(**kwargs)
		self.feature_title = feature_title
	
	@property
	def feature_name(self):
		raise NotImplementedError
	# return self._feature_name
	
	def extract(self, items: List[Dict], get_parent: Callable, manager: Script_Manager):
		raise NotImplementedError
	

class PDF_Feature(Attachment_Feature):
	
	@staticmethod
	def extract_text(path):
		pdf = fitz.open(path)
		full_text = []
		for n in range(pdf.page_count):
			full_text.append(pdf.get_page_text(n))
		return full_text
	
	@classmethod
	def extract_transcript(cls, path):
		full_text = cls.extract_text(path)
		transcript = '\n'.join(full_text)
		return transcript


class CodeExtractor(Attachment_Feature):
	@staticmethod
	def code_urls_from_path(path):
		return []


@fig.component('github-extractor')
class GithubExtractor(CodeExtractor, PDF_Feature):
	def __init__(self, feature_title='Code Links', **kwargs):
		super().__init__(feature_title=feature_title, **kwargs)

	@property
	def feature_name(self):
		return 'github'
	
	@staticmethod
	def find_urls(string):
		regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
		url = re.findall(regex, string)
		return [x[0] for x in url]
	
	
	@staticmethod
	def extract_pdf_links(path):
		PDF = PyPDF2.PdfReader(str(path))
		pages = PDF.pages
		key = '/Annots'
		uri = '/URI'
		ank = '/A'
		
		urls = []
		
		for page in pages:
			pageSliced = page  # PDF.getPage(page)
			# pageObject = pageSliced.getObject()
			pageObject = pageSliced.get_object()
			if key in pageObject.keys():
				ann = pageObject[key]
				for a in ann:
					u = a.get_object()
					if ank in u and uri in u[ank].keys():
						#                 print(u[ank][uri])
						urls.append(u[ank][uri])
		
		return urls
	
	
	@classmethod
	def extract_urls(cls, path):
		path = Path(path)
		transcript = cls.extract_transcript(path)
		
		urls = cls.extract_pdf_links(path) + cls.find_urls(transcript)
		urls = [(url if url.startswith('http') else 'http://' + url) for url in urls]
		return urls
	
	
	@staticmethod
	def select_code_urls(urls):
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
	
	
	@classmethod
	def code_urls_from_path(cls, path):
		urls = cls.extract_urls(path)
		return cls.select_code_urls(urls)
	
	
	def extract(self, items, get_parent, manager):
		srcs = [src for src in items if 'path' in src['data']]
		assert len(srcs), 'No sources found'
		urls = [url for src in srcs for url in self.code_urls_from_path(self.fix_path(src['data']['path']))]
		urls = list(OrderedDict.fromkeys(urls))
		
		if len(urls):
		
			code = [f'<p><a href="{link}" rel="noopener noreferrer nofollow">{link}</a></p>' for link in urls]
			
			lines = [f'<p>{self.feature_title}</p>', *code]
			note = create_note('\n'.join(lines), parentItem=items[-1]['data']['parentItem'])
			
			links = "\n".join(urls)
			msg = f'{len(urls)} code links (from {len(srcs)} sources)\n{links}'
			manager.add_new(note, msg=msg)
			manager.add_update(*items, msg=msg)
			return note
		
		manager.add_failed(*items, msg='No code links found')



@fig.component('wordcloud')
class WordcloudMaker(PDF_Feature, fig.Configurable):
	def __init__(self, wordcloud_root=str(Path.home() / 'OneDrive/Papers/wordclouds'),
	             height=400, width=800, max_words=50, min_font_size=10, min_word_length=3,
	             background_color='black', colormap='Pastel2',
	             use_stopwords=True, extra_stopwords=(),
	             feature_title='Wordcloud', **kwargs):
		wordcloud_root = Path(wordcloud_root)
		if not wordcloud_root.exists():
			os.makedirs(str(wordcloud_root))
			
		stopwords = set(extra_stopwords)
		if use_stopwords:
			stopwords = {*stopwords, *self.EXPANDED_STOPWORDS}
		
		super().__init__(feature_title=feature_title, **kwargs)
		
		self.size = height, width
		self.max_words = max_words
		self.min_font_size = min_font_size
		self.min_word_length = min_word_length
		self.background_color = background_color
		self.colormap = colormap
		self.stopwords = stopwords
		# self.extension = ext
		self.wordcloud_root = wordcloud_root
		self.timestamp = get_now()
		

	@property
	def feature_name(self):
		return 'wordcloud'
	
	gh_stopwords = ["0o", "0s", "3a", "3b", "3d", "6b", "6o", "a", "A", "a1", "a2", "a3", "a4", "ab", "able", "about",
	                "above", "abst", "ac", "accordance", "according", "accordingly", "across", "act", "actually", "ad",
	                "added", "adj", "ae", "af", "affected", "affecting", "after", "afterwards", "ag", "again",
	                "against", "ah", "ain", "aj", "al", "all", "allow", "allows", "almost", "alone", "along", "already",
	                "also", "although", "always", "am", "among", "amongst", "amoungst", "amount", "an", "and",
	                "announce", "another", "any", "anybody", "anyhow", "anymore", "anyone", "anyway", "anyways",
	                "anywhere", "ao", "ap", "apart", "apparently", "appreciate", "approximately", "ar", "are", "aren",
	                "arent", "arise", "around", "as", "aside", "ask", "asking", "at", "au", "auth", "av", "available",
	                "aw", "away", "awfully", "ax", "ay", "az", "b", "B", "b1", "b2", "b3", "ba", "back", "bc", "bd",
	                "be", "became", "been", "before", "beforehand", "beginnings", "behind", "below", "beside",
	                "besides", "best", "between", "beyond", "bi", "bill", "biol", "bj", "bk", "bl", "bn", "both",
	                "bottom", "bp", "br", "brief", "briefly", "bs", "bt", "bu", "but", "bx", "by", "c", "C", "c1", "c2",
	                "c3", "ca", "call", "came", "can", "cannot", "cant", "cc", "cd", "ce", "certain", "certainly", "cf",
	                "cg", "ch", "ci", "cit", "cj", "cl", "clearly", "cm", "cn", "co", "com", "come", "comes", "con",
	                "concerning", "consequently", "consider", "considering", "could", "couldn", "couldnt", "course",
	                "cp", "cq", "cr", "cry", "cs", "ct", "cu", "cv", "cx", "cy", "cz", "d", "D", "d2", "da", "date",
	                "dc", "dd", "de", "definitely", "describe", "described", "despite", "detail", "df", "di", "did",
	                "didn", "dj", "dk", "dl", "do", "does", "doesn", "doing", "don", "done", "down", "downwards", "dp",
	                "dr", "ds", "dt", "du", "due", "during", "dx", "dy", "e", "E", "e2", "e3", "ea", "each", "ec", "ed",
	                "edu", "ee", "ef", "eg", "ei", "eight", "eighty", "either", "ej", "el", "eleven", "else",
	                "elsewhere", "em", "en", "end", "ending", "enough", "entirely", "eo", "ep", "eq", "er", "es",
	                "especially", "est", "et", "et-al", "etc", "eu", "ev", "even", "ever", "every", "everybody",
	                "everyone", "everything", "everywhere", "ex", "exactly", "example", "except", "ey", "f", "F", "f2",
	                "fa", "far", "fc", "few", "ff", "fi", "fifteen", "fifth", "fify", "fill", "find", "fire", "five",
	                "fix", "fj", "fl", "fn", "fo", "followed", "following", "follows", "for", "former", "formerly",
	                "forth", "forty", "found", "four", "fr", "from", "front", "fs", "ft", "fu", "full", "further",
	                "furthermore", "fy", "g", "G", "ga", "gave", "ge", "get", "gets", "getting", "gi", "give", "given",
	                "gives", "giving", "gj", "gl", "go", "goes", "going", "gone", "got", "gotten", "gr", "greetings",
	                "gs", "gy", "h", "H", "h2", "h3", "had", "hadn", "happens", "hardly", "has", "hasn", "hasnt",
	                "have", "haven", "having", "he", "hed", "hello", "help", "hence", "here", "hereafter", "hereby",
	                "herein", "heres", "hereupon", "hes", "hh", "hi", "hid", "hither", "hj", "ho", "hopefully", "how",
	                "howbeit", "however", "hr", "hs", "http", "hu", "hundred", "hy", "i2", "i3", "i4", "i6", "i7", "i8",
	                "ia", "ib", "ibid", "ic", "id", "ie", "if", "ig", "ignored", "ih", "ii", "ij", "il", "im",
	                "immediately", "in", "inasmuch", "inc", "indeed", "index", "indicate", "indicated", "indicates",
	                "information", "inner", "insofar", "instead", "interest", "into", "inward", "io", "ip", "iq", "ir",
	                "is", "isn", "it", "itd", "its", "iv", "ix", "iy", "iz", "j", "J", "jj", "jr", "js", "jt", "ju",
	                "just", "k", "K", "ke", "keep", "keeps", "kept", "kg", "kj", "km", "ko", "l", "L", "l2", "la",
	                "largely", "last", "lately", "later", "latter", "latterly", "lb", "lc", "le", "least", "les",
	                "less", "lest", "let", "lets", "lf", "like", "liked", "likely", "line", "little", "lj", "ll", "ln",
	                "lo", "look", "looking", "looks", "los", "lr", "ls", "lt", "ltd", "m", "M", "m2", "ma", "made",
	                "mainly", "make", "makes", "many", "may", "maybe", "me", "meantime", "meanwhile", "merely", "mg",
	                "might", "mightn", "mill", "million", "mine", "miss", "ml", "mn", "mo", "more", "moreover", "most",
	                "mostly", "move", "mr", "mrs", "ms", "mt", "mu", "much", "mug", "must", "mustn", "my", "n", "N",
	                "n2", "na", "name", "namely", "nay", "nc", "nd", "ne", "near", "nearly", "necessarily", "neither",
	                "nevertheless", "new", "next", "ng", "ni", "nine", "ninety", "nj", "nl", "nn", "no", "nobody",
	                "non", "none", "nonetheless", "noone", "nor", "normally", "nos", "not", "noted", "novel", "now",
	                "nowhere", "nr", "ns", "nt", "ny", "o", "O", "oa", "ob", "obtain", "obtained", "obviously", "oc",
	                "od", "of", "off", "often", "og", "oh", "oi", "oj", "ok", "okay", "ol", "old", "om", "omitted",
	                "on", "once", "one", "ones", "only", "onto", "oo", "op", "oq", "or", "ord", "os", "ot", "otherwise",
	                "ou", "ought", "our", "out", "outside", "over", "overall", "ow", "owing", "own", "ox", "oz", "p",
	                "P", "p1", "p2", "p3", "page", "pagecount", "pages", "par", "part", "particular", "particularly",
	                "pas", "past", "pc", "pd", "pe", "per", "perhaps", "pf", "ph", "pi", "pj", "pk", "pl", "placed",
	                "please", "plus", "pm", "pn", "po", "poorly", "pp", "pq", "pr", "predominantly", "presumably",
	                "previously", "primarily", "probably", "promptly", "proud", "provides", "ps", "pt", "pu", "put",
	                "py", "q", "Q", "qj", "qu", "que", "quickly", "quite", "qv", "r", "R", "r2", "ra", "ran", "rather",
	                "rc", "rd", "re", "readily", "really", "reasonably", "recent", "recently", "ref", "refs",
	                "regarding", "regardless", "regards", "related", "relatively", "research-articl", "respectively",
	                "resulted", "resulting", "results", "rf", "rh", "ri", "right", "rj", "rl", "rm", "rn", "ro", "rq",
	                "rr", "rs", "rt", "ru", "run", "rv", "ry", "s", "S", "s2", "sa", "said", "saw", "say", "saying",
	                "says", "sc", "sd", "se", "sec", "second", "secondly", "section", "seem", "seemed", "seeming",
	                "seems", "seen", "sent", "seven", "several", "sf", "shall", "shan", "shed", "shes", "show",
	                "showed", "shown", "showns", "shows", "si", "side", "since", "sincere", "six", "sixty", "sj", "sl",
	                "slightly", "sm", "sn", "so", "some", "somehow", "somethan", "sometime", "sometimes", "somewhat",
	                "somewhere", "soon", "sorry", "sp", "specifically", "specified", "specify", "specifying", "sq",
	                "sr", "ss", "st", "still", "stop", "strongly", "sub", "substantially", "successfully", "such",
	                "sufficiently", "suggest", "sup", "sure", "sy", "sz", "t", "T", "t1", "t2", "t3", "take", "taken",
	                "taking", "tb", "tc", "td", "te", "tell", "ten", "tends", "tf", "th", "than", "thank", "thanks",
	                "thanx", "that", "thats", "the", "their", "theirs", "them", "themselves", "then", "thence", "there",
	                "thereafter", "thereby", "thered", "therefore", "therein", "thereof", "therere", "theres",
	                "thereto", "thereupon", "these", "they", "theyd", "theyre", "thickv", "thin", "think", "third",
	                "this", "thorough", "thoroughly", "those", "thou", "though", "thoughh", "thousand", "three",
	                "throug", "through", "throughout", "thru", "thus", "ti", "til", "tip", "tj", "tl", "tm", "tn", "to",
	                "together", "too", "took", "top", "toward", "towards", "tp", "tq", "tr", "tried", "tries", "truly",
	                "try", "trying", "ts", "tt", "tv", "twelve", "twenty", "twice", "two", "tx", "u", "U", "u201d",
	                "ue", "ui", "uj", "uk", "um", "un", "under", "unfortunately", "unless", "unlike", "unlikely",
	                "until", "unto", "uo", "up", "upon", "ups", "ur", "us", "used", "useful", "usefully", "usefulness",
	                "using", "usually", "ut", "v", "V", "va", "various", "vd", "ve", "very", "via", "viz", "vj", "vo",
	                "vol", "vols", "volumtype", "vq", "vs", "vt", "vu", "w", "W", "wa", "was", "wasn", "wasnt", "way",
	                "we", "wed", "welcome", "well", "well-b", "went", "were", "weren", "werent", "what", "whatever",
	                "whats", "when", "whence", "whenever", "where", "whereafter", "whereas", "whereby", "wherein",
	                "wheres", "whereupon", "wherever", "whether", "which", "while", "whim", "whither", "who", "whod",
	                "whoever", "whole", "whom", "whomever", "whos", "whose", "why", "wi", "widely", "with", "within",
	                "without", "wo", "won", "wonder", "wont", "would", "wouldn", "wouldnt", "www", "x", "X", "x1", "x2",
	                "x3", "xf", "xi", "xj", "xk", "xl", "xn", "xo", "xs", "xt", "xv", "xx", "y", "Y", "y2", "yes",
	                "yet", "yj", "yl", "you", "youd", "your", "youre", "yours", "yr", "ys", "yt", "z", "Z", "zero",
	                "zi", "zz"]
	EXPANDED_STOPWORDS = {'arXiv', 'preprint', 'arxiv', 'proceedings', 'advances', 'model', 'sample', 'samples',
	                      'images',
	                      'using', 'image', 'set', 'models', 'journal', 'international', 'conference', 'article',
	                      'method', 'outcome', 'data', 'section', 'pages',
	                      'ICLR', 'ICML', 'ICCV', 'CVPR', 'NIPS', 'sciencemag', 'researchgate', 'journal', 'conference',
	                      'IEEE', 'Max Planck', 'thus',  # 'institute',
	                      'really', 'think', 'thing', 'know', 'need', 'going', 'maybe', 'want', 'something',
	                      'will', 'make', 'may', 'another', 'much', 'many',
	                      'mathbf', 'mathbb', 'nabla', 'nabla_', 'mathrm',
	                      'Neural Information Processing Systems', 'use', 'Figure', 'Fig', 'Table', 'Equation',
	                      *STOPWORDS, *gh_stopwords}
	
	
	def generate_from_path(self, *paths, **kwargs):
		transcript = '\n'.join(self.extract_transcript(path) for path in paths)
		return self.generate(transcript, **kwargs)
	
	
	def generate(self, text, **kwargs):
		return WordCloud(width=self.size[1], height=self.size[0], max_words=self.max_words,
		                 min_font_size=self.min_font_size, background_color=self.background_color,
		                 colormap=self.colormap, stopwords=self.stopwords, min_word_length=self.min_word_length,
		                 **kwargs).generate(text)
	
	
	def extract(self, items, get_parent, manager):
		srcs = [Path(self.fix_path(src['data']['path'])) for src in items if 'path' in src['data']]
		assert len(srcs), 'No sources found'
		
		dest = self.wordcloud_root / f'{srcs[-1].stem}.jpg'
		
		wc = self.generate_from_path(*srcs)
		words = sorted(wc.words_.keys(), key=lambda w: wc.words_[w], reverse=True)
		
		if manager.is_real_run:
			wc.to_image().save(str(dest), "JPEG")
			
		linked_file = create_file(self.feature_title, str(dest), contentType='image/jpg',
		                          parentItem=items[-1]['data']['parentItem'],
		                          note=';'.join(words), accessDate=self.timestamp)
		
		msg = f'Top 3: {"; ".join(words[:3])}...'
		manager.add_new(linked_file, msg=msg)
		manager.add_update(*items, msg=msg)
		return wc
		


















