from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app import chat
from app.model_calls import model_output_events
from app.orchestration.contracts import ResearchContract, Requirement


def test_research_records_missing_checks_without_resending_prompt(monkeypatch):
    calls = []

    async def model(messages, info):
        calls.append(messages)
        checks = [{
            'requirement_id': 'summary' if len(calls) == 1 else 'price', 'requirement': 'Published price',
            'status': 'unknown', 'statement': '', 'reason': 'No published price', 'evidence': [],
        }]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'response': 'Venue A price is unknown.' if len(calls) == 1 else '',
            'findings': [{'name': 'Venue A', 'source_url': 'https://example.com', 'checks': checks}]
        })])

    monkeypatch.setenv('GEMINI_API_KEY', 'test-only')
    monkeypatch.setattr(chat, 'google_provider', lambda key: None)
    monkeypatch.setattr(chat, 'GoogleModel', lambda *args, **kwargs: FunctionModel(model))
    monkeypatch.setattr(chat, 'verify_claims', lambda output, *args: (output, {}))
    token = model_output_events.set(lambda event: None)
    try:
        _, debug = chat.generate_reply('Find the price', [],
            [{'url': 'https://example.com', 'title': 'Venue A', 'text': 'Venue A'}],
            contract=ResearchContract(activity='service', requirements=[Requirement(id='price', description='Published price')]))
    finally:
        model_output_events.reset(token)
    assert len(calls) == 1
    assert len(debug['raw_evidence_attempts']) == 1


def test_nonshopping_downgrades_invented_quotes_without_retry(monkeypatch):
    calls = []
    async def model(messages, info):
        calls.append(messages)
        quote = 'Venue A ... wheelchair accessible' if len(calls) == 1 else 'Venue A is wheelchair accessible.'
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'findings': [{
            'name': 'Venue A', 'source_url': 'https://example.com', 'checks': [{
                'requirement_id': 'location', 'requirement': 'Wheelchair accessible', 'status': 'met',
                'statement': 'Venue A is wheelchair accessible.', 'evidence': [{'source_url': 'https://example.com', 'quote': quote}]
            }]}]})])
    monkeypatch.setenv('GEMINI_API_KEY', 'test-only')
    monkeypatch.setattr(chat, 'google_provider', lambda key: None)
    monkeypatch.setattr(chat, 'GoogleModel', lambda *args, **kwargs: FunctionModel(model))
    monkeypatch.setattr(chat, 'verify_claims', lambda output, *args: (output, {}))
    token = model_output_events.set(lambda event: None)
    try:
        reply, debug = chat.generate_reply('Find a wheelchair accessible venue', [],
            [{'url': 'https://example.com', 'title': 'Venue A', 'text': 'Venue A is wheelchair accessible.'}],
            contract=ResearchContract(requirements=[Requirement(id='location', description='Wheelchair accessible')]))
    finally:
        model_output_events.reset(token)
    assert len(calls) == 1
    assert debug['coverage']['verified'] == 0
    assert debug['unsupported_checks']


def test_shopping_adds_unknown_omitted_checks_without_retry(monkeypatch):
    calls = []

    async def model(messages, info):
        calls.append(messages)
        ids = ['summary'] if len(calls) == 1 else ['memory', 'stock']
        checks = [{'requirement_id': identifier, 'requirement': identifier, 'status': 'unknown',
                   'statement': '', 'reason': 'Not published', 'evidence': []} for identifier in ids]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            'findings': [{'name': 'Laptop A', 'source_url': 'https://shop.example/product/a',
                          'source_kind': 'product', 'checks': checks}]})])

    monkeypatch.setenv('GEMINI_API_KEY', 'test-only')
    monkeypatch.setattr(chat, 'google_provider', lambda key: None)
    monkeypatch.setattr(chat, 'GoogleModel', lambda *args, **kwargs: FunctionModel(model))
    monkeypatch.setattr(chat, 'verify_claims', lambda output, *args: (output, {}))
    token = model_output_events.set(lambda event: None)
    try:
        _, debug = chat.generate_reply('Find laptops with 16 GB RAM', [],
            [{'url': 'https://shop.example/product/a', 'title': 'Laptop A', 'text': 'Laptop A'}],
            contract=ResearchContract(activity='product', requirements=[Requirement(id='memory', description='16 GB RAM')]))
    finally:
        model_output_events.reset(token)
    assert len(calls) == 1
    assert len(debug['raw_evidence_attempts']) == 1
    assert debug['missing_checks'] == {'Laptop A': ['memory', 'stock']}
    assert debug['coverage']['supported_checks'] == 0
