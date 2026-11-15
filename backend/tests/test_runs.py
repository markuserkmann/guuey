import pytest
import time

from app.runs import RunStore
from app.store import ChatStore


@pytest.fixture
def stores(tmp_path):
    path = str(tmp_path / 'chats.sqlite3')
    chats = ChatStore(path)
    runs = RunStore(path)
    chat = chats.create('owner')
    return chats, runs, chat['id']


def test_creation_is_idempotent_and_owner_scoped(stores):
    chats, runs, chat = stores
    run = runs.create('owner', chat, 'request', 'hello', 'auto')
    assert runs.create('owner', chat, 'request', 'hello', 'auto')['id'] == run['id']
    assert runs.get('other', run['id']) is None
    with pytest.raises(LookupError):
        runs.create('other', chat, 'second', 'hello', 'auto')
    with pytest.raises(ValueError):
        runs.create('owner', chat, 'request', 'different', 'auto')
    with pytest.raises(ValueError):
        runs.create('owner', chat, 'new', 'hello', 'auto')


def test_warm_reservation_routes_only_its_chat_and_expires(stores):
    chats, runs, chat = stores
    other = chats.create('owner')['id']
    assert runs.reserve_warm_browser('owner', chat, 'token', time.time() + 60)
    assert not runs.reserve_warm_browser('owner', chat, 'other', time.time() + 60)
    first = runs.create('owner', chat, 'warm', 'follow up', 'auto')
    second = runs.create('owner', other, 'other', 'hello', 'auto')
    assert runs.claim()['id'] == second['id']
    assert runs.claim_warm_browser('owner', chat, 'wrong') is None
    assert runs.claim_warm_browser('owner', other, 'token') is None
    assert runs.claim_warm_browser('owner', chat, 'token')['id'] == first['id']

    runs.finish(first['id'], 'cancelled', 'cancelled')
    assert runs.reserve_warm_browser('owner', chat, 'expired', time.time() - 1)
    third = runs.create('owner', chat, 'after', 'hello again', 'auto')
    assert runs.claim_warm_browser('owner', chat, 'expired') is None
    assert runs.claim()['id'] == third['id']


def test_browser_finish_reserves_followup_in_same_transaction(stores):
    _, runs, chat = stores
    first = runs.create('owner', chat, 'first', 'open page', 'auto')
    runs.claim()
    runs.checkpoint(first['id'], 'browser_done', {}, {'reply': 'Done', 'sources': []})
    runs.finish(first['id'], 'completed', 'browser_done', warm_token='worker')
    followup = runs.create('owner', chat, 'followup', 'continue', 'auto')
    assert runs.claim() is None
    assert runs.claim_warm_browser('owner', chat, 'worker')['id'] == followup['id']


def test_failed_browser_does_not_reserve_followup(stores):
    _, runs, chat = stores
    first = runs.create('owner', chat, 'first', 'open page', 'auto')
    runs.claim()
    runs.finish(first['id'], 'failed', 'browser_failed', warm_token='worker')
    followup = runs.create('owner', chat, 'followup', 'continue', 'auto')
    assert runs.claim()['id'] == followup['id']


def test_finish_atomically_saves_exchange_once(stores):
    chats, runs, chat = stores
    run = runs.create('owner', chat, 'request', 'hello', 'auto')
    assert runs.claim()['id'] == run['id']
    runs.checkpoint(run['id'], 'answer_ready', {'stages': []}, {'reply': 'answer', 'sources': []})
    runs.finish(run['id'], 'partial', 'finished')
    runs.finish(run['id'], 'completed', 'finished')
    assert len(chats.detail('owner', chat)['messages']) == 2
    child = runs.create('owner', chat, 'continue:'+run['id'], 'hello', 'deep', run['id'])
    assert child['parent_id'] == run['id']
    assert runs.create('owner', chat, 'continue:'+run['id'], 'hello', 'deep', run['id'])['id'] == child['id']


def test_cancel_prevents_later_worker_writes(stores):
    chats, runs, chat = stores
    run = runs.create('owner', chat, 'request', 'hello', 'auto')
    runs.claim()
    runs.finish(run['id'], 'cancelled', 'cancelled')
    runs.checkpoint(run['id'], 'answer_ready', {}, {'reply': 'late answer', 'sources': []})
    runs.finish(run['id'], 'completed', 'finished')
    assert runs.get('owner', run['id'])['status'] == 'cancelled'
    assert chats.detail('owner', chat)['messages'] == []


