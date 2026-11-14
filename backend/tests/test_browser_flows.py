import asyncio
import re
from urllib.parse import urlsplit

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.browser.actions import BrowserDeps, BrowserStop
from app.browser.agent import build_agent
from app.browser.config import BrowserSettings, BrowserUnavailable
from app.browser.connection import open_browser
from app.browser.observe import observe
from app.browser.store import BrowserStore
from app.runs import RunStore
from app.store import ChatStore


@pytest.fixture(scope='module')
def chromium_available():
    async def probe():
        async with open_browser(BrowserSettings(backend='local')):
            pass
    try:
        asyncio.run(probe())
    except BrowserUnavailable as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip('Install the locked Playwright Chromium version to run browser fixtures')
        if 'Operation not permitted' in str(exc):
            pytest.skip('This execution sandbox does not permit Chromium startup')
        raise


def fixture_store(tmp_path):
    runs = RunStore(str(tmp_path / 'flows.sqlite3'))
    chat = ChatStore(str(runs.path)).create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Order one pizza', 'auto')
    runs.claim()
    settings = BrowserSettings(backend='local', session_key='ab' * 32)
    store = BrowserStore(runs, settings)
    store.start('owner', run['id'])
    return store, run['id'], settings


def test_real_accessibility_refs(chromium_available):
    async def flow():
        async with open_browser(BrowserSettings(backend='local')) as (page, _):
            await page.set_content('<button onclick="this.textContent=\'Selected\'">Choose</button>')
            observation = await observe(page)
            ref = re.search(r'\[ref=(e\d+)\]', observation.snapshot)[1]
            await page.locator('aria-ref=' + ref).click()
            assert 'Selected' in (await observe(page, observation)).snapshot
    asyncio.run(flow())


@pytest.mark.parametrize('decision', ['approve', 'decline'])
def test_real_order_flow(chromium_available, tmp_path, decision):
    store, run_id, settings = fixture_store(tmp_path)
    orders = []
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                if urlsplit(route.request.url).path == '/confirmation':
                    orders.append(route.request.method)
                    html = '<h1>Order #1001 received</h1>'
                else:
                    html = '<h1>Margherita €16.90</h1><form method="post" action="/confirmation"><button>Place order</button></form>'
                await route.fulfill(content_type='text/html', body=html)
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            step = 0
            async def model(messages, info):
                nonlocal step
                step += 1
                if step == 1:
                    name, args = 'open', {'url': 'https://browser.example.com/checkout'}
                elif step == 2:
                    ref = re.search(r'button "Place order"[^\n]*\[ref=(e\d+)\]', deps.observation.snapshot)[1]
                    name, args = 'submit', {'ref': ref, 'kind': 'final', 'summary': 'One Margherita, €16.90'}
                elif step == 3:
                    name, args = 'confirm', {'quote': 'Order #1001 received'}
                else:
                    name, args = info.output_tools[0].name, {'outcome': 'done', 'summary': 'Order received.', 'sources': []}
                return ModelResponse(parts=[ToolCallPart(name, args)])
            task = asyncio.create_task(build_agent(settings, model=FunctionModel(model)).run('Order the pizza', deps=deps))
            async with asyncio.timeout(20):
                while True:
                    request = store.state('owner', run_id)['request']
                    if request:
                        assert orders == []
                        store.answer_request('owner', run_id, request['id'], {'decision': decision})
                        break
                    if task.done():
                        await task
                        pytest.fail('Order did not request approval')
                    await asyncio.sleep(0.01)
                if decision == 'approve':
                    assert (await task).output.outcome == 'done'
                else:
                    with pytest.raises(BrowserStop):
                        await task
    asyncio.run(flow())
    assert orders == (['POST'] if decision == 'approve' else [])
    assert store.actions('owner', run_id)[0]['status'] == ('verified' if decision == 'approve' else 'declined')


def test_real_saved_cookie_is_sent(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path)
    store.save_session('owner', 'example.com', {'cookies': [
        {'name': 'session', 'value': 'fixture-session', 'domain': 'browser.example.com', 'path': '/', 'secure': True},
    ], 'origins': []}, 'unverified')
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                assert 'session=fixture-session' in await route.request.header_value('cookie')
                await route.fulfill(content_type='text/html', body='<h1>Your account</h1>')
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            assert 'Your account' in await deps.perform('open', url='https://browser.example.com/account')
    asyncio.run(flow())
