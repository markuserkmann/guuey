from app.orchestration import orchestrator
from app.orchestration.contracts import Requirement, ResearchContract, SearchPlan, SearchQuery
from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, RequirementCheck
from app.orchestration.refinement import fallback_gap_queries, prioritize_refinement_queries, scope_known_seller_query


def scenario():
    contract = ResearchContract(activity="research", destination="Estonia", requested_count=5, requirements=[
        Requirement(id="hotel", description="Named hotel with location in this destination", scope="related"),
        Requirement(id="value", description="Budget-friendly hotel features", strength="preference", scope="related"),
    ])
    candidates = [("Tallinn Old Town", "Rija Old Town hotel"), ("Tartu", "Villa Margaretha Boutique Hotell"),
                  ("Pärnu Beach", "Hotel Vesiroos"), ("Lahemaa National Park", "Sagadi Manor Hotel"), ("Narva", "Hotell Narva")]
    findings, sources = [], []
    for index, (city, hotel) in enumerate(candidates):
        url = f"https://booking.com/hotel/ee/{index}.html"
        text = f"{city} is in Estonia. {hotel} is a hotel located in {city}."
        sources.append({"url": url, "title": city, "text": text, "retrieval": "page"})
        findings.append(Finding(name=city, source_url=url, checks=[
            RequirementCheck(requirement_id="hotel", requirement="Named hotel with location in this destination",
                status="met" if index == 0 else "unknown", statement=f"{hotel} is a hotel located in {city}." if index == 0 else "",
                scope="related", related_entity=hotel,
                evidence=[Evidence(source_url=url, quote=f"{hotel} is a hotel located in {city}.")] if index == 0 else []),
            RequirementCheck(requirement_id="value", requirement="Budget-friendly hotel features", status="unknown",
                statement="", strength="preference", scope="related", related_entity=hotel, evidence=[]),
        ]))
    planned = [SearchQuery(query=f"{hotel} location budget rates", objective=f"Research {hotel}", gap=f"{hotel} location and budget")
               for index, (_, hotel) in enumerate(candidates) if index != 1]
    return contract, GroundedAnswer(findings=findings), sources, planned


def test_missing_hard_tartu_hotel_replaces_tallinn_preference_within_four_queries():
    contract, answer, sources, planned = scenario()
    selected = prioritize_refinement_queries(planned, answer.model_dump(), contract, set(), sources)
    assert len(selected) == 4
    texts = [query.query for query in selected]
    assert any("Villa Margaretha" in text and "Tartu" in text for text in texts)
    assert not any("Rija" in text for text in texts)
    assert all(any(hotel in text for text in texts) for hotel in ("Hotel Vesiroos", "Sagadi Manor Hotel", "Hotell Narva"))
    assert any("Named hotel" in query.objective for query in selected if "Vesiroos" in query.query)


def test_fallback_hard_gaps_precede_preference_only_candidate():
    contract, answer, sources, _ = scenario()
    # Suppress broad count discovery to isolate candidate gap priority.
    contract.requested_count = None
    queries = fallback_gap_queries(answer.model_dump(), contract, set(), sources)
    assert len(queries) == 4
    assert any("Tartu" in query.query for query in queries)
    assert not any("Rija" in query.query for query in queries)


def test_orchestrator_executes_four_prioritized_searches_not_a_fifth(monkeypatch):
    contract, answer, sources, planned = scenario()
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (SearchPlan(contract=contract, queries=planned), {}))
    calls = []

    def search(query, **kwargs):
        calls.append(query)
        return sources

    monkeypatch.setattr(orchestrator, "search", search)
    monkeypatch.setattr(orchestrator, "extract_pages", lambda *args, **kwargs: sources)
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: ("answer", {}))
    result = orchestrator.run_turn("Five destinations with hotels", [], search_requested=True, contract=contract,
                                  seed_sources=sources, prior_answer=answer.model_dump())
    assert len(calls) == len(result.debug["executed_queries"]) == 4
    assert any("Tartu" in query for query in calls)
    assert not any("Rija" in query for query in calls)


def seller_scenario():
    contract = ResearchContract(shopping=True, requirements=[Requirement(id="cpu", description="Exact CPU")])
    finding = Finding(name="Laptop A1234", source_url="https://www.retailer.fi/product/a1234", source_kind="product", checks=[
        RequirementCheck(requirement_id="stock", requirement="Currently in stock", status="unknown", statement="", evidence=[]),
        RequirementCheck(requirement_id="cpu", requirement="Exact CPU", status="unknown", statement="", evidence=[]),
    ])
    sources = [{"url": finding.source_url, "title": finding.name, "text": "Laptop A1234 specifications", "retrieval": "page"}]
    return contract, finding, sources


def test_exact_known_offer_stock_query_uses_only_its_actual_seller_domain():
    contract, finding, sources = seller_scenario()
    query = SearchQuery(query="Laptop A1234 stock availability", objective="Check the seller stock",
                        category="news", start_published_date="2026-01-01", search_type="lite")
    scoped = scope_known_seller_query(query, finding, contract, sources)
    assert scoped.include_domains == ["retailer.fi"]
    assert scoped.category == query.category
    assert scoped.start_published_date == query.start_published_date
    assert scoped.search_type == "lite"
    assert scoped.filter_quote is None
    assert query.include_domains == []


def test_discovery_replacement_specs_and_unretained_offers_are_not_domain_restricted():
    contract, finding, sources = seller_scenario()
    for text in ("Alternative Laptop A1234 stock at other sellers", "Laptop A1234 exact CPU manufacturer specification"):
        query = SearchQuery(query=text, objective="Find evidence")
        assert scope_known_seller_query(query, finding, contract, sources).include_domains == []
    query = SearchQuery(query="Laptop A1234 stock availability", objective="Find evidence")
    assert scope_known_seller_query(query, finding, contract, []).include_domains == []


def test_existing_user_domain_restrictions_are_preserved():
    contract, finding, sources = seller_scenario()
    for restrictions in ({"include_domains": ["approved.fi"]}, {"exclude_domains": ["retailer.fi"]}):
        query = SearchQuery(query="Laptop A1234 stock", objective="Find evidence", **restrictions)
        assert scope_known_seller_query(query, finding, contract, sources) == query
