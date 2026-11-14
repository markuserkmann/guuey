import pytest

from app.orchestration.contracts import ResearchContract, Requirement, SearchQuery
from app.orchestration.interpretation import permitted_filters, preserve_request


@pytest.mark.parametrize("text,amount", [
    ("under €1,000", 1000), ("under €1.000", 1000), ("under €1,000.50", 1000.5),
    ("under €1.000,50", 1000.5), ("under 150 euros", 150), ("under €149,99", 149.99),
])
def test_original_budget_does_not_truncate_grouped_amount(text, amount):
    contract = preserve_request(ResearchContract(), "Find a laptop " + text)
    assert contract.budget.amount == amount
    assert contract.budget.operator == "lt"


def test_presentation_requirement_is_not_a_candidate_fact():
    contract = ResearchContract(answer_format="table", requirements=[
        Requirement(id="table_format", description="Formatted into a table"),
        Requirement(id="hotel", description="Named hotel in the destination", scope="related"),
    ])
    assert [requirement.id for requirement in contract.requirements] == ["hotel"]


def test_invalid_optional_filter_does_not_break_useful_search():
    original = "Find news published after 2025-01-01 only from https://example.com"
    query = SearchQuery(query="Latest news", objective="Find requested news", filter_quote=original,
                        include_domains=["https://example.com"], start_published_date="2025-01-01-not-a-time")
    allowed = permitted_filters(query, original)
    assert allowed.query == query.query
    assert allowed.include_domains == []
    assert allowed.start_published_date is None


def test_destination_with_related_hotels_is_not_itself_a_hotel():
    from app.orchestration.contracts import ResearchContract, Requirement
    from app.orchestration.interpretation import preserve_request
    contract = ResearchContract(subject='holiday spots and hotels', requirements=[
        Requirement(id='hotel', description='Budget hotel for each spot', scope='related')])
    result = preserve_request(contract, 'Find five holiday spots and hotels for them in a table')
    assert result.subject == 'holiday spots'
    assert not any(r.id == 'requested_type' for r in result.requirements)
    assert next(r for r in result.requirements if r.id == 'hotel').scope == 'related'


def test_requested_hotels_are_not_silently_replaced_by_hostels():
    from app.orchestration.contracts import ResearchContract, Requirement
    from app.orchestration.interpretation import preserve_request
    contract = ResearchContract(requirements=[Requirement(id='stay', description='Hotel or budget accommodation', scope='related')])
    result = preserve_request(contract, 'Five holiday spots on a budget and hotels for them')
    stay = next(r for r in result.requirements if r.id == 'stay')
    assert stay.description == 'Named hotel for this destination, with its location or proximity stated' and stay.strength == 'hard'
    assert next(r for r in result.requirements if r.id == 'hotel_value').strength == 'preference'


def test_hotel_preference_does_not_shadow_required_named_hotel():
    from app.orchestration.contracts import ResearchContract, Requirement
    from app.orchestration.interpretation import preserve_request
    contract = ResearchContract(subject='holiday spot', destination='Estonia', requirements=[
        Requirement(id='hotel', description='Named hotel located here', scope='related'),
        Requirement(id='value', description='Budget hotel features', scope='related', strength='preference')])
    result = preserve_request(contract, 'Five holiday spots in Estonia on a budget and hotels for them')
    assert next(r for r in result.requirements if r.id=='value').strength == 'preference'
    assert next(r for r in result.requirements if r.id=='value').description != next(r for r in result.requirements if r.id=='hotel').description
    assert not any(r.id=='requested_type' for r in result.requirements)
    assert not any(r.kind=='location' and r.scope=='primary' for r in result.requirements)
    assert [r.id for r in result.requirements if r.scope == 'related'] == ['hotel', 'value']


