import json

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.orchestration.grounding import GroundedAnswer
from app.orchestration.verification import resolve_source_ids, verify_claims


SOURCES = [{"url": "https://review.example/linux", "title": "Linux guide",
            "text": "Linux works on many business laptops. Laptop A has 16 GB RAM."}]


def candidate():
    return GroundedAnswer.model_validate({"findings": [{"name": "Laptop A", "source_url": "S1", "checks": [
        {"requirement_id": "linux", "requirement": "Linux support", "status": "met", "statement": "Laptop A supports Linux.",
         "evidence": [{"source_url": "S1", "quote": "Linux works on many business laptops."}]},
        {"requirement_id": "ram", "requirement": "16 GB RAM", "status": "met", "statement": "Laptop A has 16 GB RAM.",
         "evidence": [{"source_url": "S1", "quote": "Laptop A has 16 GB RAM."}]},
    ]}]})


def test_short_source_ids_resolve_before_quote_validation():
    answer = resolve_source_ids(candidate(), SOURCES)
    assert answer.findings[0].source_url == SOURCES[0]["url"]
    assert all(check.evidence[0].source_url == SOURCES[0]["url"] for check in answer.findings[0].checks)


def test_rejected_and_missing_verdicts_are_unknown_and_cached():
    requests = []
    async def model(messages, info):
        requests.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "judgments": [{"claim_id": 0, "supported": False, "reason": "General advice does not establish model-specific compatibility."}],
        })])
    answer = resolve_source_ids(candidate(), SOURCES)
    cache = {}
    for _ in range(2):
        output, trace = verify_claims(answer, SOURCES, FunctionModel(model), cache)
        assert all(check.status == "unknown" for check in output.findings[0].checks)
        assert all(not check.evidence for check in output.findings[0].checks)
        assert len(trace["rejected"]) == 2
    assert len(requests) == 1
    assert trace["cached"] == 2
    assert answer.findings[0].checks[0].status == "met"


def test_verifier_receives_exact_candidate_requirement_quote_and_context():
    observed = []
    async def model(messages, info):
        prompt = messages[0].parts[-1].content
        claims = json.loads(prompt)
        observed.extend(claims)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"judgments": [
            {"claim_id": claim["claim_id"], "supported": claim["field"] == "ram", "reason": "Specific evidence only."}
            for claim in claims]})])
    answer = resolve_source_ids(candidate(), SOURCES)
    output, trace = verify_claims(answer, SOURCES, FunctionModel(model))
    assert [check.status for check in output.findings[0].checks] == ["unknown", "met"]
    assert observed[0]["candidate"] == "Laptop A"
    assert observed[0]["requirement"] == "Linux support"
    assert observed[0]["evidence"][0]["quote"] == "Linux works on many business laptops."
    assert "Laptop A has 16 GB RAM." in observed[0]["evidence"][0]["context"]
    assert trace["usage"]["requests"] == 1


def test_offer_opening_does_not_end_in_a_partial_word():
    source = {"url": "https://shop.example/a", "title": "Laptop A", "text": "Laptop A. " + "specifications " * 200}
    answer = candidate()
    answer.findings[0].source_url = source["url"]
    answer.findings[0].source_kind = "product"
    for check in answer.findings[0].checks:
        check.status = "unknown"
    observed = []
    async def model(messages, info):
        claims = json.loads(messages[0].parts[-1].content)
        observed.extend(claims)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"judgments": [
            {"claim_id": 0, "supported": True, "reason": "Individual offer."}]})])
    output, _ = verify_claims(answer, [source], FunctionModel(model))
    assert observed[0]["evidence"]
    assert observed[0]["evidence"][0]["quote"] in source["text"]
    assert output.findings[0].source_kind == "product"


def test_shopping_pipeline_resolves_ids_audits_claims_and_counts_verification_usage(monkeypatch):
    from app import chat
    from app.orchestration.contracts import ResearchContract, Requirement

    async def model(messages, info):
        tool = info.output_tools[0]
        if "judgments" in tool.parameters_json_schema["properties"]:
            claims = json.loads(messages[0].parts[-1].content)
            output = {"judgments": [{"claim_id": claim["claim_id"], "supported": False,
                                     "reason": "Review page cannot prove an offer."} for claim in claims]}
        else:
            answer = candidate()
            answer.findings[0].source_kind = "product"
            output = answer.model_dump()
        return ModelResponse(parts=[ToolCallPart(tool.name, output)])
    monkeypatch.setattr(chat, "api_key", lambda: "fixture-key")
    monkeypatch.setattr(chat, "GoogleModel", lambda *args, **kwargs: FunctionModel(model))
    contract = ResearchContract(shopping=True, destination="Finland", requested_count=10,
                                requirements=[Requirement(id="linux", description="Linux support"), Requirement(id="ram", description="16 GB RAM")])
    reply, trace = chat.generate_reply("Find ten laptops in Finland", [], SOURCES, contract=contract)
    assert trace["usage"]["requests"] == 2
    assert trace["usage"]["input_tokens"] == trace["generation_usage"]["input_tokens"] + trace["verification"]["usage"]["input_tokens"]
    assert trace["coverage"]["verified"] == 0
    assert trace["has_gaps"]
    assert "these are leads" in reply
    assert "S1" not in reply
