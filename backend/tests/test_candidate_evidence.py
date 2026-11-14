from datetime import UTC, datetime

import pytest
from markdown_it import MarkdownIt
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.orchestration.contracts import Budget, ResearchContract, Requirement
from app.orchestration.grounding import (
    Evidence, Finding, GroundedAnswer, RequirementCheck, answer_coverage, apply_contract, render_answer,
)
from app.orchestration.verification import resolve_source_ids, verify_claims


URL = "https://hotel.example/rooms"


def source(text="Hotel A costs €150 per night.", **values):
    return {"url": URL, "title": "Hotel A", "text": text, "retrieval": "live",
            "retrieved_at": datetime.now(UTC).isoformat(), **values}


def answer(statement="€150 per night", quote="Hotel A costs €150 per night.", **check_values):
    check = RequirementCheck(requirement_id="price", requirement="Price", status="met", statement=statement,
                             evidence=[Evidence(source_url=URL, quote=quote)], **check_values)
    return GroundedAnswer(findings=[Finding(name="Hotel A", source_url=URL, source_kind="service", checks=[check])])


def contract(operator="lt", **values):
    return ResearchContract(activity="service", budget=Budget(amount=150, operator=operator, basis="per night"), **values)


@pytest.mark.parametrize("operator,status", [("lt", "not_met"), ("lte", "met")])
def test_budget_boundary_distinguishes_under_from_at_most(operator, status):
    result = apply_contract(answer(), [source()], contract(operator))
    assert result.findings[0].checks[0].status == status


@pytest.mark.parametrize("amount", ["€149.99", "149,99 €"])
def test_budget_decimal_price_does_not_round_to_boundary(amount):
    text = f"Hotel A costs {amount} per night."
    result = apply_contract(answer(f"{amount} per night", text), [source(text)], contract())
    assert result.findings[0].checks[0].status == "met"


@pytest.mark.parametrize("statement,text", [
    ("€149 per person", "Hotel A costs €149 per person."),
    ("€149 per night", "Hotel A costs €149 per person."),
    ("From €149 per night", "Hotel A costs from €149 per night."),
    ("$149 per night", "Hotel A costs $149 per night."),
])
def test_ambiguous_or_wrong_price_basis_cannot_satisfy_budget(statement, text):
    result = apply_contract(answer(statement, text), [source(text)], contract())
    assert result.findings[0].checks[0].status == "unknown"


def test_preference_failure_does_not_disqualify_hard_match():
    output = answer()
    output.findings[0].checks += [RequirementCheck(requirement_id="balcony", requirement="Prefer balcony",
        status="not_met", statement="No balcony", evidence=[Evidence(source_url=URL, quote="No balcony")])]
    sources = [source("Hotel A costs €150 per night. No balcony.")]
    request = ResearchContract(activity="service", requirements=[
        Requirement(id="price", kind="price", description="Price"),
        Requirement(id="balcony", description="Prefer balcony", strength="preference")])
    result = apply_contract(output, sources, request)
    assert answer_coverage(result, sources)["verified"] == 1
    assert not answer_coverage(result, sources)["gaps"]
    text, _ = render_answer(result, sources)
    assert "Preference not met" in text
    assert "does not meet all requirements" not in text


@pytest.mark.parametrize("second_status", ["met", "not_met"])
def test_conflicting_duplicate_checks_stay_unknown_with_both_sources(second_status):
    output = answer()
    second_url = "https://hotel.example/rates"
    other = output.findings[0].checks[0].model_copy(deep=True)
    other.status, other.statement = second_status, "€180 per night"
    other.evidence = [Evidence(source_url=second_url, quote="Hotel A costs €180 per night.")]
    output.findings[0].checks.append(other)
    sources = [source(), source("Hotel A costs €180 per night.", url=second_url)]
    result = apply_contract(output, sources, contract())
    check = result.findings[0].checks[0]
    assert check.status == "unknown"
    assert len(check.evidence) == 2
    text, _ = render_answer(result, sources)
    assert "Conflicting:" in text
    assert URL in text and second_url in text
    assert "**Does not meet requirement**" not in text


def test_unrelated_hotel_quote_does_not_support_candidate():
    sources = [source(), source("Hotel B costs €100 per night.", url="https://other.example", title="Hotel B")]
    output = answer("€100 per night", "Hotel B costs €100 per night.")
    output.findings[0].checks[0].evidence[0].source_url = sources[1]["url"]
    result = apply_contract(output, sources, contract())
    assert result.findings[0].checks[0].status == "unknown"
    assert answer_coverage(result, sources)["verified"] == 0


def test_empty_research_contract_still_rejects_unrelated_candidate_evidence():
    sources = [source(), source("Hotel B has a pool.", url="https://other.example", title="Hotel B")]
    output = answer("Has a pool", "Hotel B has a pool.")
    output.findings[0].checks[0].evidence[0].source_url = sources[1]["url"]
    result = apply_contract(output, sources, ResearchContract())
    assert result.findings[0].checks[0].status == "unknown"


def test_other_table_row_cannot_borrow_candidate_identity_from_page_title():
    text = "| Hotel | Price |\n| --- | --- |\n| Hotel A | €150 per night |\n| Hotel B | €100 per night |"
    output = answer("€100 per night", "€100 per night")
    result = apply_contract(output, [source(text)], contract())
    assert result.findings[0].checks[0].status == "unknown"


def test_unrelated_quote_cannot_retain_invented_candidate():
    output = answer()
    output.findings[0].name = "Invented Hotel Z"
    assert answer_coverage(output, [source()])["retained"] == 0
    text, _ = render_answer(output, [source()])
    assert "Invented Hotel Z" not in text


@pytest.mark.parametrize("related_entity,statement,expected", [
    ("Hotel A", "Hotel A costs €150 per night", "met"),
    (None, "Hotels cost €150 per night", "unknown"),
    ("Hotel B", "Hotel B costs €150 per night", "unknown"),
    ("Hotel A", "€150 per night", "unknown"),
])
def test_destination_hotel_check_names_real_related_entity(related_entity, statement, expected):
    text = "Hotel A is in Madeira. Hotel A costs €150 per night."
    output = answer(statement, text, related_entity=related_entity)
    output.findings[0].name = "Madeira"
    output.findings[0].source_kind = "other"
    request = ResearchContract(activity="research", requirements=[
        Requirement(id="price", kind="price", description="A named hotel with a price", scope="related")])
    result = apply_contract(output, [source(text)], request)
    assert result.findings[0].checks[0].status == expected


