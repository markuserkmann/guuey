import pytest

from app.model_calls import model_call_events
from app.orchestration import orchestrator
from app.orchestration.contracts import Budget, Requirement, ResearchContract, SearchPlan, SearchQuery
from app.orchestration.interpretation import permitted_filters, preserve_request, query_key


@pytest.mark.parametrize("wording,operator", [
    ("under", "lt"), ("below", "lt"), ("less than", "lt"),
    ("at most", "lte"), ("up to", "lte"), ("no more than", "lte"),
    ("around", "approx"), ("about", "approx"), ("approximately", "approx"),
])
def test_original_budget_operator_overrides_wrong_model_bound(wording, operator):
    model_contract = ResearchContract(budget=Budget(amount=200, currency="USD", operator="lte", basis="per night"))
    original = f"Show a table of hotels {wording} €100 per night."
    preserved = preserve_request(model_contract, original)
    assert preserved.budget.amount == 100
    assert preserved.budget.currency == "EUR"
    assert preserved.budget.operator == operator
    assert preserved.budget.basis == "per night"
    price = next(r for r in preserved.requirements if r.id == "price")
    assert price.strength == ("preference" if operator == "approx" else "hard")
    assert preserved.original_request == original
    assert preserved.answer_format == "table"
    assert model_contract.budget.amount == 200


@pytest.mark.parametrize('budget_kind', ['fact', 'type'])
def test_destination_shortlist_cannot_become_a_bookable_service(budget_kind):
    task = ResearchContract(activity='service', subject='holiday destination', destination='Estonia',
        requirements=[Requirement(id='budget', description='Budget-friendly destination in Estonia', kind=budget_kind),
                      Requirement(id='hotel', description='Named hotel for this destination', scope='related'),
                      Requirement(id='hotel_details', description='Hotel location details provided', scope='related')])
    output = preserve_request(task, 'Get me top 5 holiday spots in Estonia on an budget and hotels for them in a table')
    assert output.activity == 'research'
    assert not any(r.id == 'requested_type' for r in output.requirements)
    assert next(r for r in output.requirements if r.id == 'budget').strength == 'preference'
    assert next(r for r in output.requirements if r.id == 'hotel').strength == 'hard'
    assert not any(r.kind == 'location' and r.scope == 'primary' for r in output.requirements)
    assert not any(r.id == 'hotel_details' for r in output.requirements)


def test_followup_currency_free_price_replaces_old_budget():
    old = ResearchContract(budget=Budget(amount=420, currency="USD", operator="lte"))
    updated = preserve_request(old, "Actually, price under 2000")
    assert (updated.budget.amount, updated.budget.currency, updated.budget.operator) == (2000, "USD", "lt")
    assert next(r for r in updated.requirements if r.kind == "price").description == "Price under 2000 USD"
    assert old.budget.amount == 420


def test_unrelated_currency_free_number_does_not_change_budget():
    old = ResearchContract(budget=Budget(amount=420, operator="lte"))
    assert preserve_request(old, "I need under 16 GB of memory").budget.amount == 420


def test_new_turn_planning_receives_saved_context_and_current_request(monkeypatch):
    seen = []
    def plan(message, contract):
        seen.append(message)
        return SearchPlan(contract=ResearchContract(), queries=[]), None
    monkeypatch.setattr(orchestrator, "plan_searches", plan)
    from app.schemas import ChatTurn
    orchestrator.run_turn("price under 2000", [ChatTurn(role="user", content="laptop under €420"),
        ChatTurn(role="assistant", content="Saved previous task requirements: budget 420 EUR")], search_requested=True)
    assert seen[0].startswith("Original request:\nprice under 2000")
    assert "current request above overrides older constraints" in seen[0]
    assert "laptop under €420" in seen[0]


