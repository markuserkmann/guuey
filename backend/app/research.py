import os
import re
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


def _sources(payload: dict, *, text_limit: int) -> list[dict[str, str]]:
    sources = []
    for item in payload["results"][:10]:
        if not isinstance(item, dict):
            continue
        try:
            url = HttpUrl(item["url"])
        except (KeyError, ValueError, ValidationError):
            continue
        highlights = item.get("highlights")
        evidence = "\n".join(part for part in highlights if isinstance(part, str)) if isinstance(highlights, list) else ""
        if not evidence and isinstance(item.get("text"), str):
            evidence = item["text"]
        if not evidence.strip():
            continue
        sources.append({"title": str(item.get("title") or url.host), "url": str(url), "text": evidence[:text_limit]})
    return sources


def search(query: str) -> list[dict[str, str]]:
    payload = _request("search", {"query": query, "numResults": 10, "contents": {"highlights": True}})
    sources = _sources(payload, text_limit=4000)
    if not sources:
        raise SearchUnavailable("Exa did not return readable search results for this request.")
    return sources


def extract_pages(urls: list[str], *, limit: int = 3) -> list[dict[str, str]]:
    payload = _request("contents", {"urls": urls[:limit], "text": True})
    sources = _sources(payload, text_limit=12000)
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
