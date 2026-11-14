from app.orchestration.contracts import Budget, Requirement, ResearchContract, SearchQuery
from app.orchestration.grounding import Evidence, Finding, GroundedAnswer, RequirementCheck
from app.orchestration.interpretation import query_key
from app.orchestration.planner import source_preferences
from app.orchestration.refinement import fallback_gap_queries
import pytest


def test_site_allowlists_become_preferences_without_narrowing_search():
    query = SearchQuery(query='site:verkkokauppa.com OR site:gigantti.fi OR site:power.fi kannettava 16GB 512GB',
                        objective="Find Finnish laptop offers under 700 EUR")
    cleaned = source_preferences(query)
    assert cleaned.query.startswith("kannettava 16GB 512GB")
    assert "site:" not in cleaned.query
    assert "OR" not in cleaned.query
    assert "Preferred sources: verkkokauppa.com, gigantti.fi, power.fi" in cleaned.query
    assert cleaned.include_domains == []
    assert query.query.startswith("site:")


def test_authorized_api_filter_survives_operator_cleanup():
    query = SearchQuery(query="site:example.fi Core processor", objective="Find CPUs",
                        include_domains=["example.fi"], filter_quote="only example.fi")
    cleaned = source_preferences(query)
    assert cleaned.query == "Core processor"
    assert cleaned.include_domains == ["example.fi"]


def test_query_without_operator_is_unchanged():
    query = SearchQuery(query="Laptops under 700 EUR with 16 GB RAM", objective="Find current prices")
    assert source_preferences(query) == query


def test_site_only_query_keeps_a_meaningful_search_objective():
    query = SearchQuery(query="site:example.fi OR site:other.fi", objective="Find laptop offers")
    assert source_preferences(query).query.startswith("Find laptop offers")


def check(identifier, status, strength="hard"):
    return RequirementCheck(requirement_id=identifier, requirement=identifier, status=status,
                            statement="", evidence=[Evidence(source_url="https://example.fi/asus", quote="Price €717")]
                            if status == "not_met" else [], strength=strength)


def test_hard_budget_failure_requests_replacement_not_more_failed_offer_specs():
    contract = ResearchContract(activity="product", subject="laptop", destination="Finland",
        original_request="Find 3 laptops under €700 with 16GB RAM and 512GB SSD", requested_count=3,
        budget=Budget(amount=700, operator="lt"),
        requirements=[Requirement(id="ram", description="16GB RAM"), Requirement(id="ssd", description="512GB SSD")])
    rejected = Finding(name="ASUS €717 offer", source_url="https://example.fi/asus", source_kind="product",
                       checks=[check("price", "not_met"), check("stock", "unknown")])
    viable = Finding(name="Lenovo €679 offer", source_url="https://example.fi/lenovo", source_kind="product",
                     checks=[check("price", "met"), check("stock", "unknown")])
    queries = fallback_gap_queries(GroundedAnswer(findings=[rejected, viable]).model_dump(), contract, set())
    assert len(queries) == 2
    assert "replacement" in queries[0].gap.casefold()
    assert all(term in queries[0].query for term in ("laptop", "Finland", "700", "16GB", "512GB"))
    assert "ASUS €717 offer" in queries[0].objective
    assert "ASUS €717 offer" not in queries[0].query
    assert "Lenovo €679 offer" in queries[1].query
    assert all(query.include_domains == [] for query in queries)
    attempted = {query_key(query) for query in queries}
    alternatives = fallback_gap_queries(GroundedAnswer(findings=[rejected, viable]).model_dump(), contract, attempted)
    assert len(alternatives) == 1 and "replacement" in alternatives[0].gap.casefold()
    assert "published location contact booking" in alternatives[0].query
    attempted.update(query_key(query) for query in alternatives)
    from app.orchestration.refinement import replacement_gap_queries
    assert not replacement_gap_queries(GroundedAnswer(findings=[rejected, viable]).model_dump(), contract, attempted)


def test_failed_preference_does_not_trigger_replacement():
    contract = ResearchContract(requirements=[Requirement(id="color", description="Prefer silver", strength="preference")])
    finding = Finding(name="Laptop", checks=[check("color", "not_met", "preference")])
    assert fallback_gap_queries(GroundedAnswer(findings=[finding]).model_dump(), contract, set()) == []


