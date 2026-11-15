import json
import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from app.browser.guard import check_url


def navigation_key(url):
    check_url(url)
    parsed = urlsplit(url)
    return (parsed.scheme, parsed.netloc.lower(), parsed.path or '/', tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))))


def request_urls(text):
    return re.findall(r'https?://[^\s<>"\)]+', text or '')


def page_urls(text, base):
    for value in re.findall(r'^\s*- /url:\s*(.+)$', text, re.M):
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        yield urljoin(base, value)
