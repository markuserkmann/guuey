import json

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.orchestration.contracts import Budget, ResearchContract
from app.orchestration.grounding import GroundedAnswer, answer_coverage, apply_contract, render_answer
from app.orchestration.verification import verify_claims
from app.orchestration.passages import visible


def reported_price():
    url = 'https://seller.fi/product/a'
    sources = [{'url': url, 'title': 'Laptop A', 'text': '# Laptop A\n\nLaptop A costs €649.',
                'retrieval': 'page', 'crawled_at': '2026-11-12T12:00:00+00:00'}]
    task = ResearchContract(activity='product', budget=Budget(amount=700, operator='lt'))
    answer = GroundedAnswer.model_validate({'findings': [{'name': 'Laptop A', 'source_url': url, 'source_kind': 'product',
        'checks': [{'requirement_id': 'price', 'requirement': 'Price', 'status': 'met', 'statement': '€649',
                    'evidence': [{'source_url': url, 'quote': 'Laptop A costs €649.'}]}]}]})
    return apply_contract(answer, sources, task), sources, task


def test_cached_price_is_reported_with_date_but_never_verified():
    answer, sources, task = reported_price()
    check = next(c for c in answer.findings[0].checks if c.requirement_id == 'price')
    assert check.status == 'unknown' and check.basis == 'reported'
    assert check.observed == '2026-11-12T12:00:00+00:00'
    text, _ = render_answer(answer, sources, task)
    assert 'Reported: €649' in text and '2026-11-12' in visible(text)
    assert 'Confirm before buying' in text
    coverage = answer_coverage(answer, sources, task)
    assert coverage['verified'] == 0 and coverage['supported_checks'] == 0
    assert coverage['gaps']
    assert apply_contract(answer, sources, task) == answer


def test_verifier_rejected_reported_statement_is_removed():
    answer, sources, task = reported_price()
    observed = []
    async def model(messages, info):
        claims = json.loads(messages[0].parts[-1].content)
        observed.extend(claims)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'judgments': [
            {'claim_id': c['claim_id'], 'supported': c['field'] == 'offer', 'reason': 'Unsupported statement'} for c in claims]})])
    output, _ = verify_claims(answer, sources, FunctionModel(model))
    price = next(c for c in output.findings[0].checks if c.requirement_id == 'price')
    assert any(c.get('basis') == 'reported' for c in observed)
    assert price.basis == 'none' and not price.evidence and not price.statement
    text, _ = render_answer(output, sources, task)
    assert 'Reported: €649' not in text and 'To confirm' in text


def test_reported_volatile_check_can_be_sufficient_but_reported_price_cannot():
    from app.orchestration.contracts import Requirement
    from app.orchestration.grounding import sufficient_reported_answer
    source = {'url': 'https://venue.example/a', 'title': 'Venue A', 'text': 'Venue A admits adults.\n\nVenue A has availability.'}
    task = ResearchContract(requested_count=1, requirements=[Requirement(id='type', description='Adult admission'),
        Requirement(id='availability', description='Current availability', kind='availability')])
    answer = GroundedAnswer.model_validate({'findings': [{'name': 'Venue A', 'source_url': source['url'], 'checks': [
        {'requirement_id': 'type', 'requirement': 'Adult admission', 'status': 'met', 'statement': 'Admits adults',
         'evidence': [{'source_url': source['url'], 'quote': 'Venue A admits adults.'}]},
        {'requirement_id': 'availability', 'requirement': 'Availability', 'status': 'unknown', 'basis': 'reported',
         'statement': 'Has availability', 'evidence': [{'source_url': source['url'], 'quote': 'Venue A has availability.'}]}]}]})
    assert sufficient_reported_answer(answer, [source], task)
    task.requirements[1].kind = 'price'
    assert not sufficient_reported_answer(answer, [source], task)
