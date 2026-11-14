from datetime import UTC, datetime, timedelta
import json

import pytest

from app import research, source_cache
from app.source_cache import PersistentCache, OFFER_TTL, POLICY_TTL, VERDICT_TTL


def test_persistent_cache_expires_across_instances(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(source_cache.time, "time", lambda: clock[0])
    PersistentCache("verdicts").put("claim", {"supported": True}, VERDICT_TTL)
    assert PersistentCache("verdicts").get("claim") == {"supported": True}
    clock[0] += VERDICT_TTL
    assert PersistentCache("verdicts").get("claim") is None


@pytest.mark.parametrize("url,ttl", [
    ("https://shop.fi/product/laptop", OFFER_TTL),
    ("https://shop.fi/toimitusehdot", POLICY_TTL),
    ("https://finlex.fi/fi/laki/ajantasa/1995/19950481", POLICY_TTL),
    ("https://ytj.fi/company/123", POLICY_TTL),
    ("https://kela.fi/child-benefit", POLICY_TTL),
])
def test_page_cache_lifetimes_follow_document_type(url, ttl):
    assert source_cache.page_ttl(url) == ttl


def test_offer_cache_reuses_text_without_refreshing_observation(monkeypatch):
    calls = []
    url = "https://shop.fi/product/laptop"
    monkeypatch.setattr(research, "_request", lambda endpoint, body: calls.append(body) or {
        "results": [{"url": url, "title": "Laptop A", "text": "Laptop A costs €559."}]})
    cold = research.extract_pages([url], fresh=True)[0]
    warm = research.extract_pages([url], fresh=True)[0]
    assert len(calls) == 1
    assert cold["retrieval"] == "live" and research.live_source(cold)
    assert warm["retrieval"] == "cache" and not research.live_source(warm)
    assert warm["observed_at"] == cold["observed_at"]
    assert warm["text"] == cold["text"]


def test_policy_cached_by_domain_covers_multiple_offers(monkeypatch):
    url = "https://shop.fi/shipping"
    monkeypatch.setattr(research, "_request", lambda endpoint, body: {
        "results": [{"url": "https://shop.fi/", "title": "Shop", "text": "Shop", "subpages": [
            {"url": url, "title": "Shipping", "text": "We deliver to Finland."}]}]})
    research.extract_pages(["https://shop.fi/"], subpage_targets=["shipping and delivery policy"])
    pages = source_cache.policy_sources("shop.fi")
    assert len(pages) == 1 and pages[0]["url"] == url
    assert pages[0]["retrieval"] == "cache" and source_cache.usable_policy(pages[0])
    from app.orchestration.contracts import ResearchContract
    from app.orchestration.direct_actions import gap_actions
    task = ResearchContract(activity="product", destination="Finland")
    answer = {"findings": [{"name": name, "source_url": f"https://shop.fi/product/{name}",
        "checks": [{"requirement_id": "delivery", "status": "unknown"}]} for name in ("one", "two")]}
    actions, pending, _ = gap_actions(answer, task, pages)
    assert not actions
    assert len(pending) == 2


def test_old_policy_is_not_reused_even_when_observed_recently():
    page = {"url": "https://shop.fi/shipping", "text": "We deliver to Finland.",
            "observed_at": datetime.now(UTC).isoformat(),
            "crawled_at": (datetime.now(UTC) - timedelta(days=8)).isoformat()}
    assert not source_cache.usable_policy(page)


def test_cache_failure_does_not_fail_research(monkeypatch):
    monkeypatch.setenv("SOURCE_CACHE_PATH", "/dev/null/unwritable.sqlite3")
    assert PersistentCache("pages").get("page") is None
    PersistentCache("pages").put("page", {"text": "Public page"}, OFFER_TTL)


def test_official_page_discovered_in_search_is_available_to_page_cache(monkeypatch):
    calls = []
    url = "https://kela.fi/support-example"
    monkeypatch.setattr(research, "_request", lambda endpoint, body: calls.append(endpoint) or {
        "results": [{"url": url, "title": "Kela support", "text": "Eligibility depends on income."}]})
    discovered = research.search("Kela support eligibility")[0]
    cached = research.extract_pages([url])[0]
    assert calls == ["search"]
    assert cached["retrieval"] == "cache"
    assert cached["observed_at"] == discovered["observed_at"]


@pytest.mark.parametrize("retrieval,age_days,expected", [("page", 8, False), ("cache", 1, True)])
def test_delivery_grounding_expires_old_policy_and_reuses_fresh_cached_policy(retrieval, age_days, expected):
    from app.orchestration.contracts import ResearchContract
    from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, RequirementCheck, apply_contract
    offer_url, policy_url = "https://shop.fi/product/laptop", "https://shop.fi/shipping"
    quote = "We deliver orders to Finland."
    policy = {"url": policy_url, "title": "Delivery terms", "text": quote, "retrieval": retrieval,
              "observed_at": (datetime.now(UTC) - timedelta(days=age_days)).isoformat()}
    offer = {"url": offer_url, "title": "Laptop A", "text": "Laptop A", "retrieval": "live",
             "observed_at": datetime.now(UTC).isoformat()}
    answer = GroundedAnswer(findings=[Finding(name="Laptop A", source_url=offer_url, source_kind="product", checks=[
        RequirementCheck(requirement_id="delivery", requirement="Delivery", status="met", statement=quote,
                         evidence=[Evidence(source_url=policy_url, quote=quote)])])])
    task = ResearchContract(activity="product", destination="Finland")
    grounded = apply_contract(answer, [offer, policy], task)
    check = next(check for check in grounded.findings[0].checks if check.requirement_id == "delivery")
    assert (check.status == "met") is expected
    assert source_cache.delivery_source_current(policy) is expected
