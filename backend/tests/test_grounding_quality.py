import json
from datetime import UTC, datetime
import pytest

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.orchestration.contracts import ResearchContract, Requirement
from app.orchestration.grounding import GroundedAnswer, apply_contract
from app.orchestration.verification import verify_claims


URL = "https://sauna.example/booking"
SOURCES = [{"url": URL, "title": "Sauna A", "text": "Sauna A costs €25 per person. Book online."}]


def answer():
    return GroundedAnswer.model_validate({"findings": [{
        "name": "Sauna A", "source_url": URL, "source_kind": "service", "checks": [
            {"requirement_id": "price", "requirement": "Price", "status": "met",
             "statement": "€25 per person", "evidence": [{"source_url": URL, "quote": "€25 per person"}]},
            {"requirement_id": "booking", "requirement": "Bookable", "status": "met",
             "statement": "Book online", "evidence": [{"source_url": URL, "quote": "Book online."}]},
        ]}]})


def test_service_prices_require_fresh_evidence_but_booking_invitation_does_not():
    contract = ResearchContract(activity="service", booking_required=True, requirements=[
        Requirement(id="price", kind="price", description="Current price")])
    output = apply_contract(answer(), SOURCES, contract)
    assert [c.status for c in output.findings[0].checks] == ["unknown", "met"]
    fresh = [{**SOURCES[0], "retrieval": "live", "retrieved_at": datetime.now(UTC).isoformat()}]
    assert apply_contract(answer(), fresh, contract).findings[0].checks[0].status == "met"


def test_missing_quotes_rejected_without_model_call():
    output = answer()
    output.findings[0].source_kind = "other"
    for check in output.findings[0].checks:
        check.evidence[0].quote = "Fabricated"

    async def model(messages, info):
        raise AssertionError("Missing evidence must not consume a model call")

    result, trace = verify_claims(output, SOURCES, FunctionModel(model))
    assert all(c.status == "unknown" for c in result.findings[0].checks)
    assert len(trace["rejected"]) == 2


def test_duplicate_or_reserved_requirement_ids_do_not_reject_other_checks():
    output = answer()
    output.findings[0].source_kind = "other"
    for check in output.findings[0].checks:
        check.requirement_id = "description"

    async def model(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"judgments": [
            {"claim_id": 0, "supported": False, "reason": "Unsupported price"},
            {"claim_id": 1, "supported": True, "reason": "Explicit booking invitation"},
        ]})])

    result, _ = verify_claims(output, SOURCES, FunctionModel(model))
    assert [c.status for c in result.findings[0].checks] == ["unknown", "met"]


def test_general_response_is_audited_against_cited_page():
    output = GroundedAnswer(response=f"Sauna A is free. [Booking]({URL})")
    observed = []

    async def model(messages, info):
        observed.extend(json.loads(messages[0].parts[-1].content))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"judgments": [
            {"claim_id": 0, "supported": False, "reason": "Page states €25"},
        ]})])

    result, trace = verify_claims(output, SOURCES, FunctionModel(model))
    assert not result.response
    assert observed[0]["evidence"][0]["context"] == SOURCES[0]["text"]
    assert trace["rejected"]


def test_uncited_general_response_is_rejected_without_model_call():
    async def model(messages, info):
        raise AssertionError("No cited evidence")

    result, _ = verify_claims(GroundedAnswer(response="Sauna A is free."), SOURCES, FunctionModel(model))
    assert not result.response


def test_comparison_listing_cannot_become_verified_retailer_offer():
    output = answer()
    finding = output.findings[0]
    finding.source_kind = "product"
    finding.source_url = "https://priceinfo.fi/product/laptop"
    assert not apply_contract(output, SOURCES, ResearchContract(shopping=True)).findings


def test_stock_quote_cannot_discard_unconfirmed_status_qualifier():
    output = answer()
    finding = output.findings[0]
    finding.source_kind = "product"
    finding.checks = [finding.checks[0]]
    check = finding.checks[0]
    check.requirement_id = "stock"
    check.statement = "In stock"
    check.evidence[0].quote = "Saatavilla"
    sources = [{**SOURCES[0], "text": "Sauna A. Ei äskettäin vahvistettu Saatavilla",
                "retrieval": "live", "retrieved_at": datetime.now(UTC).isoformat()}]
    result = apply_contract(output, sources, ResearchContract(shopping=True))
    assert result.findings[0].checks[0].status == "unknown"


