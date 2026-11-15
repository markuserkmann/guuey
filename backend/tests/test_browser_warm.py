import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app import worker
from app.browser import run as browser_run
from app.browser.agent import BrowserReport
from app.browser.schemas import FormChoice
from app.browser.store import BrowserStore
from app.orchestration.decisions import RouteDecision
from app.runs import RunStore
from app.store import ChatStore
from browser_fakes import FakePage


@pytest.fixture
def warm_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'warm.sqlite3'))
    monkeypatch.setenv('BROWSER_ENABLED', 'true')
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='browser', confidence=1))
    runs = RunStore()
    chat = ChatStore().create('owner')['id']
    first = runs.create('owner', chat, 'first', 'Open the shop', 'auto')['id']
    runs.claim()
    pages, closed, guards = [], [], []

    @asynccontextmanager
    async def connection(*args, **kwargs):
        page = FakePage()
        page.is_closed = lambda: page in closed
        pages.append(page)
        guards.append(kwargs['request_guard'])
        try:
            yield page, 'https://hb.oxylabs.io/novnc/?id=private-session'
        finally:
            closed.append(page)

    monkeypatch.setattr(browser_run, 'open_browser', connection)
    finish = RunStore.finish

    def quick_finish(self, *args, **kwargs):
        kwargs['warm_seconds'] = 0.6
        return finish(self, *args, **kwargs)

    monkeypatch.setattr(RunStore, 'finish', quick_finish)
    return SimpleNamespace(runs=runs, chat=chat, first=first, pages=pages, closed=closed, guards=guards)


async def wait_finished(runs, run_id):
    async with asyncio.timeout(3):
        while runs.get('owner', run_id)['status'] == 'running':
            await asyncio.sleep(0.01)


def test_worker_reuses_page_and_finishes_before_idle_timeout(warm_runtime, monkeypatch):
    rt = warm_runtime
    calls = []

    async def model(prompt, *, deps, **kwargs):
        calls.append(deps)
        assert deps.store.state('owner', deps.run_id)['live_view_url'].endswith('private-session')
        if len(calls) == 1:
            deps.page.text = 'Basket contains apples'
            deps.secrets['password'] = {'value': 'secret'}
            deps.secret_values.append('secret')
            deps.remember_choice(FormChoice(field='size', value='Small'))
        else:
            assert deps.page.text == 'Basket contains apples'
            assert 'Basket saved' in prompt
            assert deps.secrets == {} and deps.pending_action is None
            assert deps.observation is None
            assert deps.clean('secret') != 'secret'
            assert deps.assumptions == [{'site': 'example.com', 'field': 'size', 'value': 'Small'}]
            assert deps.assumptions is not calls[0].assumptions
            deps.remember_choice(FormChoice(field='size', value='Large', source='user'))
            assert deps.assumptions == []
        return SimpleNamespace(output=BrowserReport(outcome='done', summary='Basket saved'))

    monkeypatch.setattr(browser_run, 'build_agent', lambda _: SimpleNamespace(run=model))

    async def flow():
        task = asyncio.create_task(worker.serve_chat(rt.first))
        await wait_finished(rt.runs, rt.first)
        assert not rt.closed
        assert rt.runs.warm_browser_expiry('owner', rt.chat, rt.first) > time.time()
        with pytest.raises(ValueError, match='waiting for a task'):
            await rt.guards[0](SimpleNamespace())
        followup = rt.runs.create('owner', rt.chat, 'second', 'Check my basket', 'auto')['id']
        assert rt.runs.claim() is None
        async with asyncio.timeout(4):
            await task
        result = rt.runs.get('owner', followup)
        assert result['status'] == 'completed'
        assert result['debug']['browser']['reused'] is True
        assert rt.runs.worker_run(rt.first)['id'] == followup
        assert rt.runs.warm_browser_expiry('owner', rt.chat, rt.first) is None

    asyncio.run(flow())
    assert len(rt.pages) == 1 and rt.closed == rt.pages
    assert len(calls) == 2 and calls[0] is not calls[1]
    assert calls[0].secret_values == calls[1].secret_values == []


def test_idle_expiry_closes_browser_and_releases_chat(warm_runtime, monkeypatch):
    rt = warm_runtime

    async def model(*args, **kwargs):
        return SimpleNamespace(output=BrowserReport(outcome='done', summary='Done'))

    monkeypatch.setattr(browser_run, 'build_agent', lambda _: SimpleNamespace(run=model))
    asyncio.run(worker.serve_chat(rt.first))
    assert rt.closed == rt.pages and len(rt.pages) == 1
    next_run = rt.runs.create('owner', rt.chat, 'after', 'Next task', 'auto')['id']
    assert rt.runs.claim()['id'] == next_run