@pytest.mark.parametrize("message,count", [
    ("Find 3 laptops under €700 with 16 GB RAM and 512 GB SSD", 3),
    ("Get me top 5 holiday spots in Estonia and hotels for them", 5),
    ("Recommend five affordable restaurants in Tallinn", 5),
    ("Show 10 best budget phones", 10),
    ("Can 2 adults book a sauna for 3 hours?", None),
    ("Find laptops with 16 GB RAM", None),
])
def test_explicit_result_count_survives_missing_model_count(message, count):
    from app.orchestration.interpretation import preserve_request
    result = preserve_request(ResearchContract(), message)
    assert result.requested_count == count


def test_requested_count_shortfall_gets_new_candidate_search_even_when_existing_checks_are_met():
    contract = ResearchContract(subject="holiday spots", destination="Estonia", requested_count=5)
    answer = GroundedAnswer(findings=[Finding(name="Tallinn", checks=[check("location", "met")])])
    queries = fallback_gap_queries(answer.model_dump(), contract, set())
    assert len(queries) == 1
    assert "additional distinct" in queries[0].gap
    assert "Tallinn" in queries[0].objective
    assert "5" in queries[0].objective
    assert not fallback_gap_queries(answer.model_dump(), contract, {query_key(queries[0])})


def test_related_hotel_price_gap_targets_named_hotel():
    contract = ResearchContract(destination="Estonia", requirements=[
        Requirement(id="hotel_price", description="Published nightly hotel price", kind="price", scope="related")])
    unknown = check("hotel_price", "unknown")
    unknown.requirement = "Published nightly hotel price"
    unknown.scope, unknown.related_entity = "related", "Hotel Dorpat"
    answer = GroundedAnswer(findings=[Finding(name="Tartu", checks=[unknown])])
    query = fallback_gap_queries(answer.model_dump(), contract, set())[0]
    assert query.query.startswith("Hotel Dorpat")
    assert "Published nightly hotel price" in query.query


def test_shipping_gap_targets_seller_policy_instead_of_exact_model_specs():
    contract = ResearchContract(activity="product", destination="Estonia", requirements=[
        Requirement(id="shipping", description="Delivery to Estonia", kind="delivery")])
    unknown = check("delivery", "unknown")
    answer = GroundedAnswer(findings=[Finding(name="ThinkPad X1", source_url="https://retailer.example/sku/123", checks=[unknown])])
    query = fallback_gap_queries(answer.model_dump(), contract, set())[0]
    assert "retailer.example shipping delivery policy" in query.query


def test_long_gap_descriptions_produce_bounded_queries_without_schema_failure():
    contract = ResearchContract()
    unknown = check("detail", "unknown")
    unknown.requirement = "Specific requirement " * 100
    answer = GroundedAnswer(findings=[Finding(name="An option", checks=[unknown])])
    query = fallback_gap_queries(answer.model_dump(), contract, set())[0]
    assert len(query.query) <= 500 and len(query.objective) <= 2000


@pytest.mark.parametrize("message", ["My budget is now 2000", "Raise my budget to €2000", "Budget: 2000 EUR"])
def test_explicit_budget_declaration_replaces_old_budget(message):
    from app.orchestration.interpretation import preserve_request
    old = ResearchContract(budget=Budget(amount=420, currency="EUR", operator="lt"))
    updated = preserve_request(old, message)
    assert updated.budget.amount == 2000 and updated.budget.operator == "lte"
    assert old.budget.amount == 420


def test_budget_correction_in_same_message_uses_new_amount():
    from app.orchestration.interpretation import preserve_request
    updated = preserve_request(ResearchContract(), "Find a laptop under €420, actually price under €2000")
    assert updated.budget.amount == 2000 and updated.budget.operator == "lt"