def test_general_research_pipeline_runs_semantic_verification(monkeypatch):
    from app import chat
    from pydantic_ai.messages import TextPart

    async def model(messages, info):
        if not info.output_tools:
            return ModelResponse(parts=[TextPart(f"Sauna A is free. [Booking]({URL})")])
        tool = info.output_tools[0]
        if "judgments" in tool.parameters_json_schema["properties"]:
            result = {"judgments": [{"claim_id": 0, "supported": False, "reason": "Price is not free"}]}
        else:
            result = GroundedAnswer(response=f"Sauna A is free. [Booking]({URL})").model_dump()
        return ModelResponse(parts=[ToolCallPart(tool.name, result)])

    monkeypatch.setattr(chat, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(chat, "GoogleModel", lambda *args, **kwargs: FunctionModel(model))
    reply, trace = chat.generate_reply("Tell me about Sauna A", [], SOURCES, contract=ResearchContract())
    assert "is free" not in reply
    assert trace["verification"]["rejected"]
    assert trace["usage"]["requests"] == 2


@pytest.mark.parametrize("source_kind", ["comparison", "product"])
def test_filtered_discovery_candidates_survive_in_followup_gaps(monkeypatch, source_kind):
    from app import chat

    async def model(messages, info):
        tool = info.output_tools[0]
        if "judgments" in tool.parameters_json_schema["properties"]:
            claims = json.loads(messages[0].parts[-1].content)
            return ModelResponse(parts=[ToolCallPart(tool.name, {"judgments": [
                {"claim_id": claim["claim_id"], "supported": False, "reason": "Category page, not an offer"}
                for claim in claims]})])
        output = answer()
        output.findings[0].source_kind = source_kind
        return ModelResponse(parts=[ToolCallPart(tool.name, output.model_dump())])

    monkeypatch.setattr(chat, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(chat, "GoogleModel", lambda *args, **kwargs: FunctionModel(model))
    reply, trace = chat.generate_reply("Buy Sauna A", [], SOURCES, contract=ResearchContract(shopping=True))
    assert trace["coverage"]["retained"] == 0
    assert trace["coverage"]["verified"] == 0
    assert trace["coverage"]["discovery_leads"][0]["name"] == "Sauna A"
    assert trace["coverage"]["gaps"][0]["source_url"] == URL
    assert trace["coverage"]["gaps"][0]["discovery_only"]
    assert "### 1." not in reply


@pytest.mark.parametrize("text,expected", [
    ("Toimitusaika: 1–2 arkipäivää", False),
    ("Available from https://dakauf.eu/fi", False),
    ("Toimitus Suomeen 1–2 arkipäivää", True),
    ("Delivery to Finland", True),
    ("Nouto Helsinki", True),
])
def test_delivery_requires_destination_evidence(text, expected):
    output = answer()
    finding = output.findings[0]
    finding.source_kind = "product"
    check = finding.checks[0]
    finding.checks = [check]
    check.requirement_id = "delivery"
    check.evidence[0].quote = text
    check.statement = text
    sources = [{**SOURCES[0], "text": text, "retrieval": "page", "observed_at": datetime.now(UTC).isoformat()}]
    result = apply_contract(output, sources, ResearchContract(shopping=True, destination="Finland"))
    delivery = next(c for c in result.findings[0].checks if c.requirement_id == "delivery")
    assert (delivery.status == "met") == expected


def test_category_patterns_preserve_individual_product_paths():
    from app.orchestration.grounding import category_source

    assert category_source("https://www.gigantti.fi/tietokoneet/macbook/page-2")
    assert not category_source("https://www.gigantti.fi/product/tietokoneet/macbook/123")
    assert category_source("https://shop.example/category/laptops")
    assert not category_source("https://shop.example/collections/laptops/products/model-a")


def test_rendering_removes_internal_source_tokens_without_changing_units():
    from app.orchestration.grounding import plain

    assert plain("€29 per person [S1] 2 hours [S23]") == "€29 per person 2 hours"
    assert plain("16 GB RAM") == "16 GB RAM"


def test_service_description_cannot_bypass_unknown_price_check():
    output = answer()
    output.findings[0].description = "€25 per person"
    output.findings[0].description_evidence = output.findings[0].checks[0].evidence
    output.findings[0].assessment = "Affordable at €25"
    contract = ResearchContract(activity="service", requirements=[Requirement(id="price", kind="price", description="Price")])
    result = apply_contract(output, SOURCES, contract)
    assert result.findings[0].checks[0].status == "unknown"
    assert not result.findings[0].description
    assert not result.findings[0].assessment


def test_verifier_receives_status_to_distinguish_satisfaction_from_true_statement():
    output = answer()
    output.findings[0].source_kind = "other"
    output.findings[0].checks[0].status = "not_met"
    observed = []

    async def model(messages, info):
        observed.extend(json.loads(messages[0].parts[-1].content))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"judgments": [
            {"claim_id": claim["claim_id"], "supported": True, "reason": "Checked"} for claim in observed]})])

    verify_claims(output, SOURCES, FunctionModel(model))
    assert [claim["claimed_status"] for claim in observed] == ["not_met", "met"]
    assert all(claim['basis'] == 'confirmed' for claim in observed)


