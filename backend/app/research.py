import os
import re
from collections import deque
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from pydantic import HttpUrl, ValidationError


class SearchUnavailable(Exception):
    pass


def _request(endpoint: str, body: dict) -> dict:
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        raise SearchUnavailable("Add EXA_API_KEY to the backend environment to search the web.")
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=4)) as client:
            response = client.post(
                f"https://api.exa.ai/{endpoint}",
                headers={"x-api-key": api_key},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("Invalid Exa response")
            return payload
    except (httpx.HTTPError, ValueError) as exc:
        raise SearchUnavailable("Exa retrieval is temporarily unavailable.") from exc


def source_domain(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def live_source(source: dict) -> bool:
    if source.get("retrieval") != "live":
        return False
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(source["retrieved_at"])).total_seconds()
        return 0 <= age < 300
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
    if len(text) <= limit:
        return text
    # Keep nearby model/variant context with prices and delivery details, even below long menus.
    terms = set(re.findall(r"\w{3,}", focus.casefold()))
    windows = [(start, text[start:start + 1800]) for start in range(0, min(len(text), 60000), 1500)]
    def score(window):
        body = window[1].casefold()
        return len(terms.intersection(re.findall(r"\w{3,}", body))) + 3 * bool(re.search(r"€|\beur\b|stock|varasto|saatav|toimit|shipping|delivery", body))
    chosen = {0: windows[0][1]}
    size = len(windows[0][1])
    for start, body in sorted(windows[1:], key=score, reverse=True):
        if size + len(body) + 8 > limit:
            continue
        chosen[start] = body
        size += len(body) + 8
    return "\n[…]\n".join(body for _, body in sorted(chosen.items()))


def _sources(payload: dict, *, text_limit: int, focus: str = "", retrieval: str = "discovery") -> list[dict[str, str]]:
    sources = []
    failed = {entry.get("id") for entry in payload.get("statuses", [])
              if isinstance(entry, dict) and (entry.get("error") or entry.get("status") in {"error", "failed"})}
    for item in payload["results"]:
        if not isinstance(item, dict) or item.get("url") in failed or item.get("error"):
            continue
        try:
            url = HttpUrl(item["url"])
        except (KeyError, ValueError, ValidationError):
            continue
        highlights = item.get("highlights")
        evidence = item.get("text") if isinstance(item.get("text"), str) else ""
        if isinstance(highlights, list):
            evidence += "\n" + "\n".join(part for part in highlights if isinstance(part, str))
        if not evidence.strip():
            continue
        sources.append({"title": str(item.get("title") or url.host), "url": str(url),
                        "text": evidence_windows(evidence.strip(), text_limit, focus),
                        "retrieval": retrieval, "retrieved_at": datetime.now(UTC).isoformat()})
    return sources


def search(query: str, *, objective: str = "Find evidence relevant to this request.", country: str | None = None) -> list[dict[str, str]]:
    body = {"query": query, "objective": objective, "contents": {"highlights": True}}
    if country:
        body["userLocation"] = country
    payload = _request("search", body)
    sources = _sources(payload, text_limit=4000)
    if not sources:
        raise SearchUnavailable("Exa did not return readable search results for this request.")
    return sources


def extract_pages(urls: list[str], *, limit: int = 3, fresh: bool = False, focus: str = "") -> list[dict[str, str]]:
    body = {"urls": urls[:limit], "text": True}
    if fresh:
        body["maxAgeHours"] = 0
    payload = _request("contents", body)
    sources = _sources(payload, text_limit=12000, focus=focus, retrieval="live" if fresh else "page")
    if not sources:
        raise SearchUnavailable("Exa could not retrieve readable content from the requested pages.")
    return sources


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
