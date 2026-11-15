import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError

from app.browser.actions import BrowserDeps, BrowserStop
from app.browser.agent import FillField
from app.browser.config import BrowserSettings
from app.browser.schemas import InputField
from app.browser.store import BrowserStore
from app.runs import RunStore
from app.store import ChatStore
from browser_fakes import FakePage


@pytest.fixture
def deps(tmp_path):
    runs = RunStore(str(tmp_path / 'actions.sqlite3'))
    chat = ChatStore(str(runs.path)).create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Order a pizza', 'auto')
    runs.claim()
    settings = BrowserSettings(session_key='ab' * 32)
    store = BrowserStore(runs, settings)
    store.start('owner', run['id'])
    deps = BrowserDeps(FakePage(), store, 'owner', run['id'], settings)
    deps.settle = AsyncMock()
    return deps


async def pending_request(deps):
    async with asyncio.timeout(3):
        while True:
            request = deps.store.state(deps.owner, deps.run_id)['request']
            if request:
                return request
            await asyncio.sleep(0.01)


async def approved_action(deps, tool='submit', before_answer=None, **kwargs):
    task = asyncio.create_task(deps.perform(tool, **({'ref': 'e1', 'kind': 'final', 'summary': 'Order one pizza'} if tool == 'submit' else kwargs)))
    request = await pending_request(deps)
    assert deps.page.clicks == 0
    assert deps.store.actions(deps.owner, deps.run_id)[-1]['status'] == 'attempting'
    if before_answer:
        before_answer()
    deps.store.answer_request(deps.owner, deps.run_id, request['id'], {'decision': 'approve'})
    return await task


def test_final_submit_waits_for_approval(deps):
    async def flow():
        await approved_action(deps)
        assert deps.page.clicks == 1
        assert deps.store.actions(deps.owner, deps.run_id)[0]['status'] == 'submitted'
        await deps.perform('confirm', quote='Order #1001 received')
        assert deps.store.actions(deps.owner, deps.run_id)[0]['status'] == 'verified'
    asyncio.run(flow())


@pytest.mark.parametrize('tool,args', [('click', {'ref': 'e1'}), ('press', {'key': 'Enter'})])
def test_backstop_reroutes_click_and_enter_to_approval(deps, tool, args):
    asyncio.run(approved_action(deps, tool, **args))
    assert deps.page.clicks == 1


def test_decline_never_clicks_and_stops_later_tools(deps):
    async def flow():
        task = asyncio.create_task(deps.perform('click', ref='e1'))
        request = await pending_request(deps)
        deps.store.answer_request(deps.owner, deps.run_id, request['id'], {'decision': 'decline'})
        with pytest.raises(BrowserStop, match='declined'):
            await task
        with pytest.raises(BrowserStop):
            await deps.perform('click', ref='e1')
    asyncio.run(flow())
    assert deps.page.clicks == 0
    assert deps.store.actions(deps.owner, deps.run_id)[0]['status'] == 'declined'


@pytest.mark.parametrize('change', ['name', 'url', 'total'])
def test_approval_rechecks_target_and_page(deps, change):
    def changed():
        if change == 'name':
            deps.page.controls['e1'].name = 'Buy another item'
        elif change == 'url':
            deps.page.url = 'https://shop.example.com/different'
        else:
            deps.page.text = 'Checkout total €169.00'
    result = asyncio.run(approved_action(deps, before_answer=changed))
    assert 'changed after approval' in result
    assert deps.page.clicks == 0


def test_click_timeout_never_retries_an_uncertain_submission(deps):
    deps.page.controls['e1'].click_error = TimeoutError('click timed out')
    async def flow():
        await approved_action(deps)
        result = await deps.perform('click', ref='e1')
        assert 'awaiting confirmation' in result
        assert deps.page.clicks == 1
        await deps.perform('confirm', quote='Order #1001 received')
    asyncio.run(flow())
    assert deps.store.actions(deps.owner, deps.run_id)[0]['status'] == 'verified'


