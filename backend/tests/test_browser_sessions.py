import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.browser import actions, run as browser_run, sessions
from app.browser.actions import BrowserDeps, BrowserStop
from app.browser.agent import BrowserReport
from app.browser.config import BrowserSettings, BrowserUnavailable
from app.browser.cookies import filter_storage_state
from app.browser.schemas import InputField
from app.browser.store import BrowserStore, SessionConflict, encrypt
from app.runs import RunStore
from app.store import ChatStore
from browser_fakes import FakePage


STATE = {'cookies': [{'name': 'session', 'value': 'private-session-token', 'domain': '.example.com'}], 'origins': []}


def new_run(store, owner='owner'):
    chat = ChatStore(str(store.runs.path)).create(owner)
    run = store.runs.create(owner, chat['id'], str(uuid4()), 'Sign in', 'auto')
    store.runs.claim()
    store.start(owner, run['id'])
    return run['id']


@pytest.fixture
def store(tmp_path):
    return BrowserStore(RunStore(str(tmp_path / 'sessions.sqlite3')), BrowserSettings(session_key='ab' * 32))


@pytest.fixture
def deps(store):
    value = BrowserDeps(FakePage(), store, 'owner', new_run(store), store.settings)
    value.settle = AsyncMock()
    return value


def test_simultaneous_session_acquisition_is_exclusive(store):
    run_ids = [new_run(store), new_run(store)]
    def acquire(run_id):
        try:
            return store.acquire_session('owner', run_id, 'example.com')[0]
        except SessionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(acquire, run_ids))
    assert sum(lease is not None for lease in leases) == 1
    assert store.acquire_session('other', new_run(store, 'other'), 'example.com')[0].owner == 'other'
    with pytest.raises(LookupError):
        store.acquire_session('other', run_ids[0], 'other.com')


def test_lease_blocks_import_and_stale_revision(store):
    first = store.save_session('owner', 'example.com', STATE, 'unverified')
    lease, state, status = store.acquire_session('owner', new_run(store), 'example.com')
    assert status == 'unverified' and state['cookies'][0]['value'] == 'private-session-token'
    with pytest.raises(SessionConflict):
        store.save_session('owner', 'example.com', STATE)
    saved = store.save_session('owner', 'example.com', STATE, lease=lease)
    assert saved['revision'] == first['revision'] + 1 == lease.revision
    with pytest.raises(SessionConflict):
        store.save_session('owner', 'example.com', STATE, lease=lease, expected_revision=first['revision'])
    store.require_session(lease)
    store.release_session(lease)
    with pytest.raises(SessionConflict):
        store.save_session('owner', 'example.com', STATE, lease=lease)


def test_expired_holder_cannot_save_or_release_replacement(store):
    first = store.acquire_session('owner', new_run(store), 'example.com')[0]
    with store.runs.connect() as db:
        db.execute('UPDATE browser_session_leases SET expires_at=0')
    second = store.acquire_session('owner', new_run(store), 'example.com')[0]
    with pytest.raises(SessionConflict):
        store.save_session('owner', 'example.com', STATE, lease=first)
    store.release_session(first)
    store.require_session(second, renew=True)
    with store.runs.connect() as db:
        assert db.execute('SELECT expires_at FROM browser_session_leases').fetchone()[0] > time.time() + 20


def test_delete_then_import_cannot_be_overwritten_by_old_holder(store):
    saved = store.save_session('owner', 'example.com', STATE)
    lease = store.acquire_session('owner', new_run(store), 'example.com')[0]
    store.delete_session('owner', saved['id'])
    with pytest.raises(SessionConflict):
        store.save_session('owner', 'example.com', STATE, lease=lease)
    assert store.list_sessions('owner') == []
    replacement = store.save_session('owner', 'example.com', STATE)
    assert replacement['id'] != saved['id']
    with pytest.raises(SessionConflict):
        store.save_session('owner', 'example.com', STATE, lease=lease)


def test_revision_is_authenticated_and_legacy_state_is_readable(store):
    saved = store.save_session('owner', 'example.com', STATE)
    with store.runs.connect() as db:
        db.execute('UPDATE browser_sessions SET revision=revision+1')
    with pytest.raises(BrowserUnavailable):
        store.load_session('owner', 'example.com')
    with store.runs.connect() as db:
        legacy = encrypt(STATE, store.settings.encryption_key(), f"owner|example.com|{saved['id']}")
        db.execute('UPDATE browser_sessions SET state=?,state_version=1', (legacy,))
    assert store.load_session('owner', 'example.com')['cookies'][0]['value'] == 'private-session-token'
    assert store.save_session('owner', 'example.com', STATE)['revision'] == 3
    with store.runs.connect() as db:
        assert db.execute('SELECT state_version FROM browser_sessions').fetchone()[0] == 2


