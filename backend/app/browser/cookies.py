import json
import math
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

from app.browser.guard import check_url, cookie_domain_allowed, site_of


MAX_BYTES = 1_048_576
MAX_COOKIES = 500


@dataclass
class ImportResult:
    cookies: list[dict]
    rejected: list[dict]


def _decode(text, site_url):
    if text.startswith(('[', '{')):
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            raise ValueError('Invalid cookie JSON') from None
        if isinstance(data, dict):
            if data.get('format') != 'guuey-cookies' or data.get('version') != 1:
                raise ValueError('Unsupported cookie export format')
            if check_url(data.get('origin', '')) != check_url(site_url):
                raise ValueError('Cookie export origin must match the site hostname')
            data = data.get('cookies')
        if not isinstance(data, list):
            raise ValueError('Cookie JSON must contain an array')
        return data
    if '\t' in text or text.startswith('#'):
        cookies = []
        for line in text.splitlines():
            if not line.strip() or (line.startswith('#') and not line.startswith('#HttpOnly_')):
                continue
            parts = line.removeprefix('#HttpOnly_').split('\t')
            if len(parts) != 7 or parts[1] not in {'TRUE', 'FALSE'} or parts[3] not in {'TRUE', 'FALSE'}:
                raise ValueError('Invalid Netscape cookie row')
            domain, _, path, secure, expires, name, value = parts
            try:
                expiry = float(expires)
            except ValueError:
                raise ValueError('Invalid cookie expiry') from None
            cookies.append(dict(name=name, value=value, domain=domain, path=path,
                                secure=secure == 'TRUE', httpOnly=line.startswith('#HttpOnly_'),
                                expires=-1 if expiry == 0 else expiry))
        return cookies
    header = text[7:].strip() if text.lower().startswith('cookie:') else text
    if '\n' in header or '\r' in header:
        raise ValueError('Paste one Cookie header')
    parsed = SimpleCookie()
    try:
        parsed.load(header)
    except CookieError:
        raise ValueError('Invalid Cookie header') from None
    if not parsed:
        raise ValueError('No cookies found')
    return [dict(name=name, value=value.value, domain=check_url(site_url), path='/', secure=True)
            for name, value in parsed.items()]


def _normalize(cookie, host):
    if not isinstance(cookie, dict):
        raise ValueError('Invalid cookie object')
    name, value = cookie.get('name'), cookie.get('value')
    if not isinstance(name, str) or not name or any(ord(c) < 33 or c in ';=' for c in name):
        raise ValueError('Invalid cookie name')
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid cookie value')
    if 'partitionKey' in cookie or cookie.get('partitioned'):
        raise ValueError('Partitioned cookies are not supported')
    domain = cookie.get('domain', host)
    if not cookie_domain_allowed(domain, host):
        raise ValueError('Cookie domain is outside the site or is a public suffix')
    domain = domain.lower()
    if cookie.get('hostOnly') is True:
        domain = domain.removeprefix('.')
    path = cookie.get('path', '/')
    if not isinstance(path, str) or not path.startswith('/') or any(ord(c) < 32 for c in path):
        raise ValueError('Invalid cookie path')
    result = dict(name=name, value=value, domain=domain, path=path)
    for flag in ('secure', 'httpOnly'):
        flag_value = cookie.get(flag, False)
        if not isinstance(flag_value, bool):
            raise ValueError('Cookie flags must be booleans')
        result[flag] = flag_value
    expiry = cookie.get('expires', cookie.get('expirationDate', -1))
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
        raise ValueError('Invalid cookie expiry')
    if cookie.get('session') is True:
        expiry = -1
    if expiry != -1 and expiry <= time.time():
        raise ValueError('Cookie has expired')
    result['expires'] = expiry
    same_site = cookie.get('sameSite', 'Lax')
    mapping = {'strict': 'Strict', 'lax': 'Lax', 'none': 'None', 'no_restriction': 'None', 'unspecified': 'Lax'}
    if not isinstance(same_site, str) or same_site.lower() not in mapping:
        raise ValueError('Invalid sameSite value')
    result['sameSite'] = mapping[same_site.lower()]
    if result['sameSite'] == 'None' and not result['secure']:
        raise ValueError('SameSite=None requires a secure cookie')
    return result


def parse_cookies(text: str, site_url: str) -> ImportResult:
    host = check_url(site_url)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Cookie input exceeds 1 MiB')
    raw = _decode(text.strip(), site_url)
    if len(raw) > MAX_COOKIES:
        raise ValueError('Cookie input exceeds 500 cookies')
    accepted, rejected = [], []
    for cookie in raw:
        try:
            accepted.append(_normalize(cookie, host))
        except ValueError as exc:
            name = cookie.get('name', '') if isinstance(cookie, dict) else ''
            rejected.append({'name': name[:256] if isinstance(name, str) else '', 'reason': str(exc)})
    return ImportResult(accepted, rejected)


def filter_storage_state(state: dict, site: str) -> dict:
    if not isinstance(state, dict) or not isinstance(state.get('cookies', []), list) or not isinstance(state.get('origins', []), list):
        raise ValueError('Invalid browser storage state')
    try:
        size = len(json.dumps(state, allow_nan=False).encode())
    except (TypeError, ValueError, RecursionError):
        raise ValueError('Invalid browser storage state') from None
    if size > MAX_BYTES or len(state.get('cookies', [])) > MAX_COOKIES or len(state.get('origins', [])) > 100:
        raise ValueError('Browser storage state exceeds its size limit')
    cookies = []
    for cookie in state.get('cookies', []):
        try:
            if not isinstance(cookie, dict) or not isinstance(cookie.get('domain'), str):
                continue
            host = check_url('https://' + cookie['domain'].removeprefix('.'))
            if site_of('https://' + host) == site:
                cookies.append(_normalize(cookie, host))
        except (KeyError, TypeError, ValueError):
            continue
    origins = []
    for origin in state.get('origins', []):
        try:
            if not isinstance(origin, dict):
                continue
            url = origin['origin']
            if not isinstance(url, str):
                continue
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.path or parsed.query or parsed.fragment or site_of(url) != site:
                continue
            items = origin['localStorage']
            if not isinstance(items, list) or len(items) > 1000:
                raise ValueError('Invalid local storage')
            if any(not isinstance(item, dict) or set(item) != {'name', 'value'}
                   or not isinstance(item['name'], str) or not isinstance(item['value'], str) for item in items):
                raise ValueError('Invalid local storage')
            origins.append({'origin': url, 'localStorage': items})
        except (KeyError, TypeError, ValueError):
            continue
    return {'cookies': cookies, 'origins': origins}