@pytest.mark.parametrize("with_sources", [True, False])
def test_research_history_excludes_assistant_answers_but_chat_retains_them(monkeypatch, with_sources):
    from app import chat
    from app.schemas import ChatTurn
    from pydantic_ai.messages import TextPart

    async def model(messages, info):
        if info.output_tools:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                GroundedAnswer(response=f"Sauna A costs €25 per person. [Source]({URL})",
                               limitations=["All requested details found."]).model_dump())])
        return ModelResponse(parts=[TextPart("Normal chat reply")])

    monkeypatch.setattr(chat, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(chat, "GoogleModel", lambda *args, **kwargs: FunctionModel(model))
    history = [ChatTurn(role="user", content="Find a sauna"),
               ChatTurn(role="assistant", content="Previous unsupported claim: free sauna")]
    _, trace = chat.generate_reply("Continue", history, SOURCES if with_sources else None)
    assert ("Previous unsupported claim" in trace["input"]) == (not with_sources)
    assert "Find a sauna" in trace["input"]
    assert not trace["has_gaps"]


def test_generated_completeness_claim_cannot_override_unknown_checks():
    from app.orchestration.grounding import render_answer

    output = answer()
    output.findings[0].checks[0].status = "unknown"
    output.limitations = ["All requested details found."]
    text, _ = render_answer(output, SOURCES)
    assert "All requested details found" not in text
    assert "To confirm" in text
    assert "not found" not in text.casefold()


def test_offer_review_includes_product_after_long_navigation():
    output = answer()
    output.findings[0].source_kind = "product"
    for check in output.findings[0].checks:
        check.status = "unknown"
    sources = [{**SOURCES[0], "text": "Menu navigation accessories. " * 200 +
                "\nSauna A €2500. In stock. Buy this product.\n" + "Footer information. " * 200}]
    observed = []

    async def model(messages, info):
        observed.extend(json.loads(messages[0].parts[-1].content))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"judgments": [
            {"claim_id": 0, "supported": True, "reason": "Individual offer"}]})])

    verify_claims(output, sources, FunctionModel(model))
    assert "Sauna A €2500. In stock." in observed[0]["evidence"][0]["context"]


def test_product_availability_cannot_bypass_stock_freshness():
    output = answer()
    finding = output.findings[0]
    finding.source_kind = "product"
    check = finding.checks[0]
    check.requirement_id = "availability"
    contract = ResearchContract(activity="product", requirements=[
        Requirement(id="availability", kind="availability", description="Current availability")])
    result = apply_contract(output, SOURCES, contract)
    assert result.findings[0].checks[0].status == "unknown"


def test_verified_booking_href_is_clickable_in_rendered_answer():
    from app.orchestration.grounding import check_text, Evidence, RequirementCheck
    url = 'https://booking.example/venue'
    source = {'url': 'https://operator.example/sauna', 'title': 'Venue A',
              'text': f'[Book Venue A]({url})'}
    check = RequirementCheck(requirement='Booking link', status='met', statement=f'Book at {url}',
        evidence=[Evidence(source_url=source['url'], quote='Book Venue A')])
    assert f'](<{url}>)' in check_text(check, [source])


@pytest.mark.parametrize('text,status', [('Price €649.', 'met'), ('Price €49 per month.', 'unknown')])
def test_product_total_price_does_not_require_literal_total_word(text, status):
    from app.orchestration.contracts import Budget
    from app.orchestration.grounding import apply_budget, Evidence, RequirementCheck
    source = {'url': 'https://shop.example/laptop', 'title': 'Laptop A', 'text': text}
    check = RequirementCheck(requirement='Under €700 total', status='met', statement=text,
        evidence=[Evidence(source_url=source['url'], quote=text)])
    apply_budget(check, [source], Budget(amount=700, operator='lt', basis='total'), item_price=True)
    assert check.status == status