def test_stable_source_references_survive_reordering():
    sources = [source(source_id="S_aaaa"), source("Hotel B", url="https://other.example", source_id="S_bbbb")]
    output = answer()
    output.findings[0].source_url = "S_aaaa"
    output.findings[0].checks[0].evidence[0].source_url = "S_aaaa"
    resolved = resolve_source_ids(output, list(reversed(sources)))
    assert resolved.findings[0].source_url == URL
    assert resolved.findings[0].checks[0].evidence[0].source_url == URL


def test_missing_stable_source_is_not_reinterpreted_as_positional_source():
    output = answer()
    output.findings[0].source_url = "S_missing"
    output.findings[0].checks[0].evidence[0].source_url = "S_missing"
    resolved = resolve_source_ids(output, [source()])
    assert resolved.findings[0].source_url == "S_missing"
    assert answer_coverage(resolved, [source()])["verified"] == 0


def test_table_cells_escape_text_and_link_destinations():
    url = "https://hotel.example/room?label=a|b(x)"
    sources = [source(url=url)]
    output = answer()
    output.findings[0].source_url = url
    check = output.findings[0].checks[0]
    check.evidence[0].source_url = url
    check.statement = "€150 per night | breakfast"
    request = ResearchContract(answer_format="table", requirements=[Requirement(id="price", description="Price | details")])
    text, issues = render_answer(output, sources, request)
    assert not issues
    assert "| Option |" in text and "Price \\| details" in text
    assert "%7C" in text and "%28" in text
    tokens = MarkdownIt().enable("table").parse(text)
    assert sum(token.type == "table_open" for token in tokens) == 1
    assert sum(token.type == "td_open" for token in tokens) == 2


def test_verifier_conflict_preserves_evidence_but_cannot_count_as_supported():
    output = answer()
    output.findings[0].source_kind = "other"

    async def model(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"judgments": [
            {"claim_id": 0, "supported": True, "conflicting": True, "reason": "Conflicting rates"}]})])

    result, _ = verify_claims(output, [source()], FunctionModel(model))
    check = result.findings[0].checks[0]
    assert check.status == "unknown" and check.evidence
    assert "Conflicting evidence" in check.reason
    assert answer_coverage(result, [source()])["verified"] == 0


def test_explicit_service_entity_ignores_only_requested_service_descriptor():
    text = "Löyly Helsinki. Admission is EUR 29.00 for two hours."
    output = answer("EUR 29.00", "Admission is EUR 29.00 for two hours.")
    output.findings[0].name = "Löyly Helsinki Public Sauna Session"
    request = ResearchContract(activity="service", subject="public sauna session", exact_entities=["Löyly Helsinki"],
                               requirements=[Requirement(id="price", kind="price", description="Admission price")])
    result = apply_contract(output, [source(text, title="Löyly Helsinki")], request)
    assert result.findings[0].name == "Löyly Helsinki"
    assert result.findings[0].checks[0].basis == "reported"
    assert result.findings[0].checks[0].statement == "EUR 29.00"


def test_explicit_service_identity_can_use_operator_title_and_separate_city_address():
    text = "# Public sauna\n\nAdmission is EUR 29.00 for two hours.\n\nHernesaarenranta 4, Helsinki"
    output = answer("EUR 29.00", "Admission is EUR 29.00 for two hours.")
    output.findings[0].name = "Löyly Helsinki Public Sauna Session"
    request = ResearchContract(activity="service", subject="public sauna session", destination="Helsinki", exact_entities=["Löyly Helsinki"],
                               requirements=[Requirement(id="price", kind="price", description="Admission price")])
    sources = [source(text, title="Public Sauna | Löyly")]
    result = apply_contract(output, sources, request)
    assert result.findings[0].name == "Löyly Helsinki"
    assert next(c for c in result.findings[0].checks if c.requirement_id == "price").basis == "reported"
    assert answer_coverage(result, sources)["retained"] == 1
    # The inferred alias is internal validation state, not a model-provided field.
    assert "_identity_alias" not in result.findings[0].model_dump()


def test_booking_anchor_on_same_operator_page_uses_reordered_identity_in_title():
    from app.orchestration.passages import citation_passages

    booking = "https://varaus.asio.fi/loyly"
    primary = source("# Public sauna\n\nLöyly Helsinki public sauna.", title="Public Sauna | Löyly",
                     url="https://www.loylyhelsinki.fi/en/public-sauna")
    localized = source(f"# Yleinen sauna\n\n[Varaa Saunavuoro]({booking})", title="Yleinen Sauna Helsinki | Löyly",
                       url="https://www.loylyhelsinki.fi/yleinensauna")
    identifier = next(key for key, (quote, _) in citation_passages(localized).items() if quote == "varaa saunavuoro")
    output = GroundedAnswer(findings=[Finding(name="Löyly Helsinki", source_url=primary["url"], source_kind="service", checks=[
        RequirementCheck(requirement_id="booking_link", requirement="Direct booking URL", status="met", statement=booking,
                         evidence=[Evidence(source_url=localized["url"], quote=identifier)])])])
    request = ResearchContract(activity="service", subject="public sauna", exact_entities=["Löyly Helsinki"],
                               requirements=[Requirement(id="booking_link", description="Direct booking URL")])
    resolved = resolve_source_ids(output, [primary, localized])
    result = apply_contract(resolved, [primary, localized], request)
    check = result.findings[0].checks[0]
    assert check.status == "met" and check.statement == booking
    from app.orchestration.grounding import evidence_context
    assert f"]({booking})" in evidence_context(check.evidence[0], [primary, localized])

    unrelated = localized | {"url": "https://another-operator.example/yleinensauna"}
    output.findings[0].checks[0].evidence = [Evidence(source_url=unrelated["url"], quote="Varaa Saunavuoro")]
    assert apply_contract(output, [primary, unrelated], request).findings[0].checks[0].status == "unknown"


