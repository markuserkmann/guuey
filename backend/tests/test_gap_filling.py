import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.orchestration.contracts import Requirement, ResearchContract
from app.orchestration.gap_filling import fill_candidate_gaps
from app.orchestration.grounding import GroundedAnswer
from app.orchestration.passages import citation_passages
from app.research import stable_source_id


def test_gap_call_only_fills_missing_ids_and_preserves_known_facts():
    source = {'url': 'https://venue.example/a', 'title': 'Venue A', 'text': '# Venue A\n\nVenue A has a pool.\n\nVenue A has a restaurant.'}
    task = ResearchContract(requested_count=1, requirements=[Requirement(id='pool', description='Pool'), Requirement(id='food', description='Restaurant')])
    prior = {'findings': [{'name': 'Venue A', 'source_url': source['url'], 'checks': [
        {'requirement_id': 'pool', 'requirement': 'Pool', 'status': 'met', 'statement': 'Has a pool',
         'evidence': [{'source_url': source['url'], 'quote': 'Venue A has a pool.'}]},
        {'requirement_id': 'food', 'requirement': 'Restaurant', 'status': 'unknown', 'statement': '', 'evidence': []}]}]}
    passage_id = next(i for i, (q, _) in citation_passages(source).items() if 'restaurant' in q)
    prompts = []
    async def model(messages, info):
        prompt = messages[0].parts[-1].content
        if 'judgments' in info.output_tools[0].parameters_json_schema['properties']:
            claims = json.loads(prompt)
            output = {'judgments': [{'claim_id': c['claim_id'], 'supported': True, 'reason': 'Supported'} for c in claims]}
        else:
            prompts.append(prompt)
            context = json.loads(prompt.split('\nSupplied source evidence')[0])
            assert [c['requirement_id'] for c in context['requirements_to_fill']] == ['food']
            output = {'response': [
                {'requirement_id': 'food', 'requirement': 'Restaurant', 'status': 'met', 'statement': 'Has a restaurant',
                 'evidence': [{'source_url': stable_source_id(source['url']), 'quote': passage_id}]},
                {'requirement_id': 'invented', 'requirement': 'Invented', 'status': 'met', 'statement': 'Ignore me', 'evidence': []}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])
    reply, debug = fill_candidate_gaps('Find venues', [source], task, prior, FunctionModel(model), FunctionModel(model), {},
                                     lambda *args, **kwargs: pytest.fail('No discovery call needed'))
    final = GroundedAnswer.model_validate_json(debug['output'])
    checks = {c.requirement_id: c for c in final.findings[0].checks}
    assert checks['pool'].statement == 'Has a pool'
    assert checks['food'].status == 'met' and 'invented' not in checks
    assert debug['coverage']['verified'] == 1
    assert debug['generation_usage']['requests'] == 1
    assert len(prompts) == 1 and len(prompts[0]) < 10000


def test_direct_delivery_gap_skips_planning_and_search(monkeypatch):
    from app.orchestration import orchestrator
    task = ResearchContract(activity='product', destination='Finland', requested_count=1)
    source = {'url': 'https://shop.fi/product/a', 'title': 'Laptop A', 'text': 'Laptop A'}
    prior = {'findings': [{'name': 'Laptop A', 'source_url': source['url'], 'checks': [
        {'requirement_id': 'stock', 'status': 'met'}, {'requirement_id': 'delivery', 'status': 'unknown'}]}]}
    policy = {'url': 'https://shop.fi/shipping', 'title': 'Delivery', 'text': 'Shipping to Finland.'}
    monkeypatch.setattr(orchestrator, 'plan_searches', lambda *args: pytest.fail('Direct action needs no planner'))
    monkeypatch.setattr(orchestrator, 'search', lambda *args, **kwargs: pytest.fail('Direct action needs no search'))
    monkeypatch.setattr(orchestrator, 'extract_pages', lambda *args, **kwargs: [policy])
    monkeypatch.setattr(orchestrator, 'fill_gaps', lambda *args, **kwargs: ('Updated answer', {'output': '{}'}))
    result = orchestrator.run_turn('Find laptops', [], search_requested=True, contract=task, seed_sources=[source], prior_answer=prior)
    assert result.reply == 'Updated answer'
    assert result.debug['executed_queries'] == []
    assert result.debug['retrieval']['contents_calls'] == 1


def test_gap_failure_falls_back_to_full_generation(monkeypatch):
    from app.orchestration import orchestrator
    from app.orchestration.contracts import SearchPlan, SearchQuery
    task = ResearchContract()
    source = {'url': 'https://venue.example/a', 'title': 'Venue A', 'text': 'Venue A'}
    monkeypatch.setattr(orchestrator, 'plan_searches', lambda *args: (SearchPlan(contract=task, queries=[SearchQuery(query='venue', objective='facts')]), None))
    monkeypatch.setattr(orchestrator, 'search', lambda *args, **kwargs: [source])
    monkeypatch.setattr(orchestrator, 'fill_gaps', lambda *args: (_ for _ in ()).throw(ValueError('No useful checks')))
    monkeypatch.setattr(orchestrator, 'generate_reply', lambda *args, **kwargs: ('Full answer', {}))
    result = orchestrator.run_turn('Find venues', [], search_requested=True, contract=task, prior_answer={'findings': []})
    assert result.reply == 'Full answer'
    assert result.debug['gap_filling_fallback'] == 'ValueError'


def test_primary_policy_page_is_preferred_over_long_third_party_guides():
    source = {'url': 'https://shop.fi/product/a', 'title': 'Laptop A', 'text': '# Laptop A'}
    policy = {'url': 'https://shop.fi/delivery', 'title': 'Shipping terms', 'text': 'We ship to Finland.'}
    guides = [{'url': f'https://guide.example/{i}', 'title': 'Laptop A delivery Finland shopping guide',
               'text': 'Laptop A delivery Finland shopping guide. ' * 100} for i in range(5)]
    task = ResearchContract(activity='product', destination='Finland', requested_count=1)
    prior = {'findings': [{'name': 'Laptop A', 'source_kind': 'product', 'source_url': source['url'], 'checks': [
        {'requirement_id': 'stock', 'requirement': 'Stock', 'status': 'unknown', 'statement': '', 'evidence': []},
        {'requirement_id': 'delivery', 'requirement': 'Delivery', 'status': 'unknown', 'statement': '', 'evidence': []}]}]}
    async def model(messages, info):
        prompt = messages[0].parts[-1].content
        if 'judgments' in info.output_tools[0].parameters_json_schema['properties']:
            claims = json.loads(prompt)
            output = {'judgments': [{'claim_id': c['claim_id'], 'supported': True, 'reason': 'Direct offer'} for c in claims]}
        else:
            assert policy['url'] in prompt
            output = {'response': [{'requirement_id': 'delivery', 'value': '', 'status': 'unknown', 'evidence': []}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])
    _, debug = fill_candidate_gaps('Find laptops', [source, *guides, policy], task, prior, FunctionModel(model), FunctionModel(model), {},
                                  lambda *args, **kwargs: pytest.fail('No discovery needed'))
    assert not debug['gap_filling']['failures']
