import asyncio
import re
import time
from urllib.parse import urlsplit

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app.browser.actions import BrowserDeps, BrowserStop
from app.browser.agent import FillField, build_agent
from app.browser.config import BrowserSettings, BrowserUnavailable
from app.browser.connection import open_browser
from app.browser.observe import observe
from app.browser.consent import dismiss_cookie_dialog
from app.browser.store import BrowserStore
from app.runs import RunStore
from app.store import ChatStore


@pytest.fixture(scope='module', params=['playwright', 'patchright'])
def chromium_available(request):
    async def probe():
        async with open_browser(BrowserSettings(backend='local', driver=request.param)):
            pass
    try:
        asyncio.run(probe())
    except BrowserUnavailable as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip(f'Install Chromium with uv run {request.param} install chromium to run browser fixtures')
        if 'Operation not permitted' in str(exc):
            pytest.skip('This execution sandbox does not permit Chromium startup')
        raise
    return request.param


def fixture_store(tmp_path, driver):
    runs = RunStore(str(tmp_path / 'flows.sqlite3'))
    chat = ChatStore(str(runs.path)).create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Order one pizza', 'auto')
    runs.claim()
    settings = BrowserSettings(backend='local', driver=driver, session_key='ab' * 32)
    store = BrowserStore(runs, settings)
    store.start('owner', run['id'])
    return store, run['id'], settings


def test_real_accessibility_refs(chromium_available):
    async def flow():
        async with open_browser(BrowserSettings(backend='local', driver=chromium_available)) as (page, _):
            await page.set_content('<button onclick="this.textContent=\'Selected\'">Choose</button>')
            observation = await observe(page)
            ref = re.search(r'\[ref=(e\d+)\]', observation.snapshot)[1]
            await page.locator('aria-ref=' + ref).click()
            assert 'Selected' in (await observe(page, observation)).snapshot
    asyncio.run(flow())


@pytest.mark.parametrize('frame', [False, True])
def test_custom_listbox_selection_is_scoped_and_idempotent(chromium_available, tmp_path, frame):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            body = '''<button role="combobox" aria-label="Size" aria-haspopup="listbox" aria-expanded="false"
                onclick="menu.hidden=false;this.setAttribute('aria-expanded','true')"
                onkeydown="if(event.key==='Escape'){menu.hidden=true;this.setAttribute('aria-expanded','false')}">Small</button>
                <ul role="listbox" id="menu" hidden>
                <li role="option" aria-selected="true" onclick="pick(this)">Small</li>
                <li role="option" aria-selected="false" onclick="pick(this)">Large</li></ul>
                <script>document.body.dataset.selections=0;function pick(option){
                  document.body.dataset.selections=Number(document.body.dataset.selections)+1;
                  for(const item of menu.children)item.setAttribute('aria-selected',String(item===option));
                  const input=document.querySelector('button');input.textContent=option.textContent;
                  input.setAttribute('aria-expanded','false');menu.hidden=true;}</script>'''
            async def website(route):
                await route.fulfill(content_type='text/html', body='<iframe src="/picker"></iframe>'
                    if frame and urlsplit(route.request.url).path == '/' else body)
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com/')
            for _ in range(2):
                ref = re.search(r'combobox "Size"[^\n]*\[ref=((?:f\d+)?e\d+)\]', deps.observation.text)[1]
                await deps.perform('select', ref=ref, option='Large')
                assert deps.debug['browser']['last_step']['status'] == 'ok'
            document = page.frames[1] if frame else page
            assert await document.get_by_role('combobox').inner_text() == 'Large'
            assert await document.locator('body').get_attribute('data-selections') == '1'
    asyncio.run(flow())


@pytest.mark.parametrize('placeholder,allowed,path', [('Hae tuotteita...', True, '/search'),
    ('Search products', True, '/search'), ('Email', False, '/search'), ('Search', False, '/checkout')])
