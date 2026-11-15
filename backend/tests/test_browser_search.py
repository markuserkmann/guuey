import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from app.browser.actions import BrowserDeps, BrowserStop
from app.browser.connection import open_browser
from app.browser.agent import FillField, trim_history
from app.browser.observe import Observation, reframe
from app.browser.snapshot import compact_snapshot
from app.browser.navigation import navigation_key, page_urls, request_urls
from app.browser.warm import WarmBrowser
from test_browser_actions import deps as deps
from test_browser_flows import chromium_available as chromium_available, fixture_store


def test_product_cards_collapse_repeated_fields_without_losing_prices_or_seller():
    source = '''- 'button "Closed Price: 11,00 € Pita kebab Seller: Pepe''s Delivery: 2,50 €" [ref=f2e1]':
  - generic [ref=f2e2]:
    - text: Closed
    - generic [ref=f2e3]: "Price:"
    - text: 11,00 €
    - heading "Pita kebab" [ref=f2e4]
    - text: Seller:
    - text: Pepe's
    - text: ·
    - text: Delivery:
    - text: 2,50 €'''
    compact = compact_snapshot(source)
    assert len(compact.splitlines()) == 1
    assert all(value in compact for value in ['Closed', '11,00 €', 'Pita kebab', "Pepe''s", '2,50 €', '[ref=f2e1]'])


def test_compaction_keeps_details_missing_from_accessible_name_and_nested_controls():
    source = '''- button "A reasonably long product name and description" [ref=e1]:
  - text: Available tomorrow only
  - text: Delivery €25
  - checkbox "Gift wrap" [checked] [ref=e2]'''
    compact = compact_snapshot(source)
    assert 'Available tomorrow only' in compact and 'Delivery €25' in compact
    assert 'checkbox "Gift wrap" [checked] [ref=e2]' in compact


def test_product_query_preserves_complete_card_and_excludes_neighbor_price():
    source = ('- list [ref=e1]:\n  - listitem [ref=e2]:\n    - heading "Pita kebab" [ref=e3]\n'
              + ''.join(f'    - text: Ingredient {n}\n' for n in range(12))
              + '    - text: €11\n    - text: Available today\n    - link "Shop A" [ref=e4]\n'
                '  - listitem [ref=e5]:\n    - heading "Pizza" [ref=e6]\n    - text: €3')
    result = reframe(Observation(1, 'https://shop.example.com/search', 'Search', source, text=source), query='Pita')
    assert all(value in result.snapshot for value in ['Pita kebab', '€11', 'Available today', 'Shop A'])
    assert '€3' not in result.snapshot and 'Pizza' not in result.snapshot


def test_scoped_pagination_keeps_reference_and_does_not_restart_full_page():
    source = '- main [ref=e1]:\n  - group "Results" [ref=e2]:\n' + ''.join(
        f'    - button "Product {n} {"description " * 10}" [ref=e{n+3}]\n' for n in range(150))
    result = reframe(Observation(1, 'https://shop.example.com/search', 'Search', source, text=source), ref='e2')
    assert 'truncated' in result.snapshot
    assert 'ref="e2", refresh=false' in result.snapshot


def test_history_keeps_comparison_evidence_without_stale_controls(deps):
    messages = [ModelRequest(parts=[ToolReturnPart('inspect', '[obs 1] https://shop.example.com/search\n'
                '- button "Pita €11" [ref=e1]\n… (truncated; inspect(query="", offset=20, ref="e2") for more)')]),
                ModelRequest(parts=[ToolReturnPart('inspect', '[obs 2] https://shop.example.com/search\n'
                '- button "Pita €14" [ref=e3]')])]
    output = trim_history(SimpleNamespace(deps=deps), messages)
    earlier, current = [message.parts[0].content for message in output]
    assert 'Pita €11' in earlier and 'evidence only, not current controls' in earlier
    assert '[ref=' not in earlier and 'ref="e2"' not in earlier
    assert 'Pita €14' in current and '[ref=e3]' in current
    assert '[ref=e1]' in messages[0].parts[0].content


def test_first_inspection_after_search_fill_refreshes_delayed_results(deps):
    async def flow():
        await deps.perform('fill', fields=[FillField(ref='e3', value='pita')])
        deps.page.text = 'New search results: Pita €11'
        result = await deps.perform('inspect', refresh=False, query='New search results')
        assert 'Pita €11' in result
        assert deps.debug['browser']['last_step']['cached_observation'] is False
        deps.page.aria_snapshot = AsyncMock(side_effect=AssertionError('Pagination must reuse this observation'))
        result = await deps.perform('inspect', refresh=False, query='Pita')
        assert 'no page refresh' in result and 'Pita €11' in deps.observation.snapshot
    asyncio.run(flow())


def test_repeated_reads_get_guidance_then_stop_instead_of_consuming_all_steps(deps):
    async def flow():
        for _ in range(3):
            result = await deps.perform('inspect')
        assert 'already been read repeatedly' in result and 'Do not restart' in result
        await deps.perform('inspect')
        with pytest.raises(BrowserStop, match='repeatedly read'):
            await deps.perform('inspect')
    asyncio.run(flow())


def test_new_evidence_is_not_blocked_by_read_repetition(deps):
    async def flow():
        for number in range(8):
            deps.page.text = f'Current price €{number}'
            result = await deps.perform('inspect')
            assert f'Current price €{number}' in result
            assert deps.debug['browser']['last_step']['repeated_reads'] == 1
    asyncio.run(flow())