def test_cancelled_followup_closes_warm_browser(warm_runtime, monkeypatch):
    rt = warm_runtime
    started = []

    async def model(*args, deps, **kwargs):
        started.append(deps.run_id)
        if len(started) == 2:
            rt.runs.finish(deps.run_id, 'cancelled', 'cancelled')
            await asyncio.sleep(10)
        return SimpleNamespace(output=BrowserReport(outcome='done', summary='Done'))

    monkeypatch.setattr(browser_run, 'build_agent', lambda _: SimpleNamespace(run=model))

    async def flow():
        task = asyncio.create_task(worker.serve_chat(rt.first))
        await wait_finished(rt.runs, rt.first)
        second = rt.runs.create('owner', rt.chat, 'second', 'Continue', 'auto')['id']
        async with asyncio.timeout(4):
            await task
        assert rt.runs.get('owner', second)['status'] == 'cancelled'

    asyncio.run(flow())
    assert rt.closed == rt.pages and len(rt.pages) == 1


def test_login_deletion_during_idle_closes_connection(warm_runtime, monkeypatch):
    rt = warm_runtime
    monkeypatch.setenv('BROWSER_SESSION_KEY', 'ab' * 32)
    saved = []

    async def model(*args, deps, **kwargs):
        await deps.bind_session('example.com')
        state = {'cookies': [{'name': 'session', 'value': 'private-session', 'domain': '.example.com', 'path': '/'}], 'origins': []}
        deps.page.context.storage_state.return_value = state
        saved.append(deps.store.save_session('owner', 'example.com', state, lease=deps.lease))
        return SimpleNamespace(output=BrowserReport(outcome='done', summary='Done'))

    monkeypatch.setattr(browser_run, 'build_agent', lambda _: SimpleNamespace(run=model))

    async def flow():
        task = asyncio.create_task(worker.serve_chat(rt.first))
        await wait_finished(rt.runs, rt.first)
        assert not rt.closed
        BrowserStore(rt.runs).delete_session('owner', saved[0]['id'])
        async with asyncio.timeout(3):
            await task

    asyncio.run(flow())
    assert rt.closed == rt.pages and len(rt.pages) == 1


@pytest.mark.parametrize('kind', ['input', 'takeover'])
def test_reused_browser_waits_for_user_with_live_view(warm_runtime, monkeypatch, kind):
    from app.browser.schemas import InputField
    rt = warm_runtime
    monkeypatch.setenv('BROWSER_SESSION_KEY', '')
    calls = []

    async def model(*args, deps, **kwargs):
        calls.append(deps.run_id)
        if len(calls) == 2:
            if kind == 'input':
                await deps.perform('ask_user', reason='Which address?', fields=[InputField(id='address', label='Address', kind='text')])
            else:
                await deps.perform('request_takeover', reason='Finish the verification')
        return SimpleNamespace(output=BrowserReport(outcome='done', summary='Done'))

    monkeypatch.setattr(browser_run, 'build_agent', lambda _: SimpleNamespace(run=model))

    async def flow():
        task = asyncio.create_task(worker.serve_chat(rt.first))
        await wait_finished(rt.runs, rt.first)
        second = rt.runs.create('owner', rt.chat, 'second', 'Continue', 'auto')['id']
        store = BrowserStore(rt.runs)
        async with asyncio.timeout(4):
            while True:
                state = store.state('owner', second)
                if state and state['request']:
                    break
                await asyncio.sleep(0.01)
        assert state['request']['kind'] == kind
        assert state['live_view_url'].endswith('private-session')
        assert not rt.closed
        body = {'values': {'address': 'Tallinn'}} if kind == 'input' else {'done': True}
        store.answer_request('owner', second, state['request']['id'], body)
        async with asyncio.timeout(4):
            await task
        assert rt.runs.get('owner', second)['status'] == 'completed'

    asyncio.run(flow())
    assert len(rt.pages) == 1 and rt.closed == rt.pages


def test_real_chromium_preserves_form_between_worker_turns(warm_runtime, monkeypatch):
    from app.browser.connection import open_browser
    from app.browser.config import BrowserSettings, BrowserUnavailable
    rt = warm_runtime

    async def probe():
        async with open_browser(BrowserSettings(backend='local')):
            pass

    try:
        asyncio.run(probe())
    except BrowserUnavailable as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip('Playwright Chromium is not installed')
        raise
    monkeypatch.setenv('BROWSER_BACKEND', 'local')
    monkeypatch.setattr(browser_run, 'open_browser', open_browser)
    pages = []

    async def model(*args, deps, **kwargs):
        pages.append(deps.page)
        if len(pages) == 1:
            await deps.page.set_content('<label>Delivery address<input></label>')
            await deps.page.get_by_label('Delivery address').fill('Tallinn')
        else:
            assert await deps.page.get_by_label('Delivery address').input_value() == 'Tallinn'
        return SimpleNamespace(output=BrowserReport(outcome='done', summary='Address entered'))

    monkeypatch.setattr(browser_run, 'build_agent', lambda _: SimpleNamespace(run=model))

    async def flow():
        task = asyncio.create_task(worker.serve_chat(rt.first))
        await wait_finished(rt.runs, rt.first)
        second = rt.runs.create('owner', rt.chat, 'second', 'Check the address', 'auto')['id']
        async with asyncio.timeout(10):
            await task
        assert rt.runs.get('owner', second)['status'] == 'completed'
        assert len(pages) == 2 and pages[0] is pages[1]
        assert pages[0].is_closed()

    asyncio.run(flow())
