import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.browser.config import BrowserSettings, BrowserUnavailable
from app.browser.store import BrowserStore, decrypt, encrypt
from app.runs import RunStore


@pytest.fixture
def browser(tmp_path):
    runs = RunStore(str(tmp_path / 'browser.sqlite3'))
    from app.store import ChatStore
    chat = ChatStore(str(runs.path)).create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Sign in', 'auto')
    runs.claim()
    return BrowserStore(runs, BrowserSettings(session_key='ab' * 32)), run['id']


def input_request(store, run_id):
    return store.create_request('owner', run_id, 'input', 'example.com', {
        'reason': 'Sign in', 'fields': [
            {'id': 'email', 'label': 'Email', 'kind': 'email'},
            {'id': 'password', 'label': 'Password', 'kind': 'password'},
        ],
    })


def test_encryption_authenticates_owner_site_and_id():
    key = bytes.fromhex('ab' * 32)
    encrypted = encrypt({'cookie': 'private'}, key, 'owner|example.com|id')
    assert b'private' not in encrypted
    assert decrypt(encrypted, key, 'owner|example.com|id') == {'cookie': 'private'}
    assert encrypted != encrypt({'cookie': 'private'}, key, 'owner|example.com|id')
    for aad in ('other|example.com|id', 'owner|other.com|id', 'owner|example.com|other'):
        with pytest.raises(BrowserUnavailable):
            decrypt(encrypted, key, aad)
    with pytest.raises(BrowserUnavailable):
        decrypt(encrypted, bytes.fromhex('cd' * 32), 'owner|example.com|id')


def test_secret_wiped_after_atomic_take(browser):
    store, run_id = browser
    request = input_request(store, run_id)
    result = store.answer_request('owner', run_id, request['id'], {'values': {'email': 'me@example.com', 'password': 'private-password'}})
    assert 'private-password' not in json.dumps(result)
    with store.runs.connect() as db:
        assert db.execute('PRAGMA secure_delete').fetchone()[0] == 1
        row = db.execute('SELECT * FROM browser_requests').fetchone()
        assert 'private-password' not in row['response']
        assert b'private-password' not in row['secret_values']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: store.take_response('owner', run_id, request['id']), range(2)))
    assert responses.count(None) == 1
    taken = next(response for response in responses if response)
    assert taken == {'response': {'values': {'email': 'me@example.com'}}, 'secrets': {'password': 'private-password'}}
    with store.runs.connect() as db:
        row = db.execute('SELECT * FROM browser_requests').fetchone()
        assert row['status'] == 'taken'
        assert row['secret_values'] is None


@pytest.mark.parametrize('answered', [False, True])
def test_finish_expires_requests(browser, answered):
    store, run_id = browser
    request = input_request(store, run_id)
    if answered:
        store.answer_request('owner', run_id, request['id'], {'values': {'email': 'me@example.com', 'password': 'private'}})
    store.runs.finish(run_id, 'cancelled', 'cancelled')
    assert store.get_request('owner', run_id, request['id'])['status'] == 'expired'
    with store.runs.connect() as db:
        assert db.execute('SELECT secret_values FROM browser_requests').fetchone()[0] is None
    with pytest.raises(ValueError):
        store.take_response('owner', run_id, request['id'])
    with pytest.raises(ValueError):
        input_request(store, run_id)


def test_expired_answer_is_wiped_and_cannot_be_consumed(browser):
    store, run_id = browser
    request = input_request(store, run_id)
    store.answer_request('owner', run_id, request['id'], {'values': {'email': 'me', 'password': 'private'}})
    with store.runs.connect() as db:
        db.execute("UPDATE browser_requests SET expires_at='2000-01-01T00:00:00+00:00'")
    assert store.take_response('owner', run_id, request['id']) is None
    with store.runs.connect() as db:
        row = db.execute('SELECT status,secret_values FROM browser_requests').fetchone()
        assert tuple(row) == ('expired', None)


