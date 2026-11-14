from app.orchestration.contracts import ResearchContract, Requirement
from app.orchestration.grounding import GroundedAnswer, apply_contract, render_answer


def test_service_contract_keeps_type_location_and_booking_without_product_checks():
    contract = ResearchContract(activity="service", shopping=True, subject="floating sauna", anchor="Hotel Matts in Espoo",
                                booking_required=True, destination="Finland")
    assert not contract.shopping
    assert contract.requested_count is None
    assert {r.kind for r in contract.requirements} == {"type", "location", "booking"}
    assert any("floating sauna" in r.description for r in contract.requirements)
    assert any("Hotel Matts" in r.description for r in contract.requirements)
    assert not any(r.kind in {"stock", "delivery", "availability"} for r in contract.requirements)


def test_bookability_and_a_requested_time_slot_remain_separate():
    contract = ResearchContract(activity="service", booking_required=True, requirements=[
        Requirement(id="saturday", kind="availability", description="Available on the requested Saturday at 18:00")])
    assert {r.kind for r in contract.requirements} == {"booking", "availability"}


def test_generated_checks_replace_reserved_ids_and_survive_round_trip():
    contract = ResearchContract(activity="service", subject="floating sauna", anchor="Hotel Matts",
                                destination="Finland", booking_required=True, requirements=[
        Requirement(id="type", description="Floating sauna"),
        Requirement(id="booking", description="For hire"),
        Requirement(id="location", description="Nearby"),
    ])
    assert {r.id for r in contract.requirements} == {"requested_type", "booking", "location", "proximity"}
    assert len(contract.requirements) == 4
    assert ResearchContract.model_validate_json(contract.model_dump_json()) == contract


def test_compound_requirement_cannot_hide_missing_proximity_evidence():
    contract = ResearchContract(activity="service", anchor="Hotel Matts", requirements=[
        Requirement(id="combined", description="Floating sauna near Hotel Matts available for hire")])
    assert {r.id for r in contract.requirements} == {"combined", "proximity"}
    assert "shared city alone" in contract.requirements[-1].description


def test_destination_does_not_become_its_own_proximity_constraint():
    contract = ResearchContract(activity="product", destination="Finland", anchor="Finland")
    assert contract.anchor is None
    assert any(r.kind == "delivery" for r in contract.requirements)
    assert not any(r.id == "proximity" for r in contract.requirements)


def test_saved_service_facts_survive_missing_proximity_evidence():
    url = "https://sauna.example/venue"
    sources = [{"url": url, "title": "Sauna A", "text": "# Sauna A\n\nA **floating sauna**.\n\n[Available for private hire](https://sauna.example/book)."}]
    contract = ResearchContract(activity="service", subject="floating sauna", anchor="Hotel Matts", booking_required=True)
    answer = GroundedAnswer.model_validate({"findings": [{"name": "Sauna A", "source_kind": "service", "source_url": url, "checks": [
        {"requirement_id": "requested_type", "requirement": "Floating sauna", "status": "met", "statement": "A floating sauna.",
         "evidence": [{"source_url": url, "quote": "A floating sauna."}]},
        {"requirement_id": "booking", "requirement": "Bookable", "status": "met", "statement": "Available for private hire.",
         "evidence": [{"source_url": url, "quote": "Available for private hire"}]},
    ]}]})
    output = apply_contract(answer, sources, contract)
    text, issues = render_answer(output, sources)
    assert "A floating sauna" in text
    assert "Available for private hire" in text
    assert "Hotel Matts" in text and "To confirm" in text and "Not found" not in text
    assert "stock" not in text and "deliver" not in text
    assert not issues


def test_unrequested_result_count_is_not_invented(monkeypatch):
    from app.orchestration import planner
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    async def model(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "contract": {"activity": "service", "requested_count": 5, "subject": "floating sauna", "booking_required": True},
            "queries": [{"query": "saunalautta Espoo", "objective": "Find floating sauna hire in Espoo"}],
        })])
    monkeypatch.setattr(planner, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(planner, "GoogleModel", lambda *args, **kwargs: FunctionModel(model))
    plan, _ = planner.plan_searches("Find floating saunas near Hotel Matts that can be hired.")
    assert plan.contract.requested_count is None
