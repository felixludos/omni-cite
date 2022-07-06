from datetime import datetime, timezone
from tabulate import tabulate

import re
import urllib.parse
import requests
from fuzzywuzzy import fuzz


def get_now():
	return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()
	# return datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def print_new_errors(new, errors):
	
	new = [[item['data']['key'], item['data']['itemType'], item['data']['title'], msg]
	       for item, msg in sorted(new, key=lambda x: (x[0]['data']['itemType'], x[0]['data']['title']))]
	errors = [[item['data']['key'], item['data']['itemType'], item['data']['title'], msg]
	          for item, msg in sorted(errors, key=lambda x: (x[0]['data']['itemType'], x[0]['data']['title']))]
	
	print('New')
	print(tabulate(new, headers=['Key', 'Type', 'Title', 'New']))
	
	print('Errors')
	print(tabulate(errors, headers=['Key', 'Type', 'Title', 'Error']))
	
	pass