def test_entity_alias_cannot_change_venue_or_product_configuration():
    output = answer()
    output.findings[0].name = "Hotel B Helsinki room"
    request = ResearchContract(activity="service", subject="room", destination="Helsinki", exact_entities=["Hotel A Helsinki"],
                               requirements=[Requirement(id="price", kind="price", description="Price")])
    result = apply_contract(output, [source("Hotel A costs €150 per night. Helsinki", title="Hotel A")], request)
    assert result.findings[0].name == "Hotel B Helsinki room"
    assert result.findings[0].checks[0].status == "unknown"
    output.findings[0].name = "Hotel A Helsinki Deluxe Suite"
    assert apply_contract(output, [source()], request).findings[0].name == "Hotel A Helsinki Deluxe Suite"


def test_related_hotel_price_and_capacity_work_under_destination_heading():
    text = "# Tallinn\n\nHotel A offers rooms for EUR 100 per night.\n\nHotel A has rooms for 4 guests."
    output = answer("Hotel A costs EUR 100 per night", "Hotel A offers rooms for EUR 100 per night.", related_entity="Hotel A")
    output.findings[0].name = "Tallinn"
    output.findings[0].source_kind = "other"
    output.findings[0].checks.append(RequirementCheck(requirement_id="capacity", requirement="Hotel capacity", status="met",
        statement="Hotel A has rooms for 4 guests", related_entity="Hotel A",
        evidence=[Evidence(source_url=URL, quote="Hotel A has rooms for 4 guests.")]))
    request = ResearchContract(requirements=[
        Requirement(id="price", kind="price", description="Hotel price", scope="related"),
        Requirement(id="capacity", description="Hotel capacity", scope="related")])
    result = apply_contract(output, [source(text, title="Tallinn")], request)
    assert all(check.basis == "reported" for check in result.findings[0].checks)
    assert answer_coverage(result, [source(text, title="Tallinn")])["verified"] == 0
    assert answer_coverage(result, [source(text, title="Tallinn")])["retained"] == 1


def test_missing_requirement_id_can_use_exact_unique_description_only():
    output = answer()
    output.findings[0].checks[0].requirement_id = ""
    output.findings[0].checks[0].requirement = "Nightly hotel price"
    request = ResearchContract(requirements=[Requirement(id="nightly_price", description="Nightly hotel price")])
    result = apply_contract(output, [source()], request)
    assert result.findings[0].checks[0].requirement_id == "nightly_price"
    assert result.findings[0].checks[0].status == "met"
    output.findings[0].checks[0].requirement = "Something unrelated"
    assert apply_contract(output, [source()], request).findings[0].checks[0].status == "unknown"


def test_legacy_capture_without_source_id_resolves_generated_stable_id():
    from app.research import stable_source_id

    output = answer()
    output.findings[0].source_url = stable_source_id(URL)
    output.findings[0].checks[0].evidence[0].source_url = stable_source_id(URL)
    resolved = resolve_source_ids(output, [source()])
    assert resolved.findings[0].source_url == URL
    assert resolved.findings[0].checks[0].evidence[0].source_url == URL


def test_related_hotel_name_can_be_recovered_only_from_exact_cited_heading():
    text = "### Rare Apartment Hotel\n\nRare Apartment Hotel offers accommodations in Tartu."
    output = answer("Rare Apartment Hotel is an accommodation option in Tartu", "Rare Apartment Hotel offers accommodations in Tartu.")
    output.findings[0].name = "Tartu"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", description="A hotel for the destination", scope="related")])
    result = apply_contract(output, [source(text, title="Hotels in Estonia")], request)
    assert result.findings[0].checks[0].related_entity == "Rare Apartment Hotel"
    assert result.findings[0].checks[0].status == "met"
    output.findings[0].checks[0].statement = "Imaginary Hotel is an accommodation option in Tartu"
    unknown = apply_contract(output, [source(text, title="Hotels in Estonia")], request)
    assert unknown.findings[0].checks[0].related_entity is None
    assert unknown.findings[0].checks[0].status == "unknown"


def test_hotel_relation_can_bridge_district_and_city_with_literal_passages():
    guide_url = "https://guide.example/tallinn"
    guide = source("### Tallinn Old Town\n\nTallinn, Estonia\n\nFree sightseeing.", url=guide_url, title="Estonia guide")
    hotel = source("### Old Town Boho Guesthouse\n\nCheap hotel in Tallinn", title="Hotels in Estonia")
    output = answer("Old Town Boho Guesthouse is listed as a hotel in Tallinn", "Cheap hotel in Tallinn", related_entity="Old Town Boho Guesthouse")
    output.findings[0].name = "Tallinn Old Town"
    output.findings[0].source_url = guide_url
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", description="A hotel for the holiday spot", scope="related")])
    result = apply_contract(output, [guide, hotel], request)
    check = result.findings[0].checks[0]
    assert check.basis == "reported"
    assert {item.source_url for item in check.evidence} == {URL, guide_url}
    assert any(item.quote == "Tallinn, Estonia" for item in check.evidence)
    guide["text"] = "Tallinn Old Town"
    rejected = apply_contract(output, [guide, hotel], request)
    assert rejected.findings[0].checks[0].status == "unknown"


def test_related_place_spelling_can_omit_diacritics_without_changing_hotel_identity():
    text = "## Hommiku Guesthouse\n\nCity Center, Parnu"
    output = answer("Hommiku Guesthouse is listed in Pärnu", "City Center, Parnu", related_entity="Hommiku Guesthouse")
    output.findings[0].name = "Pärnu"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", description="Named accommodation", scope="related")])
    result = apply_contract(output, [source(text, title="Estonia hotels")], request)
    assert result.findings[0].checks[0].status == "met"


def test_invalid_ellipsis_quote_remains_unknown_but_real_named_lead_is_visible():
    text = "## Katariina Guesthouse\n\nRakvere\n\n$66"
    output = answer("Katariina Guesthouse starts from $66 per night", "Katariina Guesthouse ... $66")
    output.findings[0].name = "Rakvere"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", description="A hotel for the destination", scope="related")])
    sources = [source(text, title="Estonia hotels")]
    result = apply_contract(output, sources, request)
    check = result.findings[0].checks[0]
    assert check.related_entity == "Katariina Guesthouse"
    assert check.status == "unknown" and not check.statement
    rendered, _ = render_answer(result, sources)
    assert "To confirm for Rakvere" in rendered
    assert "Katariina Guesthouse" not in rendered
    assert "$66" not in rendered and "per night" not in rendered
    assert URL in rendered