def test_confirm_requires_new_text(deps):
    deps.page.text = 'Order #1001 received'
    async def flow():
        await approved_action(deps)
        await deps.perform('confirm', quote='Order #1001 received')
        with pytest.raises(BrowserStop) as exc:
            await deps.perform('confirm', quote='Order #1001 received')
        assert exc.value.phase == 'submission_unknown'
    asyncio.run(flow())
    assert deps.store.actions(deps.owner, deps.run_id)[0]['status'] == 'unverified'


def test_cancel_during_approval(deps):
    async def flow():
        task = asyncio.create_task(deps.perform('click', ref='e1'))
        await pending_request(deps)
        deps.store.runs.finish(deps.run_id, 'cancelled', 'cancelled')
        with pytest.raises(BrowserStop):
            await task
    asyncio.run(flow())
    assert deps.page.clicks == 0
    assert deps.store.actions(deps.owner, deps.run_id)[0]['status'] == 'declined'


def test_approval_expiry(deps):
    async def flow():
        task = asyncio.create_task(deps.perform('click', ref='e1'))
        await pending_request(deps)
        with deps.store.runs.connect() as db:
            db.execute("UPDATE browser_requests SET expires_at='2000-01-01T00:00:00+00:00'")
        with pytest.raises(BrowserStop) as exc:
            await task
        assert exc.value.phase == 'approval_expired'
    asyncio.run(flow())
    assert deps.page.clicks == 0
    assert deps.store.actions(deps.owner, deps.run_id)[0]['status'] == 'expired'


def test_private_input_never_appears_in_results_or_steps(deps):
    async def flow():
        task = asyncio.create_task(deps.perform('ask_user', reason='Sign in', fields=[InputField(id='password', label='Password', kind='password')]))
        request = await pending_request(deps)
        deps.store.answer_request(deps.owner, deps.run_id, request['id'], {'values': {'password': 'private-password'}})
        result = await task
        result += await deps.perform('fill', fields=[FillField(ref='e2', secret='password')])
        assert 'private-password' not in result
        assert deps.page.controls['e2'].value == 'private-password'
        assert 'private-password' not in json.dumps(deps.store.state(deps.owner, deps.run_id))
        deps.page.url = 'https://another.com/login'
        deps.page.controls['e2'].value = ''
        result = await deps.perform('fill', fields=[FillField(ref='e2', secret='password')])
        assert 'not available for this site' in result
        assert deps.page.controls['e2'].value == ''
    asyncio.run(flow())


def test_card_fields_and_password_literal_are_refused(deps):
    async def flow():
        result = await deps.perform('fill', fields=[FillField(ref='e2', value='private-password')])
        assert 'ask_user' in result
        deps.page.controls['e3'].attrs['autocomplete'] = 'cc-number'
        result = await deps.perform('fill', fields=[FillField(ref='e3', value='4111111111111111')])
        assert 'Card fields' in result
    asyncio.run(flow())
    assert deps.page.controls['e2'].value == deps.page.controls['e3'].value == ''


def test_saved_session_restores_cookies_and_scoped_storage(deps):
    deps.page.text += '\n- link "Menu":\n  - /url: /menu'
    state = {'cookies': [{'name': 'session', 'value': 'private-session', 'domain': '.example.com'}],
             'origins': [{'origin': 'https://shop.example.com', 'localStorage': [{'name': 'token', 'value': 'private-token'}]}]}
    deps.store.save_session(deps.owner, 'example.com', state)
    async def flow():
        first = await deps.perform('open', url='https://shop.example.com/account')
        second = await deps.perform('open', url='https://shop.example.com/menu')
        assert 'Saved login state was restored for example.com' in first
        assert 'Saved login state was restored for example.com' in second
        assert 'does not prove' in first
        assert deps.restored_login_site == 'example.com'
    asyncio.run(flow())
    deps.page.context.add_cookies.assert_awaited_once()
    script = deps.page.context.add_init_script.await_args.args[0]
    assert 'location.origin !== entry.origin' in script
    assert 'private-token' in script
    assert 'private-token' not in json.dumps(deps.store.state(deps.owner, deps.run_id))
