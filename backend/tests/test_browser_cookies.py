import json
import time

import pytest

from app.browser.cookies import parse_cookies
from app.browser.guard import check_url, site_of


@pytest.mark.parametrize('text', [
    'Cookie: session=private; consent=yes',
    'session=private; consent=yes',
    json.dumps([{'name': 'session', 'value': 'private', 'domain': '.example.com'}, {'name': 'consent', 'value': 'yes'}]),
    json.dumps({'format': 'guuey-cookies', 'version': 1, 'origin': 'https://shop.example.com',
                'cookies': [{'name': 'session', 'value': 'private'}, {'name': 'consent', 'value': 'yes'}]}),
    '# Netscape HTTP Cookie File\n#HttpOnly_.example.com\tTRUE\t/\tTRUE\t0\tsession\tprivate\nshop.example.com\tFALSE\t/\tTRUE\t0\tconsent\tyes',
])
def test_cookie_formats(text):
    result = parse_cookies(text, 'https://shop.example.com')
    assert [(cookie['name'], cookie['value']) for cookie in result.cookies] == [('session', 'private'), ('consent', 'yes')]
    assert result.rejected == []
    assert all(cookie['expires'] == -1 for cookie in result.cookies)


def test_cookie_import_scoping():
    cookies = [{'name': 'ok', 'value': '1', 'domain': '.example.co.uk'},
               {'name': 'public', 'value': '1', 'domain': '.co.uk'},
               {'name': 'foreign', 'value': '1', 'domain': 'other.com'},
               {'name': 'sibling', 'value': '1', 'domain': 'other.example.co.uk'},
               {'name': 'expired', 'value': '1', 'expires': time.time() - 1},
               {'name': 'partitioned', 'value': '1', 'partitionKey': 'https://example.co.uk'},
               {'name': 'malformed', 'value': '1', 'domain': 123},
               {'name': 'invalid-label', 'value': '1', 'domain': '..example.co.uk'}]
    result = parse_cookies(json.dumps(cookies), 'https://shop.example.co.uk')
    assert len(result.cookies) == 1
    assert len(result.rejected) == 7
    assert site_of('https://shop.example.co.uk') == 'example.co.uk'
    assert parse_cookies('[{"name":"x","value":"1","domain":".github.io"}]', 'https://user.github.io').cookies == []


def test_cookie_normalization():
    cookies = [{'name': 'x', 'value': 'v', 'domain': '.example.com', 'hostOnly': True,
                'expirationDate': time.time() + 3600, 'sameSite': 'no_restriction', 'secure': True}]
    result = parse_cookies(json.dumps(cookies), 'https://example.com').cookies[0]
    assert result['sameSite'] == 'None'
    assert result['domain'] == 'example.com'
    assert result['expires'] > time.time()


@pytest.mark.parametrize('text', [
    '[' + ','.join(['{"name":"x","value":"v"}'] * 501) + ']',
    'a=' + 'a' * 1_048_576,
    'a=' + '€' * 400_000,
    '{broken', 'not cookies',
    '{"format":"unknown","cookies":[]}',
    '{"format":"guuey-cookies","version":1,"origin":"https://other.com","cookies":[]}',
])
def test_invalid_cookie_imports(text):
    with pytest.raises(ValueError):
        parse_cookies(text, 'https://example.com')


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'javascript:alert(1)', 'https://user:pass@example.com',
    'http://localhost', 'http://x.local', 'http://x.internal', 'http://127.0.0.1',
    'http://[::1]', 'http://2130706433', 'http://127.1', 'https://example.com:invalid',
    'https://example.com\\@other.com', 'https://com', 'https://github.io', 'https://example.com\n',
])
def test_url_policy(url):
    with pytest.raises(ValueError):
        check_url(url)