def test_related_hotel_table_row_can_start_with_destination_without_borrowing_other_city():
    text = "| Destination | Hotel |\n| --- | --- |\n| Parnu | Hommiku Guesthouse |\n| Tallinn | Other Hotel |"
    output = answer("Hommiku Guesthouse is listed in Pärnu", "Hommiku Guesthouse", related_entity="Hommiku Guesthouse")
    output.findings[0].name = "Pärnu"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", description="Named accommodation", scope="related")])
    assert apply_contract(output, [source(text, title="Estonia accommodation")], request).findings[0].checks[0].basis == "reported"
    output.findings[0].name = "Tallinn"
    assert apply_contract(output, [source(text, title="Estonia accommodation")], request).findings[0].checks[0].status == "unknown"


@pytest.mark.parametrize("statement", ["549,99 €", "Price now 549,99 €, regular price 779,99 €."])
def test_budget_uses_explicit_current_price_instead_of_regular_price(statement):
    text = "Hotel A. Price now 549,99 €, regular price 779,99 €."
    output = answer(statement, "Price now 549,99 €, regular price 779,99 €.")
    request = ResearchContract(activity="service", budget=Budget(amount=600, operator="lt"))
    result = apply_contract(output, [source(text)], request)
    assert result.findings[0].checks[0].status == "met"


@pytest.mark.parametrize("quote", ["Now €549.99; was €779.99", "Nyt 549,99 €; normaalihinta 779,99 €"])
def test_explicit_current_price_labels_support_common_advertised_formats(quote):
    output = answer("€549.99", quote)
    request = ResearchContract(activity="service", budget=Budget(amount=600, operator="lt"))
    assert apply_contract(output, [source("Hotel A. " + quote)], request).findings[0].checks[0].status == "met"


def test_current_price_with_parenthesized_previous_price_is_not_a_conflict():
    quote = "price: 649,00 € (was 1 099,00 €)"
    request = ResearchContract(activity="service", budget=Budget(amount=700, operator="lt"))
    result = apply_contract(answer("€649.00", quote), [source("Hotel A. " + quote)], request)
    assert result.findings[0].checks[0].status == "met"
    previous = apply_contract(answer("€1099.00", "1 099,00 €"), [source("Hotel A. " + quote)], request)
    assert previous.findings[0].checks[0].status == "unknown"


@pytest.mark.parametrize("price,statement", [("679 90€", "€679.90"), ("1 679 90€", "€1679.90"),
                                           ("1 679 €", "€1679"), ("1\u202f679\u00a090 €", "€1679.90")])
def test_retailer_spaced_cents_are_distinct_from_thousands(price, statement):
    quote = f"Hotel A. {price} näytä alv 0% näytä alv 25,5%"
    request = ResearchContract(activity="service", budget=Budget(amount=1700, operator="lt"))
    result = apply_contract(answer(statement, quote), [source(quote)], request)
    assert result.findings[0].checks[0].status == "met"
    too_low = apply_contract(answer("€90", quote), [source(quote)], request)
    assert too_low.findings[0].checks[0].status == "unknown"


@pytest.mark.parametrize("free", ["18 November 2026 · Free · ET", "Admission is free.", "Free admission", "Tasuta"])
def test_explicit_free_admission_satisfies_numeric_budget(free):
    text = f"Hotel A\n\n{free}"
    request = ResearchContract(activity="service", budget=Budget(amount=20, operator="lte"))
    result = apply_contract(answer("Free admission", free), [source(text)], request)
    assert result.findings[0].checks[0].status == "met"


@pytest.mark.parametrize("quote", ["Free shipping", "Tax free", "Admission is not free", "Free admission, premium admission €25"])
def test_free_shipping_or_ambiguous_admission_does_not_prove_zero_price(quote):
    text = f"Hotel A\n\n{quote}"
    request = ResearchContract(activity="service", budget=Budget(amount=20, operator="lte"))
    result = apply_contract(answer("Free admission", quote), [source(text)], request)
    assert result.findings[0].checks[0].status == "unknown"


@pytest.mark.parametrize("quote", [
    "€549.99 or €779.99",
    "Now €549.99; regular €779.99; another price €499.99",
    "Now from €549.99; regular €779.99",
    "Now €549.99 per month; regular €779.99",
    "€549–779", "549–779 €", "549 €–779",
])
def test_budget_does_not_resolve_ambiguous_ranges_or_conditional_prices(quote):
    output = answer("€549", quote)
    request = ResearchContract(activity="service", budget=Budget(amount=600, operator="lt"))
    assert apply_contract(output, [source("Hotel A. " + quote)], request).findings[0].checks[0].status == "unknown"


def test_regular_price_cannot_be_selected_when_context_states_different_current_price():
    text = "Hotel A. Price now 779,99 €, regular price 549,99 €."
    output = answer("549,99 €", "549,99 €")
    request = ResearchContract(activity="service", budget=Budget(amount=600, operator="lt"))
    assert apply_contract(output, [source(text)], request).findings[0].checks[0].status == "unknown"


def test_table_hides_only_supported_generated_type_and_internal_status_column():
    output = answer()
    output.findings[0].checks.append(RequirementCheck(requirement_id="requested_type", requirement="Requested type",
        status="met", statement="Hotel", evidence=[Evidence(source_url=URL, quote="Hotel A")]))
    request = ResearchContract(answer_format="table", requirements=[
        Requirement(id="price", description="Price"), Requirement(id="requested_type", kind="type", description="Requested type")])
    text, _ = render_answer(output, [source()], request)
    assert "Requested type" not in text
    assert "Evidence status" not in text
    output.findings[0].checks[-1].status = "unknown"
    text, _ = render_answer(output, [source()], request)
    assert "Requested type" in text and "To confirm" in text and "Not found" not in text


