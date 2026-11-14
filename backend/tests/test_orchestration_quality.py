from datetime import UTC, datetime, timedelta

import pytest

from app.orchestration import orchestrator
from app.orchestration.contracts import ResearchContract, SearchPlan, SearchQuery


@pytest.mark.parametrize("retrieval,age,reuse", [("live", 10, True), ("page", 10, False), ("live", 901, False)])
def test_service_followups_reuse_only_recent_live_pages(monkeypatch, retrieval, age, reuse):
    source = {"url": "https://sauna.example/booking", "title": "Sauna", "text": "Price €25",
              "retrieval": retrieval, "retrieved_at": (datetime.now(UTC) - timedelta(seconds=age)).isoformat()}
    plan = SearchPlan(contract=ResearchContract(activity="service"),
                      queries=[SearchQuery(query="sauna price", objective="Find operator prices")])
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (plan, None))
    monkeypatch.setattr(orchestrator, "search", lambda *args, **kwargs: [source])
    fetched = []

    def extract(urls, **kwargs):
        fetched.extend(urls)
        assert kwargs["fresh"]
        return [{**source, "text": "Price €30", "retrieval": "live"}]

    monkeypatch.setattr(orchestrator, "extract_pages", extract)
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: ("answer", {}))
    result = orchestrator.run_turn("Sauna prices", [], search_requested=True, seed_sources=[source])
    assert bool(fetched) is not reuse
    assert result.sources[0]["text"] == ("Price €25" if reuse else "Price €30")
    assert result.debug["retrieval"]["reused_pages"] == int(reuse)


def test_search_results_after_tenth_are_not_discarded(monkeypatch):
    plan = SearchPlan(contract=ResearchContract(), queries=[SearchQuery(query="holidays", objective="Find destinations")])
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (plan, None))
    sources = [{"url": f"https://travel.example/{i}", "title": str(i), "text": "Evidence"} for i in range(15)]
    monkeypatch.setattr(orchestrator, "search", lambda *args, **kwargs: sources)
    monkeypatch.setattr(orchestrator, "extract_pages", lambda urls, **kwargs: [s for s in sources if s["url"] in urls])
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: ("answer", {}))
    result = orchestrator.run_turn("Holiday destinations", [], search_requested=True)
    assert len(result.sources) == 15


def test_planner_rejects_party_size_as_result_count(monkeypatch):
    from dataclasses import dataclass
    from types import SimpleNamespace
    from app.orchestration import planner

    @dataclass
    class Usage:
        input_tokens: int = 1

    plan = SearchPlan(contract=ResearchContract(activity="service", requested_count=2, count_quote="2 adults"),
                      queries=[SearchQuery(query="Löyly booking", objective="Check slot")])
    monkeypatch.setattr(planner, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(planner, "call_model", lambda *args, **kwargs: SimpleNamespace(output=plan, usage=Usage()))
    result, _ = planner.plan_searches("Can 2 adults book Löyly on Saturday?")
    assert result.contract.requested_count is None
    assert result.contract.count_quote is None


@pytest.mark.parametrize("anchor", ["null", "None", "", "N/A"])
def test_empty_model_anchor_does_not_create_proximity_requirement(anchor):
    contract = ResearchContract(activity="product", destination="Finland", anchor=anchor)
    assert contract.anchor is None
    assert not any(r.id == "proximity" for r in contract.requirements)


def test_saved_null_anchor_does_not_keep_generated_proximity_check():
    contract = ResearchContract.model_validate({"activity": "product", "anchor": "null",
        "requirements": [{"id": "proximity", "kind": "location", "description": "Near null"}]})
    assert not any(r.id == "proximity" for r in contract.requirements)


def test_shopping_refinement_follows_seller_policy_subpages(monkeypatch):
    contract = ResearchContract(activity="product", destination="Finland", country_code="FI")
    page = {"url": "https://shop.example/product/a", "title": "Laptop A", "text": "Laptop A",
            "retrieval": "live", "retrieved_at": datetime.now(UTC).isoformat()}
    policy = {"url": "https://shop.example/shipping", "title": "Shipping", "text": "We ship to Finland.",
              "retrieval": "page"}
    plan = SearchPlan(contract=contract, queries=[SearchQuery(query="Laptop A delivery", objective="Find shipping policy")])
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (plan, None))
    monkeypatch.setattr(orchestrator, "search", lambda *args, **kwargs: [page])
    fetched = []

    def extract(urls, **kwargs):
        fetched.append((urls, kwargs))
        return [policy]

    monkeypatch.setattr(orchestrator, "extract_pages", extract)
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: ("answer", {}))
    prior = {"findings": [{"name": "Laptop A", "source_url": page['url'], "checks": [
        {"requirement_id": "delivery", "requirement": "Delivery to Finland", "status": "unknown", "statement": "", "evidence": []}]}]}
    result = orchestrator.run_turn("Find laptops in Finland", [], search_requested=True, contract=contract,
                                   seed_sources=[page], prior_answer=prior)
    assert len(fetched) == 1
    assert fetched[0][0] == ['https://shop.example/']
    assert 'delivery to Finland' in fetched[0][1]['subpage_targets']
    assert policy in result.sources
    assert result.debug['retrieval']['policy_pages'] == 1


