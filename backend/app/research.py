import hashlib
import json
import math
import os
import re
import time
from collections import deque
from datetime import UTC, datetime
from unicodedata import normalize
from urllib.parse import unquote, urljoin, urlsplit
from uuid import uuid4

import httpx
from markdown_it import MarkdownIt
from pydantic import HttpUrl, ValidationError
from app.secrets import redact


class SearchUnavailable(Exception):
    pass


def _request(endpoint: str, body: dict) -> dict:
    from app.model_calls import model_call_events
    from app.retrieval_budget import reserve, record
    from app.research_modes import remaining_seconds, ResearchDeadline

    observer = model_call_events.get()
    started = time.monotonic()
    request_started_at = datetime.now(UTC).isoformat()
    event = {"stage": "retrieval_request", "provider": "exa", "endpoint": endpoint, "call_id": uuid4().hex}
    if endpoint == "contents":
        event.update(urls=body.get("urls", []), livecrawl=body.get("maxAgeHours") == 0)
    if observer:
        observer({**event, "status": "started", "request_started_at": request_started_at})
    api_key = os.getenv("EXA_API_KEY")
    try:
        if not api_key:
            raise SearchUnavailable("Add EXA_API_KEY to the backend environment to search the web.")
        ticket = reserve(endpoint)
        remaining = remaining_seconds()
        if remaining < 1:
            raise ResearchDeadline('The research deadline has been reached.')
        with httpx.Client(timeout=httpx.Timeout(min(30, remaining), connect=min(4, remaining))) as client:
            response = client.post(
                f"https://api.exa.ai/{endpoint}",
                headers={"x-api-key": api_key},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid Exa response")
            record(ticket, payload)
            if not isinstance(payload.get("results"), list):
                raise ValueError("Invalid Exa response")
            observed_at = datetime.now(UTC).isoformat()
            payload["_request_meta"] = {"request_started_at": request_started_at, "observed_at": observed_at}
            if observer:
                details = {"result_count": len(payload["results"]), "observed_at": observed_at}
                request_id = payload.get("requestId")
                if isinstance(request_id, str):
                    details["request_id"] = redact(request_id)[:200]
                costs = _numeric_costs(payload.get("costDollars"))
                if costs:
                    details["cost_dollars"] = costs
                search_time = payload.get("searchTime")
                if _nonnegative_number(search_time):
                    details["search_time_ms"] = search_time
                observer({**event, **details, "status": "completed", "elapsed_seconds": round(time.monotonic() - started, 3)})
            return payload
    except (httpx.HTTPError, ValueError, SearchUnavailable) as exc:
        if observer:
            failure = {**event, "status": "failed", "elapsed_seconds": round(time.monotonic() - started, 3),
                       "error_type": type(exc).__name__}
            if isinstance(exc, httpx.HTTPStatusError):
                failure["http_status"] = exc.response.status_code
            observer(failure)
        if isinstance(exc, SearchUnavailable):
            raise
        raise SearchUnavailable("Exa retrieval is temporarily unavailable.") from exc


def _nonnegative_number(value) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _numeric_costs(value) -> dict:
    if not isinstance(value, dict):
        return {}
    allowed = {"total", "search", "neural", "keyword", "contents", "text", "highlights", "summary"}
    return {key: item if _nonnegative_number(item) else _numeric_costs(item)
            for key, item in value.items() if key in allowed and (_nonnegative_number(item) or isinstance(item, dict))}


def stable_source_id(url: str) -> str:
    canonical = urlsplit(str(HttpUrl(url)))._replace(fragment="").geturl()
    return "S_" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def source_domain(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def live_source(source: dict, max_observed_age: float = 900) -> bool:
    if source.get("retrieval") != "live":
        return False
    try:
        now = datetime.now(UTC)
        observed = source.get("observed_at", source.get("retrieved_at"))
        observed = datetime.fromisoformat(observed)
        if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() < max_observed_age:
            return False
        if source.get("crawled_at"):
            crawled = datetime.fromisoformat(source["crawled_at"])
            return crawled.tzinfo is not None and abs((observed - crawled).total_seconds()) <= 300
        return True
    except (KeyError, TypeError, ValueError):
        return False


def diverse_sources(sources: list[dict], limit: int) -> list[dict]:
    groups = {}
    seen = set()
    for source in sources:
        if source["url"] not in seen:
            groups.setdefault(source_domain(source["url"]), deque()).append(source)
            seen.add(source["url"])
    selected = []
    while any(groups.values()) and len(selected) < limit:
        for group in groups.values():
            if group and len(selected) < limit:
                selected.append(group.popleft())
    return selected


def evidence_windows(text: str, limit: int, focus: str = "") -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit < 64:
        return text[:limit]
    terms = set(re.findall(r"\w{2,}", focus.casefold()))
    width = min(1800, limit)
    step = max(1, width * 5 // 6)
    windows = [(start, min(start + width, len(text))) for start in range(0, len(text), step)]

    def score(window):
        body = text[window[0]:window[1]].casefold()
        return 3 * len(terms.intersection(re.findall(r"\w{2,}", body))) + bool(re.search(r"€|\beur\b|stock|varasto|saatav|toimit|shipping|delivery", body))

    separator = "\n\n[…]\n\n"
    # Preserve the page identity when there is also room for a relevant passage.
    chosen = [windows[0]] if limit >= 2 * width + len(separator) else []
    for candidate in sorted(windows, key=score, reverse=True):
        merged = []
        for start, end in sorted([*chosen, candidate]):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        size = sum(end - start for start, end in merged) + len(separator) * (len(merged) - 1)
        if size <= limit:
            chosen = merged
    return separator.join(text[start:end] for start, end in chosen)


def _sources(payload: dict, *, text_limit: int | None = None, focus: str = "", retrieval: str = "discovery") -> list[dict[str, str]]:
    sources = []
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return sources
    raw_statuses = payload.get("statuses")
    statuses = {entry["id"]: entry for entry in raw_statuses
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)} if isinstance(raw_statuses, list) else {}
    for identifier, status in list(statuses.items()):
        try:
            statuses.setdefault(str(HttpUrl(identifier)), status)
        except (ValueError, ValidationError):
            pass
    metadata = payload.get("_request_meta")
    metadata = metadata if isinstance(metadata, dict) else {}
    observed_at = metadata.get("observed_at") or datetime.now(UTC).isoformat()
    documents = [(item, False) for item in payload["results"]]
    for item in payload["results"]:
        if isinstance(item, dict) and isinstance(item.get("subpages"), list):
            documents.extend((child, True) for child in item["subpages"])
    seen_urls = set()
    for item, is_subpage in documents:
        if not isinstance(item, dict) or item.get("error"):
            continue
        identifier = item.get("id") if isinstance(item.get("id"), str) else None
        raw_url = item.get("url") if isinstance(item.get("url"), str) else None
        try:
            url = HttpUrl(item["url"])
            if url.username or url.password:
                continue
        except (KeyError, ValueError, ValidationError):
            continue
        status = statuses.get(identifier, statuses.get(raw_url, statuses.get(str(url), {})))
        if status.get("error") or status.get("status") in {"error", "failed"}:
            continue
        if str(url) in seen_urls:
            continue
        highlights = item.get("highlights")
        evidence = item.get("text") if isinstance(item.get("text"), str) else ""
        if not evidence.strip() and isinstance(highlights, list):
            evidence = "\n\n".join(part for part in highlights if isinstance(part, str))
        if not evidence.strip() and retrieval != "discovery":
            continue
        seen_urls.add(str(url))
        effective_retrieval = retrieval
        if retrieval == "discovery" and isinstance(item.get("text"), str) and item["text"].strip():
            effective_retrieval = "page"
        if retrieval == "live" and (status.get("source") == "cached" or (is_subpage and status.get("source") != "crawled")):
            effective_retrieval = "page"
        sources.append({"source_id": stable_source_id(str(url)), "title": str(item.get("title") or url.host), "url": str(url),
                        "text": evidence.strip() if text_limit is None else evidence_windows(evidence.strip(), text_limit, focus),
                        "retrieval": effective_retrieval,
                        "observed_at": observed_at, "retrieved_at": observed_at})
        if isinstance(metadata.get("request_started_at"), str):
            sources[-1]["request_started_at"] = metadata["request_started_at"]
        for field, provider_fields in (("published_at", ("publishedDate",)),
                                       ("crawled_at", ("crawledDate", "lastCrawledDate"))):
            for provider_field in provider_fields:
                value = item.get(provider_field)
                if isinstance(value, str):
                    try:
                        parsed = datetime.fromisoformat(value)
                    except ValueError:
                        continue
                    # A publication date may be date-only; freshness needs an explicit timezone.
                    if field == "crawled_at" and parsed.tzinfo is None:
                        continue
                    sources[-1][field] = value
                    break
        if isinstance(status.get("source"), str):
            sources[-1]["crawl_source"] = status["source"]
        if isinstance(item.get("id"), str):
            try:
                requested_url = str(HttpUrl(item["id"]))
            except (ValueError, ValidationError):
                continue
            if requested_url != str(url):
                sources[-1]["requested_url"] = requested_url
    return sources


def search(query: str, *, objective: str = "Find evidence relevant to this request.", country: str | None = None,
           include_domains: list[str] | None = None, exclude_domains: list[str] | None = None,
           start_published_date: str | None = None, end_published_date: str | None = None,
           category: str | None = None, search_type: str = "auto") -> list[dict[str, str]]:
    body = {"query": query, "objective": objective,
            "contents": {"text": {"verbosity": "full", "linkStyle": "full"}}}
    if search_type not in {"auto", "lite"}:
        raise ValueError("Search type must be auto or lite.")
    if search_type != "auto":
        body["type"] = search_type
    if category:
        if category not in {"company", "publication", "news", "personal site", "financial report", "people"}:
            raise ValueError("Unsupported Exa category.")
        if category in {"company", "people"} and (start_published_date or end_published_date):
            raise ValueError("Publication date filters are unavailable for company and people searches.")
        if category == "people" and exclude_domains:
            raise ValueError("Excluded domains are unavailable for people searches.")
        body["category"] = category
    for field, domains in (("includeDomains", include_domains), ("excludeDomains", exclude_domains)):
        if domains:
            if not isinstance(domains, list) or len(domains) > 1200 or any(not isinstance(domain, str) or not domain.strip() for domain in domains):
                raise ValueError("Domain filters require a list of up to 1200 nonempty domains or paths.")
            body[field] = list(dict.fromkeys(domain.strip() for domain in domains))
    dates = []
    for field, value in (("startPublishedDate", start_published_date), ("endPublishedDate", end_published_date)):
        if value:
            try:
                date = datetime.fromisoformat(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("Publication bounds must be ISO 8601 dates or timestamps.") from exc
            date = date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC)
            body[field] = date.isoformat()
            dates.append(date)
    if len(dates) == 2 and dates[0] > dates[1]:
        raise ValueError("Publication start must not be after publication end.")
    if country:
        body["userLocation"] = country
    payload = _request("search", body)
    sources = _sources(payload)
    if not sources:
        raise SearchUnavailable("Exa did not return readable search results for this request.")
    from app.source_cache import PersistentCache, POLICY_TTL, cache_policy_sources, page_ttl
    cache_policy_sources(sources)
    cache = PersistentCache("pages")
    for source in sources:
        # Cached search text must not intercept a product's subsequent live crawl.
        if source.get('retrieval') == 'page' and page_ttl(source['url']) == POLICY_TTL:
            cache.put(json.dumps([source['url'], []]), [source], POLICY_TTL)
    return sources


def extract_pages(urls: list[str], *, limit: int = 3, fresh: bool = False, focus: str = "",
                  subpage_targets: list[str] | None = None) -> list[dict[str, str]]:
    from app.source_cache import PersistentCache, cache_policy_sources, cached_sources, page_ttl

    body = {"urls": list(dict.fromkeys(urls[:limit])), "text": {"verbosity": "full", "linkStyle": "full"}}
    if subpage_targets:
        if not isinstance(subpage_targets, list) or len(subpage_targets) > 100 or any(
            not isinstance(target, str) or not 1 <= len(target.strip()) <= 100 for target in subpage_targets
        ):
            raise ValueError("Subpage targets require up to 100 nonempty terms of at most 100 characters.")
        body["subpages"] = 2
        body["subpageTarget"] = list(dict.fromkeys(target.strip() for target in subpage_targets))
    if fresh:
        body["maxAgeHours"] = 0
        body["livecrawlTimeout"] = 5000
    cache = PersistentCache("pages")
    def cache_key(url):
        return json.dumps([str(HttpUrl(url)), sorted(body.get("subpageTarget", []))])
    hits, missing = [], []
    for url in body["urls"]:
        saved = cache.get(cache_key(url))
        if isinstance(saved, list) and saved:
            hits.extend(cached_sources(saved))
        else:
            missing.append(url)
    if not missing:
        return list({source['url']: source for source in hits}.values())
    body['urls'] = missing
    payload = _request("contents", body)
    sources = _sources(payload, retrieval="live" if fresh else "page")
    if not sources and not hits:
        raise SearchUnavailable("Exa could not retrieve readable content from the requested pages.")
    cache_policy_sources(sources)
    for url in missing:
        direct = [source for source in sources if source['url'] == url or source.get('requested_url') == url]
        if not direct:
            continue
        # Subpage results belong to a single parent only when that request had one URL.
        bundle = sources if len(missing) == 1 else direct
        policy_request = any(re.search(r'shipping|delivery|toimitus', target, re.I) for target in body.get('subpageTarget', []))
        cache.put(cache_key(url), bundle, page_ttl(url, policy_request=policy_request))
    return list({source['url']: source for source in [*hits, *sources]}.values())


def urls_in_message(message: str) -> list[str]:
    urls = []
    for match in re.findall(r'https?://[^\s<>"\']+', message):
        candidate = match.rstrip(".,;:!?)]}")
        try:
            parsed = HttpUrl(candidate)
            parts = urlsplit(str(parsed))
            if parts.username or parts.password:
                continue
        except (ValueError, ValidationError):
            continue
        if str(parsed) not in urls:
            urls.append(str(parsed))
    return urls[:3]


def offer_links(source: dict, candidate_name: str, limit: int = 2) -> list[str]:
    if limit <= 0 or not candidate_name.strip():
        return []

    def folded(value):
        return normalize("NFKC", value).casefold()

    def words(value):
        return re.findall(r"[^\W_]+", folded(value))

    expected = words(candidate_name)
    if len(expected) < 2:
        return []
    codes = [word for word in expected if len(word) >= 6 and re.fullmatch(
        r"(?:[a-z][a-z0-9]*\d[a-z0-9]*|\d{1,3}[a-z]{2,}[a-z0-9]*\d[a-z0-9]*)", word)
        and not re.match(r"(?:lpddr|ddr|ryzen|core|rtx|gtx|usb|win)\d", word)]

    def contains_name(label):
        remaining = iter(words(label))
        return all(any(actual == wanted for actual in remaining) for wanted in expected)

    named_links, coded_links = [], []
    for block in MarkdownIt("commonmark").parse(source.get("text", "")):
        label, href = [], None
        for token in block.children or []:
            if token.type == "link_open":
                href, label = token.attrGet("href"), []
            elif token.type == "link_close" and href is not None:
                anchor = " ".join(label)
                anchor = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", anchor)
                anchor = re.sub(r"https?://[^\s<>]+", "", anchor)
                try:
                    url = HttpUrl(urljoin(source["url"], href))
                except (KeyError, ValueError, ValidationError):
                    href = None
                    continue
                path = unquote(urlsplit(str(url)).path).casefold()
                same_page = urlsplit(str(url))._replace(fragment="").geturl() == urlsplit(source["url"])._replace(fragment="").geturl()
                disallowed = re.search(r"/(?:search|categories?|collections?|brands?|reviews?|support|manuals?)(?:/|$)|/product/list(?:/|$)", path)
                media = re.search(r"\.(?:png|jpe?g|gif|webp|svg|pdf|zip|mp4)$", path)
                code_match = bool(codes) and all(re.search(
                    r"(?<![a-z0-9])" + re.escape(code) + r"(?![a-z0-9])", folded(anchor) + " " + path
                ) for code in codes)
                if (not url.username and not url.password and not disallowed and not media
                        and (contains_name(anchor) or code_match) and not same_page):
                    links = named_links if contains_name(anchor) else coded_links
                    if str(url) not in links:
                        links.append(str(url))
                href = None
            elif href is not None and token.type in {"text", "code_inline", "image"}:
                label.append(token.content)
    if named_links:
        return named_links[:limit]
    return coded_links if len(coded_links) == 1 else []


def comparison_source(url: str) -> bool:
    domain = source_domain(url)
    return any(domain == host or domain.endswith("." + host)
               for host in {"priceinfo.fi", "hinta.fi", "hintaopas.fi", "idealo.de", "pricespy.co.uk"})


def category_source(url: str) -> bool:
    path = urlsplit(url).path.casefold()
    if source_domain(url) == "gigantti.fi":
        return "/product/" not in path
    if re.search(r"/(?:product|products)/[^/]+", path):
        return False
    return bool(re.search(r"/(?:category|categories|collections)(?:/|$)|/page[-/]\d+/?$", path))