@pytest.mark.parametrize('status,phase', [('cancelled', 'cancelled'), ('partial', 'worker_interrupted'), ('failed', 'budget_exhausted')])
@pytest.mark.parametrize('already_unverified', [False, True])
def test_finish_releases_leases_and_preserves_uncertainty(store, status, phase, already_unverified):
    run_id = new_run(store)
    lease = store.acquire_session('owner', run_id, 'example.com')[0]
    action = store.create_action('owner', run_id, 'final', 'Order', 'Order', 'https://example.com/checkout', 'hash')
    store.update_action('owner', run_id, action, 'approved')
    store.update_action('owner', run_id, action, 'submitted')
    if already_unverified:
        store.update_action('owner', run_id, action, 'unverified')
    store.runs.finish(run_id, status, phase)
    result = store.runs.get('owner', run_id)
    assert 'uncertain' in result['result']['reply']
    assert result['status'] == ('cancelled' if status == 'cancelled' else 'partial')
    assert store.actions('owner', run_id)[0]['status'] == 'unverified'
    with pytest.raises(ValueError):
        store.require_session(lease)
    assert store.acquire_session('owner', new_run(store), 'example.com')[0]


def test_uncertain_submission_blocks_other_runs_until_owner_resolves(store):
    first, second = new_run(store), new_run(store)
    action = store.create_action('owner', first, 'final', 'Order', 'Order', 'https://example.com/checkout', 'hash')
    store.update_action('owner', first, action, 'approved')
    store.update_action('owner', first, action, 'submitted')
    store.update_action('owner', first, action, 'unverified')
    with pytest.raises(SessionConflict):
        store.resolve_action('owner', action, 'not_completed', 'Checked account')
    store.runs.finish(first, 'partial', 'submission_unknown')
    with pytest.raises(SessionConflict):
        store.create_action('owner', second, 'final', 'Order', 'Order', 'https://shop.example.com/checkout', 'hash')
    with pytest.raises(LookupError):
        store.resolve_action('other', action, 'confirmed', 'Checked account')
    assert store.unresolved_actions('other') == []
    store.resolve_action('owner', action, 'not_completed', 'No order in account history')
    assert store.unresolved_actions('owner') == []
    assert store.create_action('owner', second, 'final', 'Order', 'Order', 'https://example.com/checkout', 'hash')


def test_authentication_is_bound_to_one_https_site(deps):
    async def flow():
        await deps.bind_session('example.com')
        for url in ('http://example.com/account', 'https://other.com/account'):
            result = await deps.perform('open', url=url)
            assert 'HTTPS on the login site' in result
        deps.page.url = 'https://other.com'
        with pytest.raises(ValueError, match='separate browser task'):
            await deps.bind_session('other.com')
    asyncio.run(flow())


def test_deletion_revokes_live_actions_and_wipes_pending_password(deps):
    saved = deps.store.save_session('owner', 'example.com', STATE)
    async def flow():
        await deps.perform('open', url='https://example.com/account')
        request = deps.store.create_request('owner', deps.run_id, 'input', 'example.com', {'reason': 'Login', 'fields': [
            {'id': 'password', 'label': 'Password', 'kind': 'password'}]})
        deps.store.answer_request('owner', deps.run_id, request['id'], {'values': {'password': 'sensitive-password'}})
        deps.store.delete_session('owner', saved['id'])
        with pytest.raises(BrowserStop, match='deleted'):
            await deps.perform('click', ref='e1')
        with deps.store.runs.connect() as db:
            row = db.execute('SELECT status,secret_values FROM browser_requests').fetchone()
            assert tuple(row) == ('expired', None)
        assert deps.page.clicks == 0
    asyncio.run(flow())


@pytest.mark.parametrize('verified', [False, True])
def test_save_requires_fresh_account_verification(deps, monkeypatch, verified):
    deps.page.text = 'Account for Alice'
    deps.page.url = 'https://example.com/account'
    deps.page.context.storage_state.return_value = STATE
    verifier = AsyncMock(return_value=filter_storage_state(STATE, 'example.com') if verified else None)
    monkeypatch.setattr(actions, 'verify_login', verifier)
    result = asyncio.run(deps.perform('save_login', evidence_quote='Account for Alice'))
    assert deps.store.list_sessions('owner')[0]['status'] == ('saved' if verified else 'unverified')
    assert ('Verified and saved' in result) is verified
    verifier.assert_awaited_once()


def test_empty_login_state_cannot_be_marked_saved(deps, monkeypatch):
    deps.page.text = 'My account'
    verifier = AsyncMock()
    monkeypatch.setattr(actions, 'verify_login', verifier)
    result = asyncio.run(deps.perform('save_login', evidence_quote='My account'))
    assert 'no login state' in result
    verifier.assert_not_awaited()
    assert deps.store.list_sessions('owner') == []