def test_missing_quote_removes_all_invented_filters():
    query = SearchQuery(query="saunas", objective="prices", include_domains=["example.fi"],
                        exclude_domains=["other.fi"], start_published_date="2026-01-01", category="news")
    filtered = permitted_filters(query, "Find saunas in Helsinki")
    assert filtered.include_domains == filtered.exclude_domains == []
    assert filtered.start_published_date is None
    assert filtered.category is None
    assert query.include_domains == ["example.fi"]


def test_copied_numeric_requirement_cannot_authorize_news_or_date_filters():
    original = "Find laptops with 16 GB RAM."
    query = SearchQuery(query="laptops", objective="offers", filter_quote=original,
                        category="news", start_published_date="2026-01-01")
    filtered = permitted_filters(query, original)
    assert filtered.category is None
    assert filtered.start_published_date is None


def test_mentioning_domain_does_not_authorize_allowlist():
    original = "Compare example.fi and other.fi laptop offers."
    query = SearchQuery(query="laptops", objective="offers", filter_quote=original, include_domains=["example.fi"])
    assert permitted_filters(query, original).include_domains == []


def test_explicit_domain_restriction_retains_only_named_domains():
    original = "Find laptop offers only from example.fi."
    query = SearchQuery(query="laptops", objective="offers", filter_quote="only from example.fi",
                        include_domains=["example.fi", "invented.fi"])
    assert permitted_filters(query, original).include_domains == ["example.fi"]


def test_explicit_news_date_restriction_is_preserved():
    original = "Only news articles published after 2026-01-01."
    query = SearchQuery(query="sauna openings", objective="recent news", filter_quote=original,
                        category="news", start_published_date="2026-01-01")
    filtered = permitted_filters(query, original)
    assert filtered.category == "news"
    assert filtered.start_published_date == "2026-01-01"


def test_hostname_substring_cannot_authorize_different_site():
    original = "Find offers only from notexample.fi."
    query = SearchQuery(query="offers", objective="prices", filter_quote=original, include_domains=["example.fi"])
    assert permitted_filters(query, original).include_domains == []


def test_wrong_iso_date_filter_is_removed():
    original = "Only news published after 2025-01-01."
    query = SearchQuery(query="news", objective="updates", filter_quote=original, category="news",
                        start_published_date="2026-01-01T00:00:00Z")
    filtered = permitted_filters(query, original)
    assert filtered.category == "news"
    assert filtered.start_published_date is None


def test_published_price_and_booking_link_need_neither_dates_nor_self_proximity():
    contract = ResearchContract(activity="service", subject="public sauna", anchor="Löyly",
        exact_entities=["Löyly"], clarification_question="Which dates do you want?",
        requirements=[Requirement(id="price", kind="price", description="Published admission price"),
                      Requirement(id="booking", kind="booking", description="Official booking link")])
    preserved = preserve_request(contract, "Find Löyly's published admission price and official booking link.")
    assert preserved.clarification_question is None
    assert preserved.anchor is None
    assert not any(requirement.id == "proximity" for requirement in preserved.requirements)
    assert {requirement.id for requirement in preserved.requirements} >= {"price", "booking"}


def source():
    return {"url": "https://example.fi/offer", "title": "Laptop", "text": "16 GB RAM", "retrieval": "page"}


def test_followup_cannot_replace_frozen_requirements_or_budget(monkeypatch):
    frozen = preserve_request(ResearchContract(activity="product", destination="Finland",
        requirements=[Requirement(id="memory", description="At least 16 GB RAM")]), "Laptops under €1000")
    before = frozen.model_dump()
    changed = ResearchContract(budget=Budget(amount=2000, operator="lte"),
                               requirements=[Requirement(id="memory", description="8 GB RAM is enough")])
    query = SearchQuery(query="Exact laptop memory", objective="Verify memory", gap="memory")
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (SearchPlan(contract=changed, queries=[query]), {"output": "changed"}))
    monkeypatch.setattr(orchestrator, "search", lambda *args, **kwargs: [source()])
    monkeypatch.setattr(orchestrator, "extract_pages", lambda *args, **kwargs: [source()])
    seen = []

    def answer(*args, **kwargs):
        seen.append(kwargs["contract"].model_dump())
        return "answer", {}

    monkeypatch.setattr(orchestrator, "generate_reply", answer)
    result = orchestrator.run_turn("Refine laptop checks", [], search_requested=True, contract=frozen)
    assert seen == [before]
    assert result.debug["research_contract"] == before
    assert result.debug["search_plan"]["effective_contract"] == before
    assert frozen.model_dump() == before


