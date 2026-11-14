import json
from datetime import UTC, datetime

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.orchestration.contracts import ResearchContract
from app.orchestration.grounding import (
    Evidence, GroundedAnswer, KnowledgeAnswer, KnowledgePoint, LLMAnswer,
    expand_llm_answer, render_answer,
)
from app.orchestration.passages import citation_passages
from app.orchestration.verification import resolve_source_ids, verify_claims


SOURCE = {
    'url': 'https://finlex.fi/en/legislation/1995/481', 'title': 'Residential lease law',
    'text': '# Residential lease law\n\nRent is determined by agreement.\n\nThe tenant can request a review of unreasonable rent.',
    'retrieval': 'live', 'retrieved_at': datetime.now(UTC).isoformat(),
}
CONTRACT = ResearchContract(original_request='Can my landlord increase my rent? Draft a question asking for the calculation.')


def point(statement, quote='Rent is determined by agreement.'):
    return KnowledgePoint(statement=statement, evidence=[Evidence(source_url=SOURCE['url'], quote=quote)])


def reviewer(rejected=(), observed=None):
    async def respond(messages, info):
        claims = json.loads(messages[0].parts[-1].content)
        if observed is not None:
            observed.extend(claims)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'judgments': [
            {'claim_id': claim['claim_id'], 'supported': claim['field'] not in rejected,
             'reason': 'Supported.' if claim['field'] not in rejected else 'Unsupported statement.'}
            for claim in claims]})])
    return FunctionModel(respond)


def test_one_unsupported_knowledge_point_does_not_discard_other_points():
    answer = GroundedAnswer(knowledge=KnowledgeAnswer(points=[
        point('Rent is determined by agreement.'), point('Any increase is always lawful.'),
    ]))
    result, trace = verify_claims(answer, [SOURCE], reviewer({'point.1'}), contract=CONTRACT)
    assert [p.statement for p in result.knowledge.points] == ['Rent is determined by agreement.']
    assert trace['knowledge_points'] == {'proposed': 2, 'retained': 1}
    assert not result.knowledge.limited_evidence
    text, _ = render_answer(result, [SOURCE], CONTRACT)
    assert 'agreement' in text and 'always lawful' not in text
    assert 'shortlist' not in text.casefold()


def test_majority_removed_adds_one_limited_evidence_sentence_and_keeps_remainder():
    answer = GroundedAnswer(knowledge=KnowledgeAnswer(points=[point('Rent is determined by agreement.'),
        point('An increase is always lawful.'), point('Every landlord may ignore the lease.')]))
    result, _ = verify_claims(answer, [SOURCE], reviewer({'point.1', 'point.2'}), contract=CONTRACT)
    assert len(result.knowledge.points) == 1
    assert result.knowledge.limited_evidence
    text, _ = render_answer(result, [SOURCE], CONTRACT)
    assert text.count('The available evidence supports only part of the answer.') == 1
    assert 'agreement' in text


def test_knowledge_point_receives_only_its_own_local_cited_passage():
    source = {**SOURCE, 'text': SOURCE['text'] + '\n\n## Other matters\n\n' + 'Unrelated information. ' * 500}
    observed = []
    answer = GroundedAnswer(knowledge=KnowledgeAnswer(points=[point('Rent is determined by agreement.')]))
    verify_claims(answer, [source], reviewer(observed=observed), contract=CONTRACT)
    assert len(observed) == 1
    assert observed[0]['evidence'][0]['quote'] == 'Rent is determined by agreement.'
    assert 'Unrelated information' not in observed[0]['evidence'][0]['context']
    assert 'source_context' not in observed[0]['evidence'][0]


def test_legacy_prose_is_verified_per_sentence_without_removing_good_sentence():
    answer = GroundedAnswer(response=(
        f'Rent is determined by agreement. [Law]({SOURCE["url"]}) '
        f'Any increase is lawful. [Law]({SOURCE["url"]})'))
    observed = []
    result, _ = verify_claims(answer, [SOURCE], reviewer({'response.1'}, observed), contract=CONTRACT)
    assert len(observed) == 2
    assert 'agreement' in result.response
    assert 'Any increase' not in result.response


def test_missing_numeric_evidence_is_rejected_locally_even_with_permissive_verifier():
    answer = GroundedAnswer(knowledge=KnowledgeAnswer(points=[point('Rent increases are capped at 9%.')]))
    result, trace = verify_claims(answer, [SOURCE], reviewer(), contract=CONTRACT)
    assert result.knowledge.points == []
    assert trace['cached'] == 1
    assert result.alignment_rejections