def test_followup_budget_change_keeps_requested_count_from_quoted_context(monkeypatch):
    from dataclasses import dataclass
    from types import SimpleNamespace
    from app.orchestration import planner
    from app.orchestration.contracts import SearchPlan

    @dataclass
    class Usage:
        input_tokens: int = 1

    output = SearchPlan(contract=ResearchContract(requested_count=5, count_quote="5 laptops"), queries=[])
    monkeypatch.setattr(planner, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(planner, "call_model", lambda *args, **kwargs: SimpleNamespace(output=output, usage=Usage()))
    result, _ = planner.plan_searches("Original request:\nRaise my budget to 2000\nRecent context;\nuser: Find 5 laptops under €420")
    assert result.contract.requested_count == 5


def test_refinement_context_includes_count_deficit_with_existing_fact_gaps():
    from types import SimpleNamespace
    from app.orchestration.refinement import refinement_context
    result = SimpleNamespace(debug={"coverage": {"requested": 5, "retained": 3},
                                    "next_action": {"gaps": [{"requirement": "Published price"}]}})
    context = refinement_context(result)
    assert "additional distinct candidates" in context and '"missing": 2' in context
    assert "Published price" in context


@pytest.mark.parametrize("current,expected", [
    ("Raise my budget to 2000", True),
    ("Find cheaper laptops", True),
    ("Find restaurants", False),
    ("Actually restaurants with a budget under 100", False),
])
def test_inherited_count_requires_a_continuing_task(current, expected):
    from app.orchestration.planner import inherited_count_quote
    assert inherited_count_quote("5 laptops", current, current + "\nRecent context; Find 5 laptops") == expected


def test_three_tentative_products_do_not_complete_three_qualifying_offers():
    contract = ResearchContract(activity="product", subject="laptop", destination="Finland", requested_count=3,
        budget=Budget(amount=700, operator="lt"), requirements=[Requirement(id="ram", description="16GB RAM")])
    findings = [Finding(name=f'Laptop Model{i} 14"', source_url=f"https://seller.example/product/{i}", source_kind="product",
                        checks=[check("price", "met"), check("stock", "unknown"), check("delivery", "unknown")])
                for i in range(3)]
    answer = GroundedAnswer(findings=findings).model_dump()
    first = fallback_gap_queries(answer, contract, set())
    assert len(first) == 4 and "additional distinct" in first[0].gap
    assert "Fully supported candidates: 0" in first[0].objective
    attempted = {query_key(query) for query in first}
    next_queries = fallback_gap_queries(answer, contract, attempted)
    alternative = next(query for query in next_queries if "alternative sellers" in query.gap)
    assert all(text in alternative.query for text in ("700", "16GB", "in-stock"))
    assert "seller.example" in alternative.objective
    assert "retailers other than seller.example" in alternative.query
    assert all(query.query not in {old.query for old in first} for query in next_queries)
    attempted.update(query_key(query) for query in next_queries)
    assert not fallback_gap_queries(answer, contract, attempted)


def test_refinement_count_gap_uses_verified_count_even_when_shortlist_has_five_names():
    from types import SimpleNamespace
    from app.orchestration.refinement import refinement_context
    context = refinement_context(SimpleNamespace(debug={"coverage": {"requested": 5, "retained": 5, "verified": 2}}))
    assert '"missing": 3' in context and '"verified": 2' in context


@pytest.mark.parametrize("original,allowed_domains", [
    ("Find a laptop only from seller.example", []),
    ("Find a laptop", ["seller.example"]),
])
def test_alternative_seller_exploration_respects_source_restrictions(original, allowed_domains):
    contract = ResearchContract(activity="product", subject="laptop", requested_count=1,
                                original_request=original, budget=Budget(amount=700, operator="lt"))
    finding = Finding(name="Laptop X1", source_url="https://seller.example/product/123", source_kind="product",
                      checks=[check("stock", "unknown")])
    answer = GroundedAnswer(findings=[finding]).model_dump()
    first = fallback_gap_queries(answer, contract, set())
    attempted = {query_key(query) for query in first}
    if allowed_domains:
        restricted_query = SearchQuery(query="Laptop X1 stock", objective="Find stock", include_domains=allowed_domains)
        attempted.add(query_key(restricted_query))
    assert not fallback_gap_queries(answer, contract, attempted)