def test_requests_owner_scoped_and_cannot_be_answered_twice(browser):
    store, run_id = browser
    request = store.create_request('owner', run_id, 'takeover', 'example.com', {'reason': 'Finish sign in'})
    for operation in (
        lambda: store.answer_request('other', run_id, request['id'], {'done': True}),
        lambda: store.take_response('other', run_id, request['id']),
        lambda: store.get_request('other', run_id, request['id']),
        lambda: store.state('other', run_id),
    ):
        with pytest.raises(LookupError):
            operation()
    with pytest.raises(ValueError):
        store.answer_request('owner', run_id, request['id'], {'decision': 'approve'})
    store.answer_request('owner', run_id, request['id'], {'done': True})
    with pytest.raises(ValueError):
        store.answer_request('owner', run_id, request['id'], {'done': True})


def test_sessions_are_encrypted_upserted_and_owner_scoped(browser):
    store, _ = browser
    state = {'cookies': [{'name': 'session', 'value': 'private', 'domain': '.example.com'}],
             'origins': [{'origin': 'https://example.com', 'localStorage': [{'name': 'token', 'value': 'private-token'}]},
                         {'origin': 'https://other.com', 'localStorage': [{'name': 'token', 'value': 'foreign'}]}]}
    session = store.save_session('owner', 'example.com', state, 'unverified')
    assert 'private' not in json.dumps(store.list_sessions('owner'))
    assert store.list_sessions('other') == []
    assert store.load_session('other', 'example.com') is None
    restored = store.load_session('owner', 'example.com')
    assert restored['cookies'][0]['value'] == 'private'
    assert len(restored['origins']) == 1
    assert store.save_session('owner', 'example.com', state)['id'] == session['id']
    with store.runs.connect() as db:
        encrypted = db.execute('SELECT state FROM browser_sessions').fetchone()[0]
        assert b'private' not in encrypted
    with pytest.raises(LookupError):
        store.delete_session('other', session['id'])
    store.expire_session('owner', session['id'])
    assert store.load_session('owner', 'example.com') is None
    store.delete_session('owner', session['id'])
    assert store.list_sessions('owner') == []


def test_private_inputs_and_sessions_require_key(browser):
    configured, run_id = browser
    store = BrowserStore(configured.runs, BrowserSettings())
    with pytest.raises(BrowserUnavailable):
        input_request(store, run_id)
    with pytest.raises(BrowserUnavailable):
        store.save_session('owner', 'example.com', {'cookies': [], 'origins': []})
    assert store.create_request('owner', run_id, 'takeover', 'example.com', {'reason': 'Finish'})


def test_action_ledger_transitions_and_cancel(browser):
    store, run_id = browser
    action = store.create_action('owner', run_id, 'final', 'Order', 'Place order', 'https://example.com/checkout', 'hash')
    with pytest.raises(ValueError):
        store.update_action('owner', run_id, action, 'verified', 'Order received')
    store.update_action('owner', run_id, action, 'approved')
    store.update_action('owner', run_id, action, 'submitted')
    with pytest.raises(ValueError):
        store.create_action('owner', run_id, 'final', 'Order', 'Place order', 'https://example.com/checkout', 'hash')
    store.runs.finish(run_id, 'cancelled', 'cancelled')
    assert store.state('owner', run_id)['actions'][0]['status'] == 'unverified'


def test_browser_state_is_shared_by_get_and_stream(browser):
    store, run_id = browser
    assert store.runs.get('owner', run_id)['browser'] is None
    for number in range(35):
        store.add_step('owner', run_id, 'open', f'Opened page {number}')
    request = input_request(store, run_id)
    state = store.runs.get('owner', run_id)['browser']
    assert state == store.runs.stream_state('owner', run_id)['browser']
    assert len(state['steps']) == 30
    assert state['steps'][0]['seq'] == 6
    assert state['request']['id'] == request['id']
    assert not {'secret_values', 'response', 'screenshot'} & state['request'].keys()


def test_continue_rejects_browser_runs(browser):
    store, run_id = browser
    store.runs.finish(run_id, 'partial', 'browser_unavailable')
    run = store.runs.get('owner', run_id)
    with pytest.raises(ValueError, match='Browser runs'):
        store.runs.create('owner', run['chat_id'], 'continue', 'Sign in', 'deep', run_id)
