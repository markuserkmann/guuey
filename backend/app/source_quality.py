"""Classify retrieved documents before they can establish an answer fact."""
import re
from unicodedata import normalize, combining
from urllib.parse import parse_qs, urlsplit


QUALITY_RANK = {"official": 0, "retailer": 0, "secondary": 1, "aggregator": 2,
                "ugc": 3, "seo": 4, "off_target": 5}
AUTHORITIES = {
    "legal": {"finlex.fi", "kkv.fi", "kuluttajariita.fi", "ym.fi"},
    "benefits": {"kela.fi", "tyomarkkinatori.fi", "te-palvelut.fi", "tyj.fi"},
    "flight": {"eur-lex.europa.eu", "europa.eu", "traficom.fi", "finnair.com"},
    "business": {"ytj.fi", "prh.fi"},
    "errand": {"hsy.fi", "espoo.fi", "helsinki.fi", "kierratys.info", "serkierratys.fi"},
}
SECONDARY = {"visitestonia.com", "visittallinn.ee", "visitfinland.com", "myhelsinki.fi",
             "yle.fi", "suomenvuokranantajat.fi", "vuokralaiset.fi", "hel.fi"}
AGGREGATORS = {"booking.com", "hotels.com", "expedia.com", "hotelscombined.com", "kayak.com",
               "hostelworld.com", "trivago.com", "hinta.fi", "hintaopas.fi", "priceinfo.fi",
               "finder.fi", "asiakastieto.fi"}
UGC = {"reddit.com", "tripadvisor.com", "trustpilot.com", "tori.fi", "facebook.com"}
CLAIM_AGENCIES = {"airhelp.com", "flightright.com", "skyrefund.com"}
OPERATORS = {"loylyhelsinki.fi", "hektorstay.com"}
RETAILERS = {"gigantti.fi", "power.fi", "verkkokauppa.com", "proshop.fi", "jimms.fi",
             "multitronic.fi", "karkkainen.com"}


