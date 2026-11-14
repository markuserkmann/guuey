import asyncio
import hashlib
import json

from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel

from app.main import app, get_runs, get_store
from app.model_calls import call_model
from app.runs import RunStore
from app.store import ChatStore
from app.streaming import stream_run


def test_real_model_stream_emits_text_before_finishing():
    received = []

    async def tokens(messages, info):
        yield "Hello"
        await asyncio.sleep(0.2)
        assert received == ["Hello"]
        yield " Finland €"

    result = call_model(Agent(FunctionModel(stream_function=tokens)), "hello", stage="answering", on_output=received.append)
    assert received[0] == "Hello"
    assert received[-1] == "Hello Finland €"
    assert result.output == received[-1]
    assert result.usage.requests == 1


def test_structured_research_shows_candidate_progress_before_verification(monkeypatch):
    from datetime import UTC, datetime
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import DeltaToolCall
    from app import chat
    from app.model_calls import model_output_events
    from app.orchestration.contracts import ResearchContract, Requirement

    payload = {"findings": [{"name": "Laptop A", "source_url": "S1", "source_kind": "product", "checks": [
        {"requirement_id": "price", "requirement": "Price under 1000 EUR", "status": "met", "statement": "Costs €900.",
         "evidence": [{"source_url": "S1", "quote": "Laptop A costs €900."}]},
    ]}]}

    async def tokens(messages, info):
        text = json.dumps(payload)
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args=text[:100])}
        await asyncio.sleep(0.2)
        yield {0: DeltaToolCall(json_args=text[100:])}

    async def verify(messages, info):
        if 'findings' in info.output_tools[0].parameters_json_schema['properties']:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])
        assert updates and updates[-1]['kind'] == 'progress'
        claims = json.loads(messages[0].parts[-1].content)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"judgments": [
            {"claim_id": claim['claim_id'], "supported": True, "reason": "Direct evidence."} for claim in claims]})])

    monkeypatch.setattr(chat, 'api_key', lambda: 'fixture-key')
    monkeypatch.setattr(chat, 'GoogleModel', lambda *args, **kwargs: FunctionModel(function=verify, stream_function=tokens))
    updates = []
    observer = model_output_events.set(updates.append)
    try:
        reply, trace = chat.generate_reply('Find a laptop in Finland', [], [{
            'url': 'https://shop.fi/a', 'title': 'Laptop A', 'text': 'Laptop A costs €900.',
            'retrieval': 'live', 'retrieved_at': datetime.now(UTC).isoformat(),
        }], contract=ResearchContract(shopping=True, destination='Finland', requirements=[
            Requirement(id='price', kind='price', description='Price under 1000 EUR')]))
    finally:
        model_output_events.reset(observer)
    assert updates and all(update['kind'] == 'progress' for update in updates)
    assert 'Laptop A' in updates[-1]['text']
    assert '€900' not in updates[-1]['text']
    assert '€900' in reply
    assert 'To confirm for Laptop A' in reply
    assert 'Finland' in reply
    assert 'Not found' not in reply
    assert trace['verification']['claims'] == 2


def test_cancel_preserves_chat_text_and_rejects_late_writes(tmp_path):
    store = RunStore(str(tmp_path / "runs.sqlite3"))
    chat = ChatStore(str(store.path)).create('owner')
    run = store.create('owner', chat['id'], 'request', 'hello', 'auto')
    store.claim()
    store.update_live_output(run['id'], {"kind": "answer", "text": "Partial reply"})
    store.finish(run['id'], 'cancelled', 'cancelled')
    store.update_live_output(run['id'], {"kind": "answer", "text": "Late reply"})
    saved = store.get('owner', run['id'])
    assert saved['result']['reply'] == "Partial reply"
    assert saved['live_output'] is None
    assert len(store.detail('owner', chat['id'])['messages']) == 2


def test_unverified_research_progress_is_not_saved_as_an_answer(tmp_path):
    filename = str(tmp_path / "runs.sqlite3")
    store = RunStore(filename)
    chat = ChatStore(filename).create('owner')
    run = store.create('owner', chat['id'], 'request', 'research', 'auto')
    store.claim()
    store.update_live_output(run['id'], {"kind": "progress", "text": "Checking Laptop A"})
    store.finish(run['id'], 'cancelled', 'cancelled')
    assert store.get('owner', run['id'])['result'] is None
    assert not store.detail('owner', chat['id'])['messages']


def test_event_stream_restores_latest_snapshot_and_ends_on_completion():
    snapshots = [
        {"id": "run", "updated_at": "1", "status": "running", "live_output": {"kind": "answer", "text": "Hello €"}},
        {"id": "run", "updated_at": "2", "status": "completed", "live_output": None},
    ]
    class Store:
        def stream_state(self, owner, run_id):
            return snapshots.pop(0)
    class Connection:
        async def is_disconnected(self):
            return False
    async def consume():
        return [event async for event in stream_run(Store(), 'owner', 'run', Connection())]
    events = asyncio.run(consume())
    assert len(events) == 2
    assert 'Hello €' in events[0]
    assert 'id: 2' in events[1]
    assert 'completed' in events[1]


def test_stream_endpoint_is_authenticated_owner_scoped_and_omits_full_pages(tmp_path, monkeypatch):
    filename = str(tmp_path / 'api.sqlite3')
    monkeypatch.setenv('CHAT_DATABASE_PATH', filename)
    monkeypatch.setenv('AGENT_API_TOKEN', 'a' * 40)
    get_runs.cache_clear()
    get_store.cache_clear()
    owner = hashlib.sha256(('a' * 40).encode()).hexdigest()
    chats, runs = ChatStore(filename), RunStore(filename)
    chat = chats.create(owner)
    run = runs.create(owner, chat['id'], 'request', 'hello', 'auto')
    runs.claim()
    runs.checkpoint(run['id'], 'answer_ready', {}, {'reply': 'Hello', 'sources': [{'url': 'https://example.com', 'title': 'Example', 'text': 'private full page corpus'}]})
    runs.finish(run['id'], 'completed', 'finished')
    try:
        with TestClient(app) as client:
            path = f"/api/runs/{run['id']}/events"
            assert client.get(path).status_code == 401
            headers = {'Authorization': 'Bearer ' + 'a' * 40}
            response = client.get(path, headers=headers)
            assert response.status_code == 200
            assert response.headers['x-accel-buffering'] == 'no'
            payload = json.loads(response.text.split('data: ', 1)[1])
            assert payload['result']['reply'] == 'Hello'
            assert 'private full page corpus' not in response.text
            other = chats.create('different-owner')
            foreign = runs.create('different-owner', other['id'], 'other', 'hello', 'auto')
            assert client.get(f"/api/runs/{foreign['id']}/events", headers=headers).status_code == 404
    finally:
        get_runs.cache_clear()
        get_store.cache_clear()