@pytest.mark.parametrize('anonymous,authenticated,password,expected', [
    ('Sign in', 'Account for Alice', 0, True),
    ('Account for Alice', 'Account for Alice', 0, False),
    ('Sign in', 'Sign in', 0, False),
    ('Sign in', 'Account for Alice', 1, False),
])
def test_fresh_verifier_rejects_public_evidence_and_login_forms(monkeypatch, anonymous, authenticated, password, expected):
    pages = []
    @asynccontextmanager
    async def connection(*args, **kwargs):
        page = FakePage()
        page.text = anonymous if not pages else authenticated
        page.password_count = 0 if not pages else password
        page.context.clear_cookies = AsyncMock()
        page.context.storage_state.return_value = STATE
        pages.append(page)
        yield page, None
    monkeypatch.setattr(sessions, 'open_browser', connection)
    result = asyncio.run(sessions.verify_login(BrowserSettings(), STATE, 'https://example.com/account', 'Account for Alice', AsyncMock()))
    assert bool(result) is expected
    assert len(pages) == (1 if anonymous == 'Account for Alice' else 2)
    pages[0].context.add_cookies.assert_not_awaited()
    if len(pages) == 2:
        pages[1].context.add_cookies.assert_awaited_once()


def test_magic_link_is_private_single_use_and_scoped(deps):
    async def flow():
        task = asyncio.create_task(deps.perform('ask_user', reason='Sign in', fields=[InputField(id='link', label='Sign-in link', kind='magic_link')]))
        async with asyncio.timeout(3):
            while not (request := deps.store.state('owner', deps.run_id)['request']):
                await asyncio.sleep(0.01)
        url = 'https://example.com/login?token=private-magic-token'
        deps.store.answer_request('owner', deps.run_id, request['id'], {'values': {'link': url}})
        result = await task
        result += await deps.perform('open_login_link', secret='link')
        assert deps.page.url == url
        assert 'private-magic-token' not in result
        assert 'private-magic-token' not in json.dumps(deps.store.state('owner', deps.run_id))
        assert 'new private sign-in link' in await deps.perform('open_login_link', secret='link')
        deps.secrets['wrong'] = {'kind': 'magic_link', 'site': 'example.com', 'value': 'https://other.com/login', 'expires_at': time.monotonic() + 60}
        assert 'HTTPS on the login site' in await deps.perform('open_login_link', secret='wrong')
        assert deps.page.url == url
    asyncio.run(flow())


@pytest.mark.parametrize('revocation', ['delete', 'cancel'])
def test_watchdog_stops_waiting_model_and_closes_browser(store, monkeypatch, revocation):
    monkeypatch.setenv('BROWSER_SESSION_KEY', 'ab' * 32)
    saved = store.save_session('owner', 'example.com', STATE)
    chat = ChatStore(str(store.runs.path)).create('owner')
    run_id = store.runs.create('owner', chat['id'], 'run', 'Sign in', 'auto')['id']
    store.runs.claim()
    events = []
    @asynccontextmanager
    async def connection(*args, **kwargs):
        try:
            yield FakePage(), None
        finally:
            events.append('closed')
    async def run(*args, deps, **kwargs):
        await deps.perform('open', url='https://example.com/account')
        if revocation == 'delete':
            store.delete_session('owner', saved['id'])
        else:
            store.runs.finish(run_id, 'cancelled', 'cancelled')
        try:
            await asyncio.sleep(10)
        finally:
            events.append('model_cancelled')
        return SimpleNamespace(output=BrowserReport(outcome='done', summary='Should not finish'))
    monkeypatch.setattr(browser_run, 'open_browser', connection)
    monkeypatch.setattr(browser_run, 'build_agent', lambda _: SimpleNamespace(run=run))
    started = time.monotonic()
    browser_run.execute_browser(run_id, {'owner': 'owner'}, 'Sign in', '', {}, store.runs)
    assert time.monotonic() - started < 5
    assert events == ['model_cancelled', 'closed']
    result = store.runs.get('owner', run_id)
    assert result['status'] == ('cancelled' if revocation == 'cancel' else 'partial')
    if revocation == 'delete':
        assert store.list_sessions('owner') == []
    with store.runs.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM browser_session_leases').fetchone()[0] == 0


def test_storage_state_limits_and_origin_validation():
    with pytest.raises(ValueError):
        filter_storage_state({'cookies': 'invalid'}, 'example.com')
    with pytest.raises(ValueError):
        filter_storage_state({'origins': [{'origin': 'https://example.com', 'localStorage': [{'name': 'big', 'value': 'x' * 1_048_576}]}]}, 'example.com')
    state = filter_storage_state({'cookies': [None, {'domain': 1}, {'name': 'a', 'value': 'expired', 'domain': 'example.com', 'expires': 1}],
                                  'origins': [None, {'origin': 'http://example.com', 'localStorage': []},
                                              {'origin': 'https://example.com/path', 'localStorage': []},
                                              {'origin': 'https://example.com', 'localStorage': [{'name': 'bad', 'value': 3}]}]}, 'example.com')
    assert state == {'cookies': [], 'origins': []}


def test_verification_timeout_keeps_encrypted_unverified_login(deps, monkeypatch):
    deps.page.text = 'Account for Alice'
    deps.page.context.storage_state.return_value = STATE
    monkeypatch.setattr(actions, 'verify_login', AsyncMock(side_effect=TimeoutError))
    with pytest.raises(TimeoutError):
        asyncio.run(deps.perform('save_login', evidence_quote='Account for Alice'))
    assert deps.store.list_sessions('owner')[0]['status'] == 'unverified'
    assert deps.store.load_session('owner', 'example.com')['cookies'][0]['value'] == 'private-session-token'