def host(url):
    try:
        return (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    except ValueError:
        return ""


def in_domains(domain, domains):
    return any(domain == item or domain.endswith("." + item) for item in domains)


def task_kind(contract):
    if not contract:
        return "research"
    text = " ".join(filter(None, [contract.original_request, contract.objective, contract.subject,
                                  *[r.description for r in contract.requirements]])).casefold()
    if contract.shopping:
        return "shopping"
    for kind, pattern in (
        ("flight", r"\b(?:flight|airline|finnair|261/2004)\b"),
        ("benefits", r"\b(?:kela|benefits?|allowance|unemployment|etuudet|tuki)\b"),
        ("legal", r"\b(?:landlord|rent|lease|legal|law|vuokra)\b"),
        ("business", r"\b(?:business id|y-tunnus|registered address|ceo|registry)\b"),
        ("errand", r"\b(?:recycl\w*|microwave|drop off|sortti)\b"),
        ("hotels", r"\b(?:hotels?|holiday|destinations?|accommodation)\b"),
    ):
        if re.search(pattern, text):
            return kind
    return "services" if contract.activity == "service" else "research"


def individual_offer(url):
    path = urlsplit(url).path
    return bool(re.search(r"/(?:products?|tuote|p|dp|item|recommerce/forsale/item|kannettava-tietokone)/"
                          r"|/c/\d+[^/]*/[^/]+|/\d[^/]*[-/]", path, re.I))


def property_page(url):
    return bool(re.search(r"/(?:hotel|ho\d+|Hotel-Information)(?:/|[-.])", urlsplit(url).path, re.I))


def classify(url, title, text, contract=None):
    domain = host(url)
    if not domain or urlsplit(url).scheme not in {"http", "https"}:
        return "off_target", ["Invalid source URL"]
    path = urlsplit(url).path.casefold()
    kind = task_kind(contract)
    request = " ".join(filter(None, [getattr(contract, 'original_request', ''),
                                     getattr(contract, 'jurisdiction', ''), getattr(contract, 'destination', '')]))
    finnish = getattr(contract, 'country_code', None) == 'FI' or re.search(r"\b(?:Finland|Finnish|Helsinki|Espoo|Suomi)\b", request, re.I)
    foreign_authority = domain.endswith('.gov') or re.search(r"\.(?:gov|govt)\.(?:uk|us|au|nz|ca)$", domain)
    if foreign_authority and (kind in {'legal', 'benefits'} and finnish or kind == 'flight' and re.search(r'\bEU\b', request)):
        return "off_target", ["Authority for a different jurisdiction"]
    if (re.search(r"/(?:search|s|budget/city|place|category|categories)(?:/|$)|/theme/cheap-and-budget|/hotellid/[^/]+/[^/]*-d\d+", path)
            or (set(parse_qs(urlsplit(url).query)) & {"q", "query", "search"} and not path.startswith("/oar/"))):
        return "seo", ["Search or category listing, discovery only"]
    if re.search(r"\b(?:listed here for|cheapest hotels in|\d+ (?:best|cheap|budget) hotels in)\b", title + " " + text[:500], re.I):
        return "seo", ["Keyword-stuffed listing"]
    if in_domains(domain, {"hostelworld.com"}) and not re.search(r"/(?:hostels?/p|hosteldetails)/", path):
        return "seo", ["Hostel destination listing"]
    if kind == "shopping":
        if re.search(r"/(?:category|categories|collections|c)(?:/|$)", path) and not individual_offer(url):
            return "seo", ["Product category, discovery only"]
        if re.search(r"\b(?:best laptops|buying guide|laptop reviews|hands.on|review)\b", title, re.I) or re.search(r"/(?:news|articles?|editorial|reviews?|buying-guides?|best-laptops)(?:/|$)", path):
            return "off_target", ["Review or roundup cannot establish an offer"]
    if in_domains(domain, CLAIM_AGENCIES) and kind == "flight":
        return "seo", ["Claim agency marketing"]
    if in_domains(domain, UGC):
        return "ugc", ["User-generated or marketplace source"]
    if in_domains(domain, AGGREGATORS):
        if kind == "hotels" and property_page(url) and in_domains(domain, {"booking.com", "hotels.com", "expedia.com"}):
            return "official", ["Individual property page on booking provider"]
        return "aggregator", ["Comparison, directory or listing"]
    if in_domains(domain, AUTHORITIES.get(kind, set())) or re.search(r"(?:^|\.)(?:gov|govt)(?:\.[a-z]{2})?$|\.gov\.[a-z]{2}$", domain):
        return "official", ["Authority or official registry"]
    if in_domains(domain, SECONDARY):
        return "secondary", ["Tourism board, association or reputable publication"]
    selling = bool(re.search(r"\b(?:in stock|varastossa|add to cart|lisää ostoskoriin)\b", text, re.I)
                   and re.search(r"€\s*\d|\d\s*€|EUR\s*\d", text, re.I)
                   and title.strip() and title.casefold() in text.casefold() and path.strip("/"))
    if kind == "shopping" and (individual_offer(url) or selling or (in_domains(domain, RETAILERS) and path.endswith(".html"))):
        return "retailer", ["Individual seller offer"]
    if kind == "shopping" and re.search(r"deliver|shipping|toimitus|terms|ehdot", path):
        return "retailer", ["Seller policy page"]
    if kind in {"legal", "benefits"}:
        return "secondary", ["Not an identified authority; cannot confirm legal or benefit rules"]
    if in_domains(domain, OPERATORS) or (domain == "varaus.asio.fi" and "/loyly" in path):
        return "official", ["Official operator or its booking provider"]
    def compact(value):
        return re.sub(r"[^\w]", "", "".join(c for c in normalize("NFKD", value.casefold()) if not combining(c)))
    brand = compact(domain.split(".")[-2] if "." in domain else domain)
    compact_title = compact(title)
    entities = getattr(contract, "exact_entities", []) if contract else []
    brand_match = len(brand) >= 4 and (brand in compact_title or any(
        brand in compact(entity) for entity in entities))
    publisher = re.search(r"guide|news|blog|magazine|directory|tripadvisor|travelguide", brand) or re.search(
        r"/(?:news|articles?|blog|guides?)(?:/|$)|\b(?:review|best of|top \d+|roundup)\b", path + " " + title, re.I)
    if brand_match and not publisher:
        return "official", ["Operator or company identity matches its domain"]
    return "secondary", ["Independent source; primary ownership not established"]


def source_class(source, contract=None):
    return classify(source.get("url", ""), source.get("title", ""), source.get("text", ""), contract)[0]
