from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.browser import run as browser_run
from app.browser.actions import BrowserStop
from app.browser.agent import BrowserReport
from app.browser.config import BrowserUnavailable
from app.runs import RunStore
from app.store import ChatStore
from browser_fakes import FakePage


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'run.sqlite3'))
    store = RunStore()
    chat = ChatStore(str(store.path)).create('owner')
    run = store.create('owner', chat['id'], 'request', 'Open a page', 'auto')
    store.claim()
    return store, run['id'], {'owner': 'owner'}


@pytest.mark.parametrize('outcome,phase,status', [('done', 'browser_done', 'completed'),
    ('blocked', 'browser_blocked', 'partial'), ('needs_user', 'browser_needs_user', 'partial')])
def test_browser_run_result_and_cleanup(runtime, monkeypatch, outcome, phase, status):
    store, run_id, row = runtime
    closed = []
    @asynccontextmanager
    async def connection(*args, **kwargs):
        try:
            yield FakePage(), 'https://hb.oxylabs.io/novnc/?id=private-id'
        finally:
            closed.append(True)
    monkeypatch.setattr(browser_run, 'open_browser', connection)
    report = BrowserReport(outcome=outcome, summary='Task result')
    monkeypatch.setattr(browser_run, 'build_agent', lambda settings: SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=report))))
    browser_run.execute_browser(run_id, row, 'Open page', '', {}, store)
    result = store.get('owner', run_id)
    assert result['phase'] == phase and result['status'] == status
    assert result['result']['reply'] == 'Task result'
    assert result['browser']['live_view_url'] is None
    assert closed == [True]
    assert 'private-id' not in str(result['debug'])


def test_restricted_browser_failure(runtime, monkeypatch):
    store, run_id, row = runtime
    @asynccontextmanager
    async def connection(*args, **kwargs):
        raise BrowserUnavailable('1337 Invalid target')
        yield
    monkeypatch.setattr(browser_run, 'open_browser', connection)
    monkeypatch.setattr(browser_run, 'build_agent', lambda settings: None)
    browser_run.execute_browser(run_id, row, 'Open page', '', {}, store)
    assert store.get('owner', run_id)['phase'] == 'browser_blocked'


def test_timeout_after_submission_reports_uncertainty(runtime, monkeypatch):
    store, run_id, row = runtime
    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield FakePage(), None
    async def run(*args, deps, **kwargs):
        action = deps.store.create_action('owner', run_id, 'final', 'Order', 'Place order', 'https://example.com/checkout', 'hash')
        deps.store.update_action('owner', run_id, action, 'approved')
        deps.store.update_action('owner', run_id, action, 'submitted')
        deps.pending_action = {'id': action, 'name': 'Place order'}
        raise TimeoutError
    monkeypatch.setattr(browser_run, 'open_browser', connection)
    monkeypatch.setattr(browser_run, 'build_agent', lambda settings: SimpleNamespace(run=run))
    browser_run.execute_browser(run_id, row, 'Order', '', {}, store)
    result = store.get('owner', run_id)
    assert result['phase'] == 'submission_unknown'
    assert result['result']['browser']['actions'][0]['status'] == 'unverified'
    assert 'Check your account' in result['result']['reply']


def test_invalid_tool_arguments_are_not_reported_as_provider_failure(runtime, monkeypatch):
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    store, run_id, row = runtime
    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield FakePage(), None
    async def run(*args, deps, **kwargs):
        deps.debug['browser']['validation_errors'] = [{'tool': 'fill', 'issues': [{'path': 'fields.0.ref', 'code': 'string_pattern_mismatch'}]}]
        raise UnexpectedModelBehavior('Tool fill failed with private-value')
    monkeypatch.setattr(browser_run, 'open_browser', connection)
    monkeypatch.setattr(browser_run, 'build_agent', lambda settings: SimpleNamespace(run=run))
    browser_run.execute_browser(run_id, row, 'Search', '', {}, store)
    result = store.get('owner', run_id)
    assert result['phase'] == 'browser_needs_user'
    assert result['debug']['error']['code'] == 'browser_tool_validation_failed'
    assert 'fill' in result['result']['reply']
    assert 'private-value' not in str(result)