def test_a_citation_with_a_supplied_url_prefix_cannot_support_a_different_url():
    answer = GroundedAnswer(response=f'Rent is determined by agreement. [Law]({SOURCE["url"]}-invented)')
    result, trace = verify_claims(answer, [SOURCE], reviewer(), contract=CONTRACT)
    assert not result.response
    assert trace['cached'] == 1


def test_verdict_cannot_add_a_number_absent_from_points_or_user_request():
    answer = GroundedAnswer(knowledge=KnowledgeAnswer(verdict='The maximum increase is 9%.',
        points=[point('Rent is determined by agreement.')]))
    result, trace = verify_claims(answer, [SOURCE], reviewer(), contract=CONTRACT)
    assert not result.knowledge.verdict
    assert len(result.knowledge.points) == 1
    assert any(item['field'] == 'knowledge.verdict' for item in trace['rejected'])


def test_cached_authority_point_is_reported_with_original_observation():
    source = {**SOURCE, 'retrieval': 'cache', 'cache_hit': True}
    answer = GroundedAnswer(knowledge=KnowledgeAnswer(points=[point('Rent is determined by agreement.')]))
    result, _ = verify_claims(answer, [source], reviewer(), contract=CONTRACT)
    actual = result.knowledge.points[0]
    assert actual.basis == 'reported'
    assert actual.observed == SOURCE['retrieved_at']
    text, _ = render_answer(result, [source], CONTRACT)
    assert 'Reported' in text and 'as of' in text


def test_auxiliary_verdict_and_draft_are_audited_and_unsupported_text_removed():
    answer = GroundedAnswer(knowledge=KnowledgeAnswer(verdict='The increase is definitely lawful.',
        points=[point('Rent is determined by agreement.')], steps=['Ask the landlord for the calculation.'],
        draft='Could you explain how you calculated the proposed increase?',
        caveats=['Every landlord can always increase rent.']))
    observed = []
    result, _ = verify_claims(answer, [SOURCE], reviewer({'knowledge.verdict', 'knowledge.caveats.0'}, observed), contract=CONTRACT)
    assert not result.knowledge.verdict
    assert not result.knowledge.caveats
    assert result.knowledge.steps and result.knowledge.draft
    assert any(c['field'] == 'knowledge.draft' and c['task_context'] == CONTRACT.original_request for c in observed)
    text, _ = render_answer(result, [SOURCE], CONTRACT)
    assert 'Draft (not sent)' in text
    assert 'definitely lawful' not in text


def test_compact_knowledge_references_resolve_and_schema_omits_app_owned_fields():
    reference = next(identifier for identifier, (quote, _) in citation_passages(SOURCE).items() if 'agreement' in quote)
    output = LLMAnswer.model_validate({'knowledge': {'points': [
        {'statement': 'Rent is determined by agreement.', 'evidence': [reference]}]}})
    answer = resolve_source_ids(expand_llm_answer(output, [SOURCE], CONTRACT), [SOURCE])
    assert answer.knowledge.points[0].evidence[0].source_url == SOURCE['url']
    assert answer.knowledge.points[0].evidence[0].passage_id == reference
    check_schema = LLMAnswer.model_json_schema()['$defs']['LLMCheck']['properties']
    assert set(check_schema) == {'requirement_id', 'status', 'value', 'evidence', 'reason'}
    point_schema = LLMAnswer.model_json_schema()['$defs']['LLMKnowledgePoint']['properties']
    assert set(point_schema) == {'statement', 'evidence'}


def test_chat_generates_compact_knowledge_and_verifies_each_point(monkeypatch):
    from app import chat
    reference = next(identifier for identifier, (quote, _) in citation_passages(SOURCE).items() if 'agreement' in quote)
    observed = []

    async def model(messages, info):
        tool = info.output_tools[0]
        if 'judgments' in tool.parameters_json_schema['properties']:
            claims = json.loads(messages[0].parts[-1].content)
            observed.extend(claims)
            output = {'judgments': [{'claim_id': c['claim_id'], 'supported': True, 'reason': 'Exact rule.'} for c in claims]}
        else:
            assert 'response' not in tool.parameters_json_schema['properties']
            output = {'knowledge': {'points': [{'statement': 'Rent is determined by agreement.', 'evidence': [reference]}]}}
        return ModelResponse(parts=[ToolCallPart(tool.name, output)])

    monkeypatch.setattr(chat, 'api_key', lambda: 'fixture-key')
    monkeypatch.setattr(chat, 'GoogleModel', lambda *args, **kwargs: FunctionModel(model))
    reply, trace = chat.generate_reply(CONTRACT.original_request, [], [SOURCE], contract=CONTRACT)
    assert len(observed) == 1 and observed[0]['field'] == 'point.0'
    assert 'agreement' in reply
    assert 'shortlist' not in reply.casefold()
    assert trace['usage']['requests'] == 2
    assert not trace['has_gaps']