def test_unknown_hotel_preference_does_not_repeat_accommodation_lead():
    from app.orchestration.grounding import check_text

    check = RequirementCheck(requirement_id="hotel_value", requirement="Budget hotel preference", status="unknown", statement="",
                             related_entity="Hotel A", scope="related", strength="preference", evidence=[])
    assert check_text(check, [source("## Hotel A\n\nTallinn")]) == ""


def test_compound_destination_hotel_label_is_separated_without_changing_entities():
    guide_url = "https://guide.example/estonia"
    sources = [source("Tallinn is in Estonia.", url=guide_url, title="Estonia guide"),
               source("Go Hotel Shnelli is in Tallinn.", title="Go Hotel Shnelli")]
    output = answer("Go Hotel Shnelli is in Tallinn", "Go Hotel Shnelli is in Tallinn.", related_entity="Go Hotel Shnelli")
    output.findings[0].name = "Tallinn (Hotel: Go Hotel Shnelli)"
    output.findings[0].source_url = guide_url
    output.findings[0].source_kind = "other"
    request = ResearchContract(subject="holiday spot", destination="Estonia", requirements=[
        Requirement(id="price", description="Named hotel for this destination, with its location stated", scope="related")])
    result = apply_contract(output, sources, request)
    assert result.findings[0].name == "Tallinn"
    assert result.findings[0].checks[0].status == "met"
    assert result.findings[0].checks[0].related_entity == "Go Hotel Shnelli"
    output.findings[0].name = "Tallinn (Hotel: Different Hotel)"
    assert apply_contract(output, sources, request).findings[0].name == "Tallinn (Hotel: Different Hotel)"
    output.findings[0].name = "Invented Place (Hotel: Go Hotel Shnelli)"
    assert apply_contract(output, sources, request).findings[0].name == "Invented Place (Hotel: Go Hotel Shnelli)"


def test_destination_scope_does_not_add_hard_location_and_budget_remains_preference():
    request = ResearchContract(subject="holiday spot", destination="Estonia", requirements=[
        Requirement(id="destinations_budget", kind="location", description="Holiday spots located in Estonia suitable for budget travelers")])
    budget = next(r for r in request.requirements if r.id == "destinations_budget")
    assert budget.kind == "fact" and budget.strength == "preference"
    assert not any(r.kind == "location" and r.scope == "primary" for r in request.requirements)
    assert ResearchContract.model_validate_json(request.model_dump_json()) == request


def test_explicit_hostel_type_cannot_be_recovered_as_named_hotel():
    from app.orchestration.grounding import recover_hotel_locations

    text = "## City Hostel\n\nCity Hostel is located in Tallinn, Estonia."
    output = answer("City Hostel is in Tallinn", "City Hostel is located in Tallinn, Estonia.", related_entity="City Hostel")
    output.findings[0].name = "Tallinn"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", description="Named hotel for this destination", scope="related")])
    sources = [source(text, title="City Hostel")]
    result = apply_contract(output, sources, request)
    assert result.findings[0].checks[0].status == "not_met"
    assert "hostel alternative" in result.findings[0].checks[0].statement
    assert recover_hotel_locations(result, sources) == []
    result.findings[0].checks[0].status = "unknown"
    assert recover_hotel_locations(result, sources) == []
    assert result.findings[0].checks[0].status == "unknown"


def test_generic_hotels_directory_does_not_turn_named_hostel_into_hotel():
    text = "# Hotellid\n\n## Hotels\n\n## Milton Hostel\n\nPrices starting from 15 € | Tallinna mnt 93b, Pärnu"
    output = answer("Milton Hostel is located in Pärnu", "Prices starting from 15 € | Tallinna mnt 93b, Pärnu",
                    related_entity="Milton Hostel")
    output.findings[0].name = "Pärnu"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", description="Named hotel for this destination", scope="related")])
    result = apply_contract(output, [source(text, title="Hotels in Estonia")], request)
    assert result.findings[0].checks[0].status == "not_met"
    assert answer_coverage(result, [source(text, title="Hotels in Estonia")])["verified"] == 0


@pytest.mark.parametrize("text,statement,status", [
    ("Hotel A in Tallinn. Rooms from €34.", "Hotel A offers rooms from €34", "unknown"),
    ("Hotel A in Tallinn. Affordable rooms.", "Hotel A offers affordable rooms", "met"),
])
def test_related_budget_fact_cannot_bypass_price_freshness(text, statement, status):
    output = answer(statement, text, related_entity="Hotel A")
    output.findings[0].name = "Tallinn"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", kind="fact", description="Budget hotel features", scope="related", strength="preference")])
    sources = [source(text, retrieved_at="2020-01-01T00:00:00+00:00")]
    result = apply_contract(output, sources, request)
    assert result.findings[0].checks[0].status == status


def test_fresh_nonprice_quote_cannot_launder_old_budget_amount():
    old_url = "https://hotel.example/old"
    output = answer("Hotel A offers rooms from €34", "Hotel A in Tallinn. Affordable rooms.", related_entity="Hotel A")
    output.findings[0].name = "Tallinn"
    output.findings[0].source_kind = "other"
    output.findings[0].checks[0].evidence.append(Evidence(source_url=old_url, quote="Hotel A in Tallinn. Rooms from €34."))
    sources = [source("Hotel A in Tallinn. Affordable rooms."),
               source("Hotel A in Tallinn. Rooms from €34.", url=old_url, retrieved_at="2020-01-01T00:00:00+00:00")]
    request = ResearchContract(requirements=[Requirement(id="price", kind="fact", description="Budget features", scope="related", strength="preference")])
    assert apply_contract(output, sources, request).findings[0].checks[0].status == "unknown"


@pytest.mark.parametrize("limit,status", [(700, "met"), (100, "not_met")])
def test_split_display_price_preserves_whole_euros_and_cents(limit, status):
    output = answer("€679.90", "679 90€")
    request = ResearchContract(activity="service", budget=Budget(amount=limit, operator="lt"))
    assert apply_contract(output, [source("Hotel A. 679 90€")], request).findings[0].checks[0].status == status