def test_control_checks_never_replace_the_page_reference_map(deps):
    deps.page.aria_snapshot = AsyncMock(side_effect=AssertionError('Control checks must not replace refs'))
    deps.page.controls['e1'].aria_snapshot = AsyncMock(side_effect=AssertionError('Subtree snapshot destroys sibling refs'))
    control = asyncio.run(deps.control(deps.page.controls['e1']))
    assert control['role'] == 'button' and control['name'] == 'Place order'


def test_unknown_search_parameters_cannot_reset_a_working_flow(deps):
    async def flow():
        await deps.perform('open', url='https://shop.example.com/search?q=pita')
        result = await deps.perform('open', url='https://shop.example.com/search?q=pita&sort=price_asc')
        assert 'do not invent paths or query parameters' in result
        assert deps.page.url == 'https://shop.example.com/search?q=pita'
        deps.page.text += '\n- link "More results":\n  - /url: /search?q=pita&page=2'
        await deps.observe()
        await deps.perform('open', url='https://shop.example.com/search?page=2&q=pita')
        assert deps.page.url == 'https://shop.example.com/search?page=2&q=pita'
    asyncio.run(flow())


def test_user_provided_navigation_remains_available(deps):
    async def flow():
        await deps.perform('open', url='https://shop.example.com/')
        deps.remember_urls(request_urls('Open https://shop.example.com/special?q=1 now.'))
        await deps.perform('open', url='https://shop.example.com/special?q=1')
        assert deps.page.url == 'https://shop.example.com/special?q=1'
    asyncio.run(flow())


def test_observed_relative_and_quoted_links_preserve_the_exact_query():
    links = list(page_urls('- link "Hotel":\n  - /url: "/hotel?guests=1&room=2"\n'
                           '- link "Next":\n  - /url: \'/search?q=men\'\'s\'', 'https://shop.example.com/'))
    assert links == ['https://shop.example.com/hotel?guests=1&room=2', "https://shop.example.com/search?q=men's"]
    assert navigation_key(links[0]) == navigation_key('https://shop.example.com/hotel?room=2&guests=1#availability')


def test_checkout_review_button_is_not_mistaken_for_a_form_submission(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                await route.fulfill(content_type='text/html', body='<button onclick="this.textContent=\'Hotel Matts\'">View address</button>')
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com/checkout')
            ref = re.search(r'button "View address"[^\n]*\[ref=(e\d+)\]', deps.observation.text)[1]
            await deps.perform('click', ref=ref)
            assert await page.get_by_role('button', name='Hotel Matts').is_visible()
            assert store.actions('owner', run_id) == []
    asyncio.run(flow())


def test_quantity_changes_are_progress_even_without_a_spinbutton_role(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            await page.set_content('<span id="quantity">1</span><button onclick="quantity.textContent=Number(quantity.textContent)+1">Increase guests</button>')
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.observe()
            for _ in range(4):
                ref = re.search(r'button "Increase guests"[^\n]*\[ref=(e\d+)\]', deps.observation.text)[1]
                await deps.perform('click', ref=ref)
            assert await page.locator('#quantity').inner_text() == '5'
    asyncio.run(flow())


@pytest.mark.parametrize('markup', ['<input type="submit" value="Place order">',
                                  '<button><img alt="Place order"></button>'])
def test_native_and_image_control_names_keep_final_action_guard(chromium_available, tmp_path, markup):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            await page.set_content(markup)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            deps.final_submit = AsyncMock(return_value='approval required')
            await deps.observe()
            ref = re.search(r'button\b[^\n]*\[ref=(e\d+)\]', deps.observation.text)[1]
            control = await deps.control(deps.locator(ref))
            assert control['name'] == 'Place order'
            await deps.perform('click', ref=ref)
            deps.final_submit.assert_awaited_once()
    asyncio.run(flow())


def test_new_tab_becomes_active_and_back_restores_search_and_filters(chromium_available, tmp_path):
    store, run_id, settings = fixture_store(tmp_path, chromium_available)
    async def flow():
        async with open_browser(settings) as (page, _):
            async def website(route):
                body = ('<h1>Available room €55</h1>' if route.request.url.endswith('/hotel') else
                        '<input value="Tallinn"><input type="checkbox" checked aria-label="Hotels">'
                        '<a href="/hotel" target="_blank">Hotel details</a>')
                await route.fulfill(content_type='text/html; charset=utf-8', body=body)
            await page.context.route('https://browser.example.com/**', website)
            deps = BrowserDeps(page, store, 'owner', run_id, settings)
            await deps.perform('open', url='https://browser.example.com/search')
            ref = re.search(r'link "Hotel details"[^\n]*\[ref=(e\d+)\]', deps.observation.text)[1]
            result = await deps.perform('click', ref=ref)
            assert deps.page is not page and 'Available room €55' in result
            assert deps.page.url.endswith('/hotel')
            warm = WarmBrowser(asyncio.get_running_loop(), 'test')
            warm.park(deps)
            active, _, reused = await warm.acquire(settings, AsyncMock(side_effect=AssertionError('Keep the open tab')))
            assert active is deps.page and reused
            followup = BrowserDeps(active, store, 'owner', run_id, settings)
            warm.attach(followup)
            assert followup.navigation_urls == deps.navigation_urls
            deps = followup
            result = await deps.perform('back')
            assert deps.page is page and 'Tallinn' in result
            assert await page.get_by_role('checkbox', name='Hotels').is_checked()
    asyncio.run(flow())
