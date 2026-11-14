from app.orchestration.grounding import GroundedAnswer, render_answer


SOURCES = [{"url": "https://shop.example/product", "text": "Laptop A has 16 GB RAM. Price €900.", "title": "Shop"}]


def answer(quote="Laptop A has 16 GB RAM.", url="https://shop.example/product"):
    return GroundedAnswer.model_validate({"findings": [{"name": "Laptop A", "checks": [
        {"requirement": "16 GB RAM", "status": "met", "statement": "16 GB installed", "evidence": [{"source_url": url, "quote": quote}]},
        {"requirement": "Linux support", "status": "unknown", "statement": "", "evidence": []},
    ]}]})


def test_partial_finding_keeps_supported_requirement_and_link():
    text, issues = render_answer(answer(), SOURCES)
    assert "16 GB installed" in text
    assert "https://shop.example/product" in text
    assert "Unverified" in text
    assert not issues


def test_unknown_url_does_not_support_claim():
    text, issues = render_answer(answer(url="https://invented.example"), SOURCES)
    assert "16 GB installed" not in text
    assert "https://invented.example" not in text
    assert issues


def test_fabricated_quote_does_not_support_claim():
    text, issues = render_answer(answer(quote="Linux certified"), SOURCES)
    assert "16 GB installed" not in text
    assert issues


def test_bad_finding_does_not_discard_good_finding():
    result = answer()
    result.findings.extend(answer(quote="Invented evidence").findings)
    text, issues = render_answer(result, SOURCES)
    assert "16 GB installed" in text
    assert issues


def test_live_pipeline_repairs_missing_evidence_once(monkeypatch):
    from app import chat
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    calls = []

    async def model(messages, info):
        calls.append(messages)
        output = answer().model_dump()
        if len(calls) == 1:
            output['findings'][0]['checks'][0]['evidence'] = []
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])

    monkeypatch.setattr(chat, 'api_key', lambda: 'fixture-key')
    monkeypatch.setattr(chat, 'GoogleModel', lambda *args, **kwargs: FunctionModel(model))
    reply, trace = chat.generate_reply('Find a laptop', [], SOURCES)
    assert len(calls) == 2
    assert trace['evidence_attempts'][0]['validation_issues']
    assert not trace['validation_issues']
    assert 'https://shop.example/product' in reply


def test_sourced_candidate_survives_all_unknown_checks():
    output = answer()
    output.findings[0].source_url = SOURCES[0]['url']
    for check in output.findings[0].checks:
        check.status = 'unknown'
        check.evidence = []
    text, issues = render_answer(output, SOURCES)
    assert '### 1. Laptop A' in text
    assert '[Source]' in text
    assert 'Unverified' in text
    assert not issues


def test_rejected_findings_show_leads_without_stale_verified_count():
    output = answer(quote='Invented quote')
    output.requested_count = 5
    output.limitations = ['Only 1 item has fully verified evidence matching all criteria.']
    text, issues = render_answer(output, SOURCES)
    assert '[Shop]' in text
    assert 'these are leads' in text
    assert 'Only 1 item' not in text
    assert '16 GB installed' not in text
    assert issues


def test_invented_candidate_identity_is_not_displayed():
    output = answer(quote='Invented quote')
    output.findings[0].name = 'Invented laptop'
    output.findings[0].source_url = SOURCES[0]['url']
    text, issues = render_answer(output, SOURCES)
    assert 'Invented laptop' not in text
    assert issues


def test_general_research_can_answer_in_prose():
    output = GroundedAnswer(response='Linux distributions differ in their release schedules.')
    text, issues = render_answer(output, SOURCES)
    assert text == output.response
    assert not issues


def test_quote_matching_tolerates_presentation_changes():
    from app.orchestration.grounding import passage_present
    assert passage_present('Laptop A has 16 GB RAM.', '**Laptop A** has 16 GB RAM.')
    assert passage_present('Laptop A & accessories', 'Laptop A &amp; accessories')
    assert passage_present('Memory. 16 GB', '| Memory | 16 GB |')
    assert not passage_present('Linux supported', 'Linux is not supported')
    assert not passage_present('900', '1900')
    assert not passage_present('900', '900.99')
    assert not passage_present('16 GB', '116 GB')


def test_shortlist_has_sourced_description_and_labeled_assessment():
    output = answer()
    item = output.findings[0]
    item.description = 'A laptop with 16 GB RAM.'
    item.description_evidence = item.checks[0].evidence
    item.assessment = 'Worth considering if Linux compatibility checks out.'
    item.assessment_evidence = item.checks[0].evidence
    text, issues = render_answer(output, SOURCES)
    assert 'A laptop with 16 GB RAM' in text
    assert 'Assessment:' in text
    assert 'option to consider' in text
    assert not issues


def test_unsupported_description_is_removed_without_losing_candidate():
    output = answer()
    output.findings[0].description = 'Invented battery specification'
    text, issues = render_answer(output, SOURCES)
    assert 'Invented battery specification' not in text
    assert 'Laptop A' in text
    assert issues


def test_coverage_uses_validated_checks():
    from app.orchestration.grounding import answer_coverage
    output = answer(quote='Invented quote')
    output.findings[0].source_url = SOURCES[0]['url']
    for check in output.findings[0].checks:
        check.status = 'met'
    coverage = answer_coverage(output, SOURCES)
    assert coverage['retained'] == 1
    assert coverage['verified'] == 0


def test_answer_enforces_local_size_limit():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='at most 30'):
        GroundedAnswer(findings=answer().findings * 31)