def test_spaced_cents_do_not_misparse_thousands_or_return_period():
    from decimal import Decimal
    from app.orchestration.grounding import monetary_values

    assert monetary_values("1 679€", "EUR") == {Decimal("1679")}
    assert monetary_values("€1 679", "EUR") == {Decimal("1679")}
    assert monetary_values("1 679 90€", "EUR") == {Decimal("1679.90")}
    assert monetary_values("€679 90 day returns", "EUR") == {Decimal("679")}


def test_parenthetical_was_price_is_not_a_conflicting_current_offer():
    output = answer("649€", "649€ (was1099€)")
    request = ResearchContract(activity="service", budget=Budget(amount=700, operator="lt"))
    assert apply_contract(output, [source("Hotel A. 649€ (was1099€)")], request).findings[0].checks[0].status == "met"


def test_primary_budget_fact_cannot_publish_stale_room_rates():
    text = "Narva offers rooms from $24 per night."
    output = answer(text, text)
    output.findings[0].name = "Narva"
    output.findings[0].source_kind = "other"
    request = ResearchContract(subject="holiday spot", requirements=[Requirement(id="price", kind="fact", description="Budget-friendly destination features", strength="preference")])
    result = apply_contract(output, [source(text, title="Narva", retrieved_at="2020-01-01T00:00:00+00:00")], request)
    assert result.findings[0].checks[0].status == "unknown"


def test_budget_recovery_keeps_free_activity_but_drops_stale_food_rate():
    from app.orchestration.grounding import recover_budget_features

    text = "# Narva\n\nWalking around the castle grounds is free. Meals cost €5."
    output = answer("Narva has free castle walks and meals for €5", "Walking around the castle grounds is free. Meals cost €5.")
    output.findings[0].name = "Narva"
    output.findings[0].source_kind = "other"
    request = ResearchContract(subject="holiday spot", requirements=[Requirement(id="price", kind="fact", description="Budget-friendly destination features", strength="preference")])
    sources = [source(text, title="Narva", retrieved_at="2020-01-01T00:00:00+00:00")]
    result = apply_contract(output, sources, request)
    assert result.findings[0].checks[0].status == "unknown"
    repairs = recover_budget_features(result, sources)
    assert repairs and repairs[0]["review_required"] is True
    check = result.findings[0].checks[0]
    assert "walking around the castle grounds is free." in check.statement
    assert "€5" not in check.statement and "meals" not in check.statement
    assert check.evidence[0].passage_id


@pytest.mark.parametrize("text,title", [
    ("# Hotel B\n\nHotel B offers affordable rooms.", "Hotel B"),
    ("# Hotel A\n\nHotel A is not affordable and parking is not free.", "Hotel A"),
    ("# Hotel A\n\nIs parking free?", "Hotel A"),
    ("# Hotel A\n\nHotel A offers rooms for €50.", "Hotel A"),
])
def test_budget_recovery_rejects_other_candidate_negated_free_and_prices(text, title):
    from app.orchestration.grounding import recover_budget_features

    output = answer()
    output.findings[0].checks[0].status = "unknown"
    output.findings[0].checks[0].requirement = "Budget-friendly features"
    assert not recover_budget_features(output, [source(text, title=title)])
    assert output.findings[0].checks[0].status == "unknown"


def test_budget_feature_recovery_does_not_establish_numeric_budget():
    from app.orchestration.grounding import recover_budget_features

    output = answer()
    output.findings[0].checks[0].status = "unknown"
    output.findings[0].checks[0].requirement = "Budget under €150"
    assert not recover_budget_features(output, [source("Hotel A offers free breakfast.")])


def test_budget_recovery_does_not_overwrite_unresolved_conflict():
    from app.orchestration.grounding import recover_budget_features

    output = answer()
    check = output.findings[0].checks[0]
    check.status, check.requirement, check.reason = "unknown", "Budget-friendly features", "Conflicting evidence remains."
    assert not recover_budget_features(output, [source("Hotel A offers affordable rooms.")])


def test_historical_monetary_fact_is_not_treated_as_current_travel_price():
    text = "Entity A received €150 in 2010."
    output = answer(text, text)
    output.findings[0].name = "Entity A"
    output.findings[0].source_kind = "other"
    request = ResearchContract(subject="economic history", requirements=[Requirement(id="price", description="Recorded transaction in 2010")])
    check = apply_contract(output, [source(text, title="Entity A", retrieved_at="2020-01-01T00:00:00+00:00")], request).findings[0].checks[0]
    assert check.basis == "reported" and check.statement == text
    assert "current" not in check.reason


def test_extractive_budget_replaces_exaggerated_activity_comparison():
    from app.orchestration.grounding import recover_budget_features

    sentence = "Tartu has cheaper cafes and accommodation than Tallinn, with similar activity prices."
    output = answer("Tartu is cheaper for food, lodging and cultural activities", sentence)
    output.findings[0].name = "Tartu"
    check = output.findings[0].checks[0]
    check.requirement = "Budget-friendly destinations"
    repairs = recover_budget_features(output, [source(sentence, title="Estonia budget guide")], replace_assertions=True)
    assert repairs and repairs[0]["review_required"]
    assert check.statement == sentence.casefold()
    assert "similar activity prices" in check.statement
    assert "cultural" not in check.statement


def test_extractive_budget_does_not_expand_cheap_hotels_into_cheap_food():
    from app.orchestration.grounding import recover_budget_features

    sentence = "Hotels in Narva are cheaper than hotels in Tallinn."
    output = answer("Narva is cheapest for lodging and food", sentence)
    output.findings[0].name = "Narva"
    check = output.findings[0].checks[0]
    check.requirement = "Budget-friendly destination"
    recover_budget_features(output, [source(sentence, title="Hotel averages")], replace_assertions=True)
    assert check.statement == sentence.casefold()
    assert "food" not in check.statement


@pytest.mark.parametrize("scope", ["primary", "related"])
def test_free_wifi_from_another_hotel_cannot_establish_destination_or_selected_hotel_budget(scope):
    from app.orchestration.grounding import recover_budget_features

    text = "# Other Hotel\n\nOther Hotel in Tallinn provides free WiFi and free parking."
    output = answer("Selected Hotel is affordable", "Other Hotel in Tallinn provides free WiFi and free parking.")
    output.findings[0].name = "Tallinn"
    check = output.findings[0].checks[0]
    check.requirement, check.scope = "Budget-friendly features", scope
    check.related_entity = "Selected Hotel" if scope == "related" else None
    assert not recover_budget_features(output, [source(text, title="Tallinn hotels")], replace_assertions=True)
    assert check.status == "unknown" and not check.statement


