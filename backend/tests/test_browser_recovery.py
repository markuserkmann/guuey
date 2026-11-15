import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError

from app.browser.actions import BrowserStop, interaction_error
from app.browser.observe import Observation, foreground_text, observe
from app.browser.progress import BrowserProgress
from test_browser_actions import deps as deps, pending_request


@pytest.mark.parametrize('message,expected', [
    ('element is visible, enabled and stable\noverlay intercepts pointer events', 'blocked_by_overlay'),
    ('waiting for locator("aria-ref=e4")\nelement is visible, enabled and stable', 'timeout'),
    ('element is not visible', 'not_visible'),
    ('element is detached', 'stale_ref'),
    ('strict mode violation', 'ambiguous'),
])
def test_specific_interaction_failures(message, expected):
    assert interaction_error(TimeoutError(message))[0] == expected


def test_address_dialog_and_portalled_suggestions_survive_large_catalog(deps):
    deps.page.text = '\n'.join('- generic: Catalog item ' + str(i) for i in range(14000)) + '''
- dialog "Delivery address" [ref=e30]:
  - textbox "Address" [ref=e31]: Matts Hotel
  - button "Confirm address" [ref=e32]
- listbox [ref=e33]:
  - option "Matts Hotel, Espoo" [ref=e34]
'''
    observation = asyncio.run(observe(deps.page))
    assert 'ref=e31' in observation.snapshot[:1000]
    assert 'ref=e34' in observation.snapshot[:1000]
    assert len(observation.snapshot) < 12500
    assert len(observation.text) > 300000
    assert 'Catalog item' not in foreground_text(observation.text)


@pytest.mark.parametrize('prefix', ['', 'f21', 'f999'])
def test_timer_does_not_hide_repeated_action(prefix):
    progress = BrowserProgress()
    keys = []
    for i in range(8):
        obs = Observation(i, 'https://example.com', '', '', text=f'''
- button "Choose address" [ref={prefix}e{i + 1}]
- generic: Countdown {i}
''')
        key = progress.key('click', {'ref': f'{prefix}e{i + 1}'}, obs)
        keys.append(key)
        progress.record(key, True)
    assert len(set(keys)) == 1
    assert progress.exhausted
    assert progress.repeats(keys[-1])


def test_alternating_navigation_is_bounded():
    progress = BrowserProgress()
    for i in range(6):
        source, target = ('a', 'b') if i % 2 else ('b', 'a')
        obs = Observation(i, 'https://example.com/' + source, '', '', text=f'Timer {i}')
        key = progress.key('open', {'url': 'https://example.com/' + target}, obs)
        if i >= 4:
            assert progress.repeats(key)
        progress.record(key, True, unsuccessful=False)


def test_successful_dropdown_cycles_are_bounded():
    progress = BrowserProgress()
    for cycle in range(3):
        for expanded in (False, True):
            ref = f'e{cycle * 2 + 1}'
            text = (f'- combobox "Track" [ref={ref}]:\n  - generic: Abja-Paluoja (2 km)'
                    if not expanded else
                    f'- listbox:\n  - option "Abja-Paluoja (2 km)" [selected] [ref={ref}]')
            obs = Observation(cycle, 'https://example.com', '', '', text=text)
            key = progress.key('click', {'ref': ref}, obs)
            assert progress.repeats(key) == (cycle == 2)
            progress.record(key, False)


def test_changed_form_values_allow_further_actions():
    progress = BrowserProgress()
    for value in ('', 'Chris', 'Chris Smith'):
        obs = Observation(1, 'https://example.com', '', '', text=f'''
- textbox "Name" [ref=e1]: {value}
- button "Next" [ref=e2]
''')
        key = progress.key('click', {'ref': 'e2'}, obs)
        assert not progress.repeats(key)
        progress.record(key, False)


def test_inspection_preserves_failed_action_history(deps):
    deps.progress.record('blocked', True)
    asyncio.run(deps.perform('inspect', query='Email'))
    assert list(deps.progress.outcomes) == [True]


def test_overlay_diagnostics_preserve_reason_and_scrub_secrets(deps):
    deps.secret_values.append('private-session')
    deps.page.controls['e3'].click_error = TimeoutError(
        'element is visible\n<div private-session> intercepts pointer events')
    output = asyncio.run(deps.perform('click', ref='e3'))
    assert 'blocked_by_overlay' in output
    diagnostic = deps.debug['browser']['step_diagnostics'][0]
    assert diagnostic['target']['name'] == 'Email'
    assert 'intercepts pointer events' in diagnostic['detail']
    assert 'private-session' not in str(deps.debug)


def test_recovery_requests_takeover_once_and_rechecks_page(deps):
    deps.store.set_view(deps.owner, deps.run_id, 'https://hb.oxylabs.io/novnc/?id=test')
    deps._request_takeover = AsyncMock()
    async def flow():
        output = await deps.recover_or_stop('Address selection is blocked.')
        assert 'Inspect the result' in output
        assert deps.observation is not None
        with pytest.raises(BrowserStop, match='Address selection'):
            await deps.recover_or_stop('Address selection is blocked.')
    asyncio.run(flow())
    deps._request_takeover.assert_awaited_once()


def test_recovery_wait_can_be_cancelled(deps):
    deps.settings = replace(deps.settings, session_key='')
    deps.store.set_view(deps.owner, deps.run_id, 'https://hb.oxylabs.io/novnc/?id=test')
    async def flow():
        task = asyncio.create_task(deps.recover_or_stop('Address dialog blocked.'))
        request = await pending_request(deps)
        assert request['kind'] == 'takeover'
        deps.store.runs.finish(deps.run_id, 'cancelled', 'cancelled')
        with pytest.raises(BrowserStop):
            await task
    asyncio.run(flow())
