from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app import research
from app.model_calls import model_call_events


def page(**fields):
    return {"url": "https://example.fi/sauna", "title": "Sauna", "text": "Private hire €120.", **fields}


def test_stable_ids_survive_order_and_observation_changes():
    first = research._sources({"results": [page(), page(url="https://other.fi/")]}, text_limit=1000)
    second = research._sources({"results": [page(url="https://other.fi/"), page()]}, text_limit=1000)
    assert first[0]["source_id"] == second[1]["source_id"]
    assert first[0]["source_id"] != first[1]["source_id"]
    assert research.stable_source_id("https://EXAMPLE.fi:443/sauna#prices") == first[0]["source_id"]
    assert research.stable_source_id("https://example.fi/sauna?package=private") != first[0]["source_id"]


def test_publication_and_observation_do_not_invent_crawl_time():
    source = research._sources({"results": [page(publishedDate="2024-01-02")]}, text_limit=1000)[0]
    assert source["published_at"] == "2024-01-02"
    assert source["observed_at"] == source["retrieved_at"]
    assert "crawled_at" not in source


def test_stale_crawl_is_not_fresh_despite_recent_observation():
    old = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    source = research._sources({"results": [page(crawledDate=old)]}, text_limit=1000, retrieval="live")[0]
    assert source["crawled_at"] == old
    assert not research.live_source(source)


def test_live_crawl_remains_fresh_for_the_run_not_just_five_minutes():
    observed = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
    source = page(retrieval='live', observed_at=observed, crawled_at=observed)
    assert research.live_source(source)
    assert not research.live_source(source, max_observed_age=300)
    source['crawled_at'] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    assert not research.live_source(source)


def test_search_full_text_is_a_cached_page_but_highlights_are_discovery():
    sources = research._sources({'results': [page(), page(url='https://example.fi/b', text='', highlights=['Snippet.'])]})
    assert [source['retrieval'] for source in sources] == ['page', 'discovery']


def test_status_url_normalization_does_not_promote_cached_page():
    payload = {"results": [page(url="https://example.fi/")],
               "statuses": [{"id": "https://EXAMPLE.fi:443", "source": "cached", "status": "success"}]}
    source = research._sources(payload, text_limit=1000, retrieval="live")[0]
    assert source["retrieval"] == "page"
    assert not research.live_source(source)


@pytest.mark.parametrize("payload", [None, [], {}, {"results": None}, {"results": "bad"}])
def test_malformed_sources_return_no_evidence(payload):
    assert research._sources(payload, text_limit=1000) == []


def test_malformed_optional_fields_do_not_crash_valid_results():
    payload = {"results": [None, {}, page(id=[], publishedDate="bad", crawledDate="2024-01-01")],
               "statuses": [{"id": [], "status": "error"}, None]}
    source = research._sources(payload, text_limit=1000)[0]
    assert "published_at" not in source
    assert "crawled_at" not in source
    assert source["url"] == "https://example.fi/sauna"
    assert len(research._sources({"results": [page()], "statuses": None}, text_limit=1000)) == 1


def test_highlight_boundaries_cannot_become_one_quote():
    from app.orchestration.passages import find_passage
    source = research._sources({"results": [page(text="", highlights=["Sauna A.", "Sauna B costs €10."])]}, text_limit=1000)[0]
    assert find_passage("Sauna A. Sauna B costs €10.", source["text"]) is None


def test_default_search_has_no_hard_filters_or_freshness(monkeypatch):
    calls = []
    monkeypatch.setattr(research, "_request", lambda endpoint, body: calls.append(body) or {"results": [page()]})
    research.search("Helsinki sauna", objective="Find public prices")
    assert calls == [{"query": "Helsinki sauna", "objective": "Find public prices",
                      "contents": {"text": {"verbosity": "full", "linkStyle": "full"}}}]


def test_explicit_filters_use_supported_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(research, "_request", lambda endpoint, body: calls.append(body) or {"results": [page()]})
    research.search("Sauna opening", country="FI", category="news", search_type="lite",
                    include_domains=["example.fi/news"], exclude_domains=["archive.example.fi"],
                    start_published_date="2026-01-01", end_published_date="2026-02-01T00:00:00Z")
    body = calls[0]
    assert body["type"] == "lite"
    assert body["includeDomains"] == ["example.fi/news"]
    assert body["excludeDomains"] == ["archive.example.fi"]
    assert body["startPublishedDate"] == "2026-01-01T00:00:00+00:00"
    assert body["category"] == "news"
    assert body["userLocation"] == "FI"
    assert body["contents"] == {"text": {"verbosity": "full", "linkStyle": "full"}}


@pytest.mark.parametrize("settings", [
    {"category": "people", "start_published_date": "2026-01-01"},
    {"category": "company", "end_published_date": "2026-01-01"},
    {"category": "people", "exclude_domains": ["example.org"]},
    {"category": "github"}, {"search_type": "fast"},
    {"start_published_date": "tomorrow"},
    {"start_published_date": "2026-02-01", "end_published_date": "2026-01-01"},
    {"include_domains": "example.org"},
])
def test_invalid_filters_fail_before_network(monkeypatch, settings):
    monkeypatch.setattr(research, "_request", lambda *args: pytest.fail("Unexpected network request"))
    with pytest.raises(ValueError):
        research.search("query", **settings)


def mock_http(monkeypatch, payload, status=200):
    monkeypatch.setenv("EXA_API_KEY", "private-exa-key")
    monkeypatch.delenv("EXA_USAGE_DATABASE", raising=False)
    response = httpx.Response(status, json=payload, request=httpx.Request("POST", "https://api.exa.ai/search"))
    monkeypatch.setattr(research.httpx, "Client", lambda **kwargs: nullcontext(SimpleNamespace(post=lambda *args, **kwargs: response)))


def test_call_diagnostics_preserve_cost_and_timing_without_secrets(monkeypatch):
    mock_http(monkeypatch, {"results": [page()], "requestId": "request-private-exa-key",
                           "searchTime": 300, "costDollars": {"total": 0.007, "search": {"neural": 0.005}, "secret": "private-exa-key"}})
    events = []
    token = model_call_events.set(events.append)
    try:
        source = research.search("query")[0]
    finally:
        model_call_events.reset(token)
    assert events[0]["status"] == "started"
    assert events[-1]["status"] == "completed"
    assert events[0]["call_id"] == events[-1]["call_id"]
    assert events[-1]["cost_dollars"] == {"total": 0.007, "search": {"neural": 0.005}}
    assert events[-1]["search_time_ms"] == 300
    assert events[-1]["elapsed_seconds"] >= 0
    assert "private-exa-key" not in str(events)
    assert source["request_started_at"] <= source["observed_at"]


@pytest.mark.parametrize("payload,status", [([], 200), ({"results": {}}, 200), ({"error": "private-exa-key"}, 429)])
def test_failed_requests_emit_safe_diagnostics(monkeypatch, payload, status):
    mock_http(monkeypatch, payload, status)
    events = []
    token = model_call_events.set(events.append)
    try:
        with pytest.raises(research.SearchUnavailable):
            research.search("query")
    finally:
        model_call_events.reset(token)
    assert events[-1]["status"] == "failed"
    assert events[-1]["elapsed_seconds"] >= 0
    assert "private-exa-key" not in str(events)