def test_related_budget_requires_own_property_section_for_unnamed_sentence():
    from app.orchestration.grounding import recover_budget_features

    text = "# Hotel A\n\nHotel A is in Tallinn.\n\n## Other Hotel\n\nAffordable rooms are offered here."
    output = answer("Hotel A is affordable", "Affordable rooms are offered here.", related_entity="Hotel A")
    output.findings[0].name = "Tallinn"
    check = output.findings[0].checks[0]
    check.requirement, check.scope = "Budget-friendly hotel", "related"
    assert not recover_budget_features(output, [source(text)], replace_assertions=True)
    assert check.status == "unknown"


def test_extractive_budget_keeps_own_explicit_affordability_and_free_city_activity():
    from app.orchestration.grounding import recover_budget_features

    text = "# Hotel A\n\nHotel A offers affordable rooms in Tallinn."
    output = answer("Hotel A is ideal for cheap cultural activities", "Hotel A offers affordable rooms in Tallinn.", related_entity="Hotel A")
    output.findings[0].name = "Tallinn"
    check = output.findings[0].checks[0]
    check.requirement, check.scope = "Budget-friendly hotel", "related"
    assert recover_budget_features(output, [source(text)], replace_assertions=True)
    assert check.statement == "hotel a offers affordable rooms in tallinn."


@pytest.mark.parametrize("heading", ["Tartu City", "Tartu: Student Capital", "Tartu — Student Capital"])
def test_budget_extraction_accepts_explicit_city_heading_and_lower_cost_sentence(heading):
    from app.orchestration.grounding import recover_budget_features

    sentence = "Accommodation costs 20 percent less than in Tallinn."
    output = answer("Tartu is cheap for all activities", sentence)
    output.findings[0].name = "Tartu"
    check = output.findings[0].checks[0]
    check.requirement = "Budget-friendly destination"
    text = f"# {heading}\n\n{sentence}"
    assert recover_budget_features(output, [source(text, title="Estonia guide")], replace_assertions=True)
    assert check.statement == "Tartu: " + sentence.casefold()


def test_catalog_count_fragment_is_not_a_destination_budget_sentence():
    from app.orchestration.grounding import recover_budget_features

    output = answer("Tallinn has affordable culture and food", "749 cheap hotels")
    output.findings[0].name = "Tallinn"
    check = output.findings[0].checks[0]
    check.requirement = "Budget-friendly destination"
    assert not recover_budget_features(output, [source("# Tallinn\n\n749 cheap hotels", title="Estonia hotels")], replace_assertions=True)
    assert check.status == "unknown"


def test_primary_budget_does_not_promote_other_linked_hotel_options():
    from app.orchestration.grounding import recover_budget_features

    text = "# Pärnu\n\n[Austri Villa](https://booking.example/hotel/ee/austri), [Alex Maja](https://booking.example/hotel/ee/alex), and [Hedon Spa & Hotel](https://booking.example/hotel/ee/hedon) are popular cheap hotels in Pärnu."
    output = answer("Pärnu is affordable", "Unused quote")
    finding = output.findings[0]
    finding.name = "Pärnu"
    finding.checks[0].requirement = "Budget-friendly destination"
    finding.checks.append(RequirementCheck(requirement_id="hotel", requirement="Named hotel", status="unknown",
        statement="", scope="related", related_entity="Hotel Legend", evidence=[]))
    assert not recover_budget_features(output, [source(text, title="Hotels in Pärnu")], replace_assertions=True)
    assert finding.checks[0].status == "unknown"


@pytest.mark.parametrize("label", ["/ Cheapest rate per night", "Lowest price", "Best available rate per room"])
def test_generic_rate_ui_label_does_not_prove_related_hotel_affordability(label):
    from app.orchestration.grounding import recover_budget_features

    text = f"# Hotel A\n\nHotel A is in Tallinn.\n\n{label}"
    output = answer("Hotel A is affordable", label, related_entity="Hotel A")
    output.findings[0].name = "Tallinn"
    check = output.findings[0].checks[0]
    check.requirement, check.scope = "Budget-friendly hotel", "related"
    assert not recover_budget_features(output, [source(text)], replace_assertions=True)
    assert check.status == "unknown"


def test_scoped_cheap_hotel_category_survives_rate_ui_filter():
    from app.orchestration.grounding import recover_budget_features

    sentence = "Popular with guests booking cheap hotels in Tallinn"
    text = "# Hotel A\n\nHotel A is in Tallinn.\n\n" + sentence
    output = answer("Hotel A is affordable", sentence, related_entity="Hotel A")
    output.findings[0].name = "Tallinn"
    check = output.findings[0].checks[0]
    check.requirement, check.scope = "Budget-friendly hotel", "related"
    assert recover_budget_features(output, [source(text)], replace_assertions=True)
    assert check.statement == "Hotel A: " + sentence.casefold()


def test_hotel_location_can_expand_to_same_page_address_passage():
    from app.orchestration.grounding import candidate_evidence, Finding, RequirementCheck, Evidence
    source={'url':'https://hotel.example/contact', 'title':'Contact', 'text':'# Contact\n\n### Go Hotel Shnelli\n\nToompuiestee 37/1, Tallinn, Estonia\n\n### Nearby\n\nThe Old Town 50 m'}
    check=RequirementCheck(requirement='Hotel for Tallinn', status='met', statement='Go Hotel Shnelli is 50 m from the Old Town in Tallinn.', scope='related', related_entity='Go Hotel Shnelli', evidence=[Evidence(source_url=source['url'],quote='The Old Town 50 m')])
    result=candidate_evidence(Finding(name='Tallinn',checks=[check]),check,[source])
    assert any('tallinn' in e.quote for e in result)
    other=source|{'text':source['text'].replace('Go Hotel Shnelli','Other Hotel')}
    assert not candidate_evidence(Finding(name='Tallinn',checks=[check]),check,[other])