def test_enter_search_without_form_keeps_checkout_and_non_search_guards(chromium_available, tmp_path, placeholder, allowed, path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                await route.fulfill(content_type='text/html', body=f'<input placeholder="{placeholder}" '
                    'onkeydown="if(event.key===\'Enter\')document.querySelector(\'h1\').textContent=\'Search results: pita\'">'
                    '<h1>Catalog</h1>')
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com' + path)
            ref = re.search(r'textbox[^\n]*\[ref=(e\d+)\]', deps.observation.text)[1]
            await deps.perform('fill', fields=[FillField(ref=ref, value='pita')])
            result = await deps.perform('press', key='Enter')
            assert (await page.locator('h1').inner_text() == 'Search results: pita') is allowed
            assert ('Search submitted' in result) is allowed
    asyncio.run(flow())


@pytest.mark.parametrize('late_frame', [False, True])
def test_text_wait_finds_embedded_form_without_exhausting_timeout(chromium_available, tmp_path, late_frame):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)

    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                if urlsplit(route.request.url).path == '/form':
                    body = '<h2>Choose a service</h2>'
                elif late_frame:
                    body = ('<h1>Booking</h1><script>setTimeout(() => {'
                            'const frame = document.createElement("iframe"); frame.src="/form"; '
                            'document.body.append(frame);}, 300)</script>')
                else:
                    body = '<h1>Booking</h1><iframe src="/form"></iframe>'
                await route.fulfill(content_type='text/html', body=body)

            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await page.goto('https://browser.example.com/', wait_until='domcontentloaded')
            started = time.monotonic()
            await deps._wait_for_text('Choose a service', seconds=5)
            assert time.monotonic() - started < 3

    asyncio.run(flow())