def test_repeated_query_stops_before_retrieval_or_answering(monkeypatch):
    original = SearchQuery(query="Sauna Helsinki PRICE", objective="Find prices")
    repeated = SearchQuery(query="  sauna   helsinki price  ", objective="A different rationale", gap="price")
    attempted = {query_key(original)}
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (SearchPlan(contract=ResearchContract(), queries=[repeated]), None))
    for name in ("search", "extract_pages", "generate_reply"):
        monkeypatch.setattr(orchestrator, name, lambda *args, **kwargs: pytest.fail("Repeated query must not execute"))
    with pytest.raises(orchestrator.RefinementComplete):
        orchestrator.run_turn("Sauna prices", [], search_requested=True, attempted_queries=attempted)
    assert attempted == {query_key(original)}


def test_executor_preserves_diagnostic_context_and_records_query(monkeypatch):
    query = SearchQuery(query="Helsinki sauna price", objective="Find prices")
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (SearchPlan(contract=ResearchContract(), queries=[query]), None))

    def search(*args, **kwargs):
        model_call_events.get()({"stage": "search_probe", "status": "completed"})
        return [{**source(), 'text': ''}]

    def extract(*args, **kwargs):
        model_call_events.get()({"stage": "extract_probe", "status": "completed"})
        return [{**source(), 'text': ''}]

    monkeypatch.setattr(orchestrator, "search", search)
    monkeypatch.setattr(orchestrator, "extract_pages", extract)
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: ("answer", {}))
    attempted, events = set(), []
    token = model_call_events.set(events.append)
    try:
        result = orchestrator.run_turn("Sauna prices", [], search_requested=True, attempted_queries=attempted)
    finally:
        model_call_events.reset(token)
    assert {event["stage"] for event in events} >= {"search_probe", "extract_probe", "retrieval"}
    assert attempted == {query_key(query)}
    assert result.debug["executed_queries"] == [query.model_dump()]


def test_empty_initial_plan_can_return_material_clarification(monkeypatch):
    question = "Which city should I search?"
    plan = SearchPlan(contract=ResearchContract(clarification_question=question, missing_information=["Search city"]), queries=[])
    monkeypatch.setattr(orchestrator, "plan_searches", lambda *args: (plan, None))
    monkeypatch.setattr(orchestrator, "search", lambda *args, **kwargs: pytest.fail("No query was planned"))
    monkeypatch.setattr(orchestrator, "generate_reply", lambda *args, **kwargs: (question, {}))
    result = orchestrator.run_turn("Find nearby places", [], search_requested=True)
    assert question in result.reply
    assert result.sources == []


def test_continuation_normalizes_saved_compound_destination_contract_before_planning(monkeypatch):
    from app.orchestration.contracts import Requirement
    from app.orchestration.orchestrator import RefinementComplete
    import pytest
    old = ResearchContract(original_request='Find holiday spots and hotels for them in Estonia', subject='holiday spots and hotels', requirements=[Requirement(id='hotel',description='Named hotel for the destination',scope='related')])
    seen=[]
    def plan(message, contract):
        seen.append(contract)
        return SearchPlan(contract=contract,queries=[]), None
    monkeypatch.setattr(orchestrator,'plan_searches',plan)
    with pytest.raises(RefinementComplete):
        orchestrator.run_turn('Continue',[],search_requested=True,contract=old,attempted_queries={'previous'})
    assert seen[0].subject == 'holiday spots'
    assert seen[0].original_request == old.original_request
    assert not any(r.id=='requested_type' for r in seen[0].requirements)