def test_product_output_instructions_do_not_duplicate_configuration_checks():
    contract = ResearchContract(activity="product", requirements=[
        Requirement(id="ram", description="At least 16 GB RAM"),
        Requirement(id="ssd", description="At least 512 GB SSD"),
        Requirement(id="seller_link", description="Seller link provided"),
        Requirement(id="exact_config", description="Exact configuration details provided"),
    ])
    result = preserve_request(contract, "Find laptops with 16 GB RAM and 512 GB SSD; show exact configuration and seller links")
    assert {requirement.id for requirement in result.requirements} >= {"ram", "ssd", "stock"}
    assert not {"seller_link", "exact_config"}.intersection(requirement.id for requirement in result.requirements)
    assert {requirement.id for requirement in contract.requirements} >= {"seller_link", "exact_config"}


def test_configuration_request_without_atomic_specs_remains_a_factual_requirement():
    contract = ResearchContract(activity="product", requirements=[
        Requirement(id="exact_config", description="Exact configuration details provided")])
    result = preserve_request(contract, "Compare these laptops and identify each exact configuration")
    assert next(requirement for requirement in result.requirements if requirement.id == "exact_config").description == "Exact model or SKU and configuration of this offer"


def test_real_product_and_related_link_requirements_are_preserved():
    contract = ResearchContract(requirements=[
        Requirement(id="model", description="Exact model 21KJ00ABMX with 32 GB RAM"),
        Requirement(id="booking", description="Official booking link"),
        Requirement(id="shipping", description="Link to seller's shipping policy"),
        Requirement(id="hotel_link", description="Product link provided", scope="related"),
    ])
    result = preserve_request(contract, "Check exact models and named hotels with official booking and shipping policy links")
    assert {requirement.id for requirement in result.requirements} >= {"model", "booking", "shipping", "hotel_link"}


def test_top_five_output_request_is_not_a_per_candidate_evidence_check():
    contract = ResearchContract(destination='Estonia', requirements=[
        Requirement(id='spots', description='Top 5 holiday spots in Estonia'),
        Requirement(id='hotel', description='Named hotel for this destination', scope='related')])
    normalized = preserve_request(contract, 'Get me top 5 holiday spots in Estonia and hotels for them')
    assert normalized.requested_count == 5
    assert 'spots' not in {requirement.id for requirement in normalized.requirements}
    assert 'hotel' in {requirement.id for requirement in normalized.requirements}


def test_factual_ranking_condition_is_preserved():
    contract = ResearchContract(requested_count=5, count_quote='5 companies', requirements=[
        Requirement(id='revenue', description='Top 5 companies by annual revenue')])
    assert contract.requirements[0].id == 'revenue'


def test_count_is_not_an_individual_destination_fact_and_hotel_location_is_separate():
    contract = ResearchContract(subject='holiday destination', destination='Estonia', requested_count=5,
        count_quote='5 holiday spots', requirements=[
            Requirement(id='spots', description='5 distinct budget holiday spots in Estonia', kind='type'),
            Requirement(id='hotel', description='Named hotel in the destination', kind='location', scope='related')])
    assert 'spots' not in {r.id for r in contract.requirements}
    assert not any(r.kind == 'location' and r.scope == 'primary' for r in contract.requirements)


@pytest.mark.parametrize('wording,kind,strength', [
    ('Budget holiday spots and hotels', 'fact', 'preference'),
    ('Budget holiday spots and hotels with prices', 'price', 'hard'),
    ('Hotels under €150', 'price', 'hard'),
])
def test_qualitative_budget_does_not_invent_a_room_rate_requirement(wording, kind, strength):
    contract = ResearchContract(requirements=[
        Requirement(id='hotel', description='Named hotel', scope='related'),
        Requirement(id='value', description='Economical price or budget-friendly accommodation option',
                    kind='price', scope='related')])
    result = preserve_request(contract, wording)
    value = next(r for r in result.requirements if r.id == 'value')
    assert (value.kind, value.strength) == (kind, strength)
    assert 'hotel_value' not in {r.id for r in result.requirements}
