import ipaddress
import re
from urllib.parse import urlsplit

import tldextract


_suffixes = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)
FINAL_ACTION_PATTERNS = re.compile(
    r'\b(place\s+(?:order|booking)|confirm\s+(?:order|purchase|booking|payment)|buy(?:\s+now)?|pay(?:\s+now)?|'
    r'order\s+now|book(?:\s+now)?|send|subscribe|delete|cancel|'
    r'tilaa|maksa|vahvista\s+(?:tilaus|varaus|maksu)|osta|lähetä|poista|peruuta|'
    r'beställ|betala|bekräfta|köp|skicka|prenumerera|radera|avboka|'
    r'telli|maksa|kinnita|osta|saada|kustuta|tühista|'
    r'bestellen|bezahlen|zahlungspflichtig|kaufen|buchen|senden|abonnieren|löschen|stornieren)\b', re.I,
)
CARD_FIELDS = re.compile(
    r'card\s*(?:number|no\.?|expiry|expiration)|credit\s*card|debit\s*card|'
    r'\b(?:cvc|cvv|expiry|expiration\s*date|korttinumero|turvakoodi|kortnummer|kaardinumber|kartennummer)\b', re.I,
)


def is_final_control(name, url, role, patterns=''):
    if FINAL_ACTION_PATTERNS.search(name) or (patterns and re.search(patterns, name, re.I)):
        return True
    return role == 'submit' and bool(re.search(r'checkout|payment|pay', urlsplit(url).path, re.I))


def is_card_field(attrs):
    autocomplete = attrs.get('autocomplete', '').lower().split()
    return any(value.startswith('cc-') for value in autocomplete) or bool(CARD_FIELDS.search(
        ' '.join(str(attrs.get(key, '')) for key in ('name', 'label', 'placeholder', 'id'))
    ))


def secret_allowed(page_url, request_site):
    try:
        return urlsplit(page_url).scheme == 'https' and site_of(page_url) == request_site
    except ValueError:
        return False


def check_url(url: str) -> str:
    if not isinstance(url, str) or re.search(r'[\s\x00-\x1f\x7f\\]', url):
        raise ValueError('Use an HTTP or HTTPS URL with a public hostname')
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
        port = parsed.port
    except (ValueError, UnicodeError):
        raise ValueError('Invalid site URL') from None
    if parsed.scheme not in {'http', 'https'} or not host or parsed.username is not None or parsed.password is not None:
        raise ValueError('Use an HTTP or HTTPS URL without credentials')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid site port')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('Private hostnames are not allowed')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('IP addresses are not allowed')
    if len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
        raise ValueError('Invalid site hostname')
    extracted = _suffixes(host)
    if not extracted.domain or not extracted.suffix:
        raise ValueError('Use a hostname with a registrable domain')
    return host


def site_of(url: str) -> str:
    return _suffixes(check_url(url)).top_domain_under_public_suffix


def cookie_domain_allowed(domain: str, host: str) -> bool:
    if not isinstance(domain, str):
        return False
    domain = domain.removeprefix('.').lower()
    try:
        if check_url('https://' + domain) != domain:
            return False
    except ValueError:
        return False
    return (domain == host or host.endswith('.' + domain)) and bool(
        _suffixes(domain).domain and _suffixes(domain).suffix
    ) and _suffixes(domain).top_domain_under_public_suffix == _suffixes(host).top_domain_under_public_suffix