def test_followed_offer_refresh_replaces_discovery_body(monkeypatch):
    offer_url = 'https://shop.example/product/a'
    lead = {'url': 'https://shop.example/laptops', 'title': 'Laptops',
            'text': f'[Laptop A]({offer_url})', 'retrieval': 'page'}
    stale = {'url': offer_url, 'title': 'Laptop A', 'text': 'Discovery only', 'retrieval': 'discovery'}
    fresh = {**stale, 'text': 'Laptop A costs €599. In stock.', 'retrieval': 'live',
             'retrieved_at': datetime.now(UTC).isoformat()}
    contract = ResearchContract(activity='product')
    plan = SearchPlan(contract=contract, queries=[SearchQuery(query='Laptop A offer', objective='Find actual offer')])
    monkeypatch.setattr(orchestrator, 'plan_searches', lambda *args: (plan, None))
    monkeypatch.setattr(orchestrator, 'search', lambda *args, **kwargs: [stale])
    monkeypatch.setattr(orchestrator, 'offer_links', lambda *args: [offer_url])
    calls = []

    def extract(urls, **kwargs):
        calls.append(urls)
        return [fresh]

    monkeypatch.setattr(orchestrator, 'extract_pages', extract)
    monkeypatch.setattr(orchestrator, 'generate_reply', lambda *args, **kwargs: ('answer', {}))
    prior = {'findings': [], 'discovery_leads': [{'name': 'Laptop A', 'source_url': lead['url']}]}
    result = orchestrator.run_turn('Find laptops', [], search_requested=True, contract=contract,
                                   seed_sources=[lead], prior_answer=prior)
    selected = next(source for source in result.sources if source['url'] == offer_url)
    assert selected == fresh
    assert result.debug['retrieval']['offer_pages'] == 1


def test_live_crawl_is_one_batch_of_offers_and_keeps_cached_failures(monkeypatch):
    task = ResearchContract(activity='product', subject='laptop', destination='Finland', country_code='FI')
    pages = [{'url': f'https://seller.fi/product/{i}', 'title': f'Laptop {i}',
              'text': f'Laptop {i}: 16 GB RAM, €649.', 'retrieval': 'page'} for i in range(12)]
    pages += [{'url': 'https://www.trustpilot.com/review/seller.fi', 'title': 'Laptop reviews', 'text': 'Laptop', 'retrieval': 'page'},
              {'url': 'https://seller.it/product/a', 'title': 'Laptop A', 'text': 'Laptop A €649', 'retrieval': 'page'}]
    monkeypatch.setattr(orchestrator, 'plan_searches', lambda *args: (SearchPlan(contract=task, queries=[SearchQuery(query='laptops', objective='offers')]), None))
    monkeypatch.setattr(orchestrator, 'search', lambda *args, **kwargs: pages)
    calls = []
    def extract(urls, **kwargs):
        calls.append((urls, kwargs))
        raise orchestrator.SearchUnavailable('Live crawl failed')
    monkeypatch.setattr(orchestrator, 'extract_pages', extract)
    monkeypatch.setattr(orchestrator, 'generate_reply', lambda *args, **kwargs: ('answer', {}))
    state = {}
    first = orchestrator.run_turn('Find laptops', [], search_requested=True, retrieval_state=state)
    assert len(calls) == 2 and len(calls[0][0]) == 8
    assert all(url.startswith('https://seller.fi/product/') for url in calls[0][0])
    assert calls[1][0] == ['https://seller.fi/']
    assert 'shipping and delivery policy' in calls[1][1]['subpage_targets']
    assert first.sources[0]['retrieval'] == 'page'
    orchestrator.run_turn('Find laptops', [], search_requested=True, retrieval_state=state)
    assert len(calls) == 2