def test_worker_executes_mock_chat_and_persists_result(tmp_path, monkeypatch):
    from app.worker import execute

    path = str(tmp_path / 'worker.sqlite3')
    monkeypatch.setenv('CHAT_DATABASE_PATH', path)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    chats = ChatStore(path)
    runs = RunStore(path)
    chat = chats.create('owner')
    run = runs.create('owner', chat['id'], 'request', 'hello', 'auto')
    runs.claim()
    execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert saved['status'] == 'completed'
    assert saved['debug']['decision']['workflow'] == 'chat'
    assert len(saved['debug']['stages']) == 1
    assert len(chats.detail('owner', chat['id'])['messages']) == 2


def test_checkpoint_preserves_decimal_usage(stores):
    from decimal import Decimal

    _, runs, chat = stores
    run = runs.create('owner', chat, 'decimal', 'hello', 'auto')
    runs.claim()
    debug = {'routing_usage': {'cost': Decimal('0.00000123456789'), 'tokens': 12},
             'stages': [{'usage': {'cost': Decimal('0.005')}}]}
    runs.checkpoint(run['id'], 'planning', debug)
    saved = runs.get('owner', run['id'])['debug']
    assert saved['routing_usage']['cost'] == '0.00000123456789'
    assert saved['routing_usage']['tokens'] == 12
    assert saved['stages'][0]['usage']['cost'] == '0.005'


def test_worker_fallback_with_decimal_usage_completes(tmp_path, monkeypatch):
    from dataclasses import dataclass
    from decimal import Decimal
    from types import SimpleNamespace
    from app import worker
    from app.orchestration.decisions import DecisionUnavailable, RouteDecision

    @dataclass
    class Usage:
        input_tokens: int = 42
        cost: Decimal = Decimal('0.000123')

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run_sync(self, *args, **kwargs):
            return SimpleNamespace(output=RouteDecision(workflow='chat', confidence=0.9), usage=Usage())

    def unavailable(*args):
        raise DecisionUnavailable('fixture')

    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'fallback.sqlite3'))
    monkeypatch.setattr(worker, 'api_key', lambda: 'fixture-key')
    monkeypatch.setattr(worker, 'decide', unavailable)
    monkeypatch.setattr(worker, 'Agent', FakeAgent)
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: SimpleNamespace(reply='answer', sources=[], debug={'usage': {'cost': Decimal('0.002')}}))
    chats, runs = ChatStore(), RunStore()
    chat = chats.create('owner')
    run = runs.create('owner', chat['id'], 'request', 'hello', 'auto')
    runs.claim()
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert saved['status'] == 'completed'
    assert saved['debug']['routing_usage']['cost'] == '0.000123'
    assert saved['debug']['stages'][0]['usage']['cost'] == '0.002'


def test_auto_research_retries_evidence_gaps(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import worker
    from app.orchestration.decisions import RouteDecision

    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'retry.sqlite3'))
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='research', confidence=1))
    calls = []
    def research(message, history, **kwargs):
        calls.append(list(history))
        return SimpleNamespace(reply='Missing Linux evidence' if len(calls) == 1 else 'Verified findings',
            sources=[{'url': f'https://example.com/{len(calls)}', 'title': 'Product', 'text': 'Evidence'}],
            debug={'has_gaps': len(calls) == 1, 'validation_issues': []})
    monkeypatch.setattr(worker, 'run_turn', research)
    chats, runs = ChatStore(), RunStore()
    chat = chats.create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Find ten laptops', 'auto')
    runs.claim()
    worker.execute(run['id'])
    assert len(calls) == 2
    assert any('Missing Linux evidence' in turn.content for turn in calls[1])
    assert runs.get('owner', run['id'])['status'] == 'completed'


def test_research_keeps_useful_first_round_when_followup_degrades(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import worker
    from app.orchestration.decisions import RouteDecision

    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'best.sqlite3'))
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='research', confidence=1))
    calls = []
    def research(*args, **kwargs):
        calls.append(1)
        first = len(calls) == 1
        return SimpleNamespace(reply='Useful shortlist' if first else 'No verified findings',
            sources=[{'url': f'https://example.com/{len(calls)}', 'title': 'Product', 'text': 'Evidence'}],
            debug={'has_gaps': True, 'coverage': {'retained': 5 if first else 0, 'verified': 1 if first else 0}})
    monkeypatch.setattr(worker, 'run_turn', research)
    chats, runs = ChatStore(), RunStore()
    chat = chats.create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Find five computers', 'auto')
    runs.claim()
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert len(calls) == worker.RESEARCH_LIMITS['auto'][0]
    assert saved['result']['reply'] == 'Useful shortlist'
    assert saved['result']['sources'][0]['url'] == 'https://example.com/1'
    assert saved['status'] == 'partial'