def test_control_checks_preserve_other_field_refs(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            await page.set_content('<label>Name<input></label><label>Condition<input></label>'
                                   '<label>Review<textarea></textarea></label>')
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.observe()
            refs = re.findall(r'textbox[^\n]*\[ref=(e\d+)\]', deps.observation.text)
            fields = [FillField(ref=ref, value=value) for ref, value in
                      zip(refs, ['chris123', 'okay', 'The condition was okay.'], strict=True)]
            result = await deps.perform('fill', fields=fields)
            assert 'Fields filled.' in result
            assert await page.locator('input, textarea').evaluate_all(
                'elements => elements.map(element => element.value)'
            ) == ['chris123', 'okay', 'The condition was okay.']
    asyncio.run(flow())


def test_frame_refs_fill_select_and_click(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                body = ('<iframe src="/form"></iframe>' if urlsplit(route.request.url).path == '/'
                        else '<label>Name<input></label><label>Address<input></label>'
                             '<label>Size<select><option>Small</option><option>Large</option></select></label>'
                             '<button onclick="this.textContent=\'Selected\'">Choose</button>')
                await route.fulfill(content_type='text/html', body=body)
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com/')
            def ref(name):
                return re.search(r'"' + name + r'"[^\n]*\[ref=(f\d+e\d+)\]', deps.observation.text)[1]
            result = await deps.perform('fill', fields=[FillField(ref=ref('Name'), value='Chris'),
                                                        FillField(ref=ref('Address'), value='Hotel Matts')])
            assert 'Fields filled.' in result
            await deps.perform('select', ref=ref('Size'), option='Large')
            clicked_ref = ref('Choose')
            await deps.perform('click', ref=clicked_ref)
            frame = page.frames[1]
            assert await frame.get_by_label('Name', exact=True).input_value() == 'Chris'
            assert await frame.get_by_label('Address', exact=True).input_value() == 'Hotel Matts'
            assert await frame.get_by_role('combobox', name='Size', exact=True).input_value() == 'Large'
            assert await frame.get_by_role('button', name='Selected').is_visible()
            assert deps.debug['browser']['last_step']['ref'] == clicked_ref
    asyncio.run(flow())


def test_main_frame_refs_after_navigation_failure(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                if urlsplit(route.request.url).path == '/error':
                    await route.abort('failed')
                else:
                    await route.fulfill(content_type='text/html', body='<label>Search<input></label>'
                                        '<button onclick="this.textContent=\'Results\'">Find</button><a href="/error">Broken link</a>')
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com/')
            await deps.perform('open', url='https://browser.example.com/error')
            await page.wait_for_url('chrome-error://chromewebdata/')
            await deps.perform('open', url='https://browser.example.com/')
            field = re.search(r'textbox "Search"[^\n]*\[ref=(f\d+e\d+)\]', deps.observation.text)[1]
            assert 'Fields filled.' in await deps.perform('fill', fields=[FillField(ref=field, value='pitakebab')])
            button = re.search(r'button "Find"[^\n]*\[ref=(f\d+e\d+)\]', deps.observation.text)[1]
            await deps.perform('submit', ref=button, kind='search', summary='Search for pita kebab')
            assert await page.get_by_role('textbox', name='Search').input_value() == 'pitakebab'
            assert await page.get_by_role('button', name='Results').is_visible()
            assert deps.debug['browser']['last_step']['status'] == 'ok'
    asyncio.run(flow())


def test_frame_portal_keeps_foreground_controls(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                body = ('<button>Background</button><iframe src="/popup"></iframe>'
                        if urlsplit(route.request.url).path == '/' else
                        '<div role="region" id="portal-product"><button>Start ordering</button></div>')
                await route.fulfill(content_type='text/html', body=body)
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com/')
            assert deps.observation.blocks_background
            assert 'Start ordering' in deps.observation.foreground
            background = re.search(r'button "Background"[^\n]*\[ref=(e\d+)\]', deps.observation.text)[1]
            with pytest.raises(ValueError, match='behind the foreground'):
                deps.locator(background)
            foreground = re.search(r'button "Start ordering"[^\n]*\[ref=(f\d+e\d+)\]', deps.observation.text)[1]
            assert await deps.locator(foreground).is_visible()
    asyncio.run(flow())


def test_frame_controls_respect_authenticated_site_scope(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                body = ('<iframe src="https://other.example.net/form"></iframe>'
                        if urlsplit(route.request.url).hostname == 'browser.example.com'
                        else '<label>Password<input type="password"></label>')
                await route.fulfill(content_type='text/html', body=body)
            await page.context.route('https://**/*', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com/')
            await deps.bind_session('example.com')
            deps.secrets['password'] = {'site': 'example.com', 'kind': 'password', 'value': 'private-password'}
            ref = re.search(r'textbox "Password"[^\n]*\[ref=(f\d+e\d+)\]', deps.observation.text)[1]
            result = await deps.perform('fill', fields=[FillField(ref=ref, secret='password')])
            assert 'Authenticated browsing requires HTTPS on the login site' in result
            assert await page.frames[1].get_by_label('Password').input_value() == ''
            assert 'private-password' not in result
    asyncio.run(flow())


@pytest.mark.parametrize('popup', [False, True])
def test_portal_inspection_preserves_page_refs(chromium_available, popup):
    async def flow():
        async with open_browser(BrowserSettings(backend='local', driver=chromium_available)) as (page, _):
            await page.set_content('<button>Account</button><div role="region" id="portal-toast"></div>'
                                   + ('<div role="region" id="portal-product"><button>Add to basket</button></div>'
                                      if popup else ''))
            observation = await observe(page)
            for ref in re.findall(r'button[^\n]*\[ref=(e\d+)\]', observation.text):
                assert await page.locator('aria-ref=' + ref).is_visible()
            assert observation.blocks_background == popup
            if popup:
                assert 'Add to basket' in observation.foreground
                assert 'Account' not in observation.foreground
    asyncio.run(flow())


@pytest.mark.parametrize('decision', ['approve', 'decline'])
@pytest.mark.parametrize('framed', [False, True])
def test_real_order_flow(chromium_available, tmp_path, decision, framed):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    orders = []
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                if framed and urlsplit(route.request.url).path == '/checkout':
                    html = '<iframe src="/form"></iframe>'
                elif urlsplit(route.request.url).path == '/confirmation':
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
                    ref = re.search(r'button "Place order"[^\n]*\[ref=((?:f\d+)?e\d+)\]', deps.observation.snapshot)[1]
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
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
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


@pytest.mark.parametrize('label', ['Allow', 'Use only necessary', 'Hylkää', 'Hyväksyn'])
def test_cookie_dismissal_unlocks_address_dialog(chromium_available, label):
    async def flow():
        async with open_browser(BrowserSettings(backend='local', driver=chromium_available)) as (page, _):
            await page.set_content('''
                <button onclick="document.querySelector('#address').showModal()">Choose address</button>
                <main>''' + '<p>Catalog item</p>' * 14000 + '''</main>
                <dialog id="address"><label>Address<input></label><button>Confirm address</button></dialog>
                <div role="dialog" style="position:fixed;inset:0;background:white">
                We use cookies <button onclick="this.parentElement.remove()">''' + label + '''</button></div>''')
            async def active(): pass
            assert await dismiss_cookie_dialog(page, active, lambda url: None, set())
            await page.get_by_role('button', name='Choose address', exact=True).click()
            snapshot = await observe(page)
            assert 'textbox "Address"' in snapshot.snapshot[:1000]
            ref = re.search(r'textbox "Address"[^\n]*\[ref=(e\d+)\]', snapshot.snapshot)[1]
            await page.locator('aria-ref=' + ref).fill('Matts Hotel Espoo')
            assert await page.get_by_role('textbox').input_value() == 'Matts Hotel Espoo'
    asyncio.run(flow())


@pytest.mark.parametrize('cookie', [False, True])
def test_consent_does_not_claim_unrelated_or_failed_dismissal(chromium_available, cookie):
    async def flow():
        async with open_browser(BrowserSettings(backend='local', driver=chromium_available)) as (page, _):
            await page.set_content('<div role="dialog">' + ('Cookies' if cookie else 'Location permission')
                                   + '<button onclick="this.remove()">Allow</button></div>')
            async def active(): pass
            assert not await dismiss_cookie_dialog(page, active, lambda url: None, set())
            assert await page.get_by_role('dialog').is_visible()
            if not cookie:
                assert await page.get_by_role('button', name='Allow').count() == 1
    asyncio.run(flow())


def test_real_address_combobox_selection_before_continue(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            html = '''<main>''' + '<p>Catalog item</p>' * 14000 + '''</main>
              <dialog open aria-label="Delivery address">
                <label>Street name and number<input id="street" role="combobox"
                  oninput="document.getElementById('choices').hidden=false"></label>
                <button id="continue" disabled onclick="this.parentElement.textContent='Delivery address selected: Matts Hotel Espoo'">Continue</button>
              </dialog>
              <div id="choices" role="listbox" hidden>
                <button type="button" role="option" onclick="document.getElementById('continue').disabled=false;this.parentElement.hidden=true">Matts Hotel Espoo</button>
              </div>'''
            await page.context.route('https://browser.example.com/**',
                                     lambda route: route.fulfill(content_type='text/html', body=html))
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com/address')
            def ref(role, name):
                return re.search(role + ' "' + name + r'"[^\n]*\[ref=(e\d+)\]', deps.observation.snapshot)[1]
            await deps.perform('fill', fields=[FillField(ref=ref('combobox', 'Street name and number'), value='Matts Hotel Espoo')])
            assert await page.get_by_role('button', name='Continue').is_disabled()
            await deps.perform('click', ref=ref('option', 'Matts Hotel Espoo'))
            assert await page.get_by_role('button', name='Continue').is_enabled()
            result = await deps.perform('submit', ref=ref('button', 'Continue'), kind='checkout_step', summary='Select delivery address')
            assert 'Delivery address selected: Matts Hotel Espoo' in result
            assert store.actions('owner', run_id) == []
    asyncio.run(flow())