def test_hotel_prefix_is_not_a_different_property_name():
    from app.orchestration.grounding import candidate_evidence, Finding, RequirementCheck, Evidence
    source={'url':'https://hotel.example/a','title':'Hansalinn','text':'# Hansalinn\n\nHansalinn is located in Pärnu.'}
    check=RequirementCheck(requirement='Hotel for Pärnu',status='met',statement='Hansalinn is located in Pärnu.',scope='related',related_entity='Hotell Hansalinn',evidence=[Evidence(source_url=source['url'],quote='Hansalinn is located in Pärnu.')])
    assert candidate_evidence(Finding(name='Pärnu',checks=[check]),check,[source])
    check.related_entity='Hotell Othername'
    assert not candidate_evidence(Finding(name='Pärnu',checks=[check]),check,[source])


def test_unknown_hotel_relation_recovers_only_from_literal_same_property_location():
    from app.orchestration.grounding import recover_hotel_locations, GroundedAnswer, Finding, RequirementCheck
    check=RequirementCheck(requirement='Named hotel for this destination, with its location or proximity stated',requirement_id='hotel',status='unknown',statement='',scope='related',related_entity='Hotel A',evidence=[])
    answer=GroundedAnswer(findings=[Finding(name='Tallinn',checks=[check])])
    wrong={'url':'https://example.com/b','title':'Hotel B','text':'# Hotel B\n\nHotel B is in Tallinn.'}
    assert not recover_hotel_locations(answer,[wrong])
    assert check.status=='unknown'
    own={'url':'https://example.com/a','title':'Hotel A','text':'# Hotel A\n\nHotel A is in Tallinn.'}
    repairs=recover_hotel_locations(answer,[own])
    assert repairs[0]['review_required'] is True
    assert check.evidence[0].quote=='hotel a is in tallinn.'
    assert check.status=='met'


def test_shortened_property_name_cannot_hide_explicit_hostel_type_in_page_body():
    from app.orchestration.grounding import recover_hotel_locations

    text = ("# Viru Backpackers\n\nViru 5, Tallinn, Estonia\n\n## Overview\n\n"
            "Viru Backpackers Hostel is a small property in Tallinn's old town.")
    output = answer("Viru Backpackers is located in Tallinn", "Viru 5, Tallinn, Estonia",
                    related_entity="Viru Backpackers")
    output.findings[0].name = "Tallinn"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", description="Named hotel for this destination", scope="related")])
    result = apply_contract(output, [source(text, title="Viru Backpackers Hotel Deals")], request)
    check = result.findings[0].checks[0]
    assert check.status == "not_met" and "hostel alternative" in check.statement
    check.status, check.evidence = "unknown", []
    assert not recover_hotel_locations(result, [source(text, title="Viru Backpackers Hotel Deals")])


@pytest.mark.parametrize("name,text,title", [
    ("Old Town Munkenhof Guesthouse", "Old Town Munkenhof Guesthouse – cosy rooms and medieval Tallinn outside the door.", "Munkenhof"),
    ("Hektor Design Hostel", "Riia street 26, Tartu 50405, Estonia", "Hostel Tartu | Room for 1 | Hektor Design Hostel"),
    ("Sagadi Manor Hostel", "Sagadi Manor Hostel: a hostel inside Sagadi Manor within Lahemaa National Park.", "Budget Lahemaa")])
def test_cited_typed_property_does_not_require_markdown_heading_to_fail_hotel(name, text, title):
    output = answer(f"{name} is located in this destination", text, related_entity=name)
    output.findings[0].name = "Tallinn"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", kind="type", description="Named hotel for this destination", scope="related")])
    result = apply_contract(output, [source(text, title=title)], request)
    check = result.findings[0].checks[0]
    assert check.status == "not_met"
    assert check.evidence


def test_unsourced_model_guess_of_hostel_name_cannot_create_a_type_contradiction():
    text = "Munkenhof offers rooms in Tallinn."
    output = answer("Munkenhof Guesthouse is in Tallinn", text, related_entity="Munkenhof Guesthouse")
    output.findings[0].name = "Tallinn"
    output.findings[0].source_kind = "other"
    request = ResearchContract(requirements=[Requirement(id="price", kind="type", description="Named hotel for this destination", scope="related")])
    result = apply_contract(output, [source(text, title="Munkenhof")], request)
    assert result.findings[0].checks[0].status == "unknown"


def test_location_recovery_cannot_use_unrelated_city_from_long_tour_description():
    from app.orchestration.grounding import recover_destination_locations

    text = ("# Viru Backpackers\n\nViru Backpackers Hostel is in Tallinn's old town. "
            "If you are seeing more of Estonia, ask about day trips to Lahemaa National Park.")
    output = GroundedAnswer(findings=[Finding(name="Lahemaa National Park", checks=[
        RequirementCheck(requirement="Located in Estonia", requirement_id="location", status="unknown", statement="", evidence=[])])])
    assert not recover_destination_locations(output, [source(text)])
    assert output.findings[0].checks[0].status == "unknown"
    check = output.findings[0].checks[0]
    check.status = "met"
    check.statement = "Lahemaa National Park: Viru Backpackers Hostel is in Tallinn's old town."
    check.evidence = [Evidence(source_url=URL, quote=text.split("\n\n", 1)[1])]
    request = ResearchContract(requirements=[Requirement(id="location", kind="location", description="Located in Estonia")])
    revalidated = apply_contract(output, [source(text)], request)
    assert revalidated.findings[0].checks[0].status == "unknown"


@pytest.mark.parametrize("quote", ["Lahemaa National Park is in northern Estonia.", "Lahemaa National Park, Estonia",
                                  "Lahemaa National Park: Estonia's national park"])
def test_location_recovery_uses_direct_candidate_location_sentence(quote):
    from app.orchestration.grounding import recover_destination_locations

    output = GroundedAnswer(findings=[Finding(name="Lahemaa National Park", checks=[
        RequirementCheck(requirement="Located in Estonia", requirement_id="location", status="unknown", statement="", evidence=[])])])
    assert recover_destination_locations(output, [source(quote)])
    check = output.findings[0].checks[0]
    assert check.status == "met"
    assert check.statement.casefold() == quote.casefold()
