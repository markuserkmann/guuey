import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from playwright.async_api import Error, TimeoutError as PlaywrightTimeout

from app.browser.observe import observe, reframe, unreadable_frames
from app.browser.schemas import FormChoice
from browser_fakes import FakeControl
from test_browser_actions import approved_action, deps as deps


def test_cached_inspection_reads_whole_date_group_without_browser_calls(deps):
    text = ('- group "Tuesday" [ref=f21e4]:\n'
            + '\n'.join(f'  - button "{hour}:00" [ref=f21e{hour}]' for hour in range(10, 19))
            + '\n- group "Wednesday" [ref=f21e20]:\n  - button "09:00" [ref=f21e21]')
    deps.page.aria_snapshot = AsyncMock(return_value=text)

    async def flow():
        await deps.observe()
        deps.page.aria_snapshot = AsyncMock(side_effect=AssertionError('Unexpected browser read'))
        deps.page.title = AsyncMock(side_effect=AssertionError('Unexpected browser read'))
        deps.dismiss_cookies = AsyncMock(side_effect=AssertionError('Unexpected browser read'))
        result = await deps.perform('inspect', ref='f21e4', refresh=False)
        assert 'no page refresh' in result
        assert '10:00' in result and '18:00' in result
        assert 'Wednesday' not in result and '09:00' not in result
        assert deps.observation.text == text
        assert deps.debug['browser']['last_step']['cached_observation'] is True
        result = await deps.perform('inspect', query='Wednesday', refresh=False)
        assert '09:00' in result

    asyncio.run(flow())


@pytest.mark.parametrize('navigation', [False, True])
def test_live_inspection_refreshes_dynamic_slots_and_cache_cannot_cross_urls(deps, navigation):
    async def flow():
        deps.page.text = 'Available: 10:00'
        await deps.observe()
        deps.page.text = 'Available: 14:00'
        if navigation:
            deps.page.url = 'https://shop.example.com/other-calendar'
        args = {'refresh': False} if navigation else {}
        result = await deps.perform('inspect', query='Available', **args)
        assert '14:00' in result and '10:00' not in result
        assert deps.debug['browser']['last_step']['cached_observation'] is False

    asyncio.run(flow())


def test_cached_subtree_preserves_foreground_and_missing_ref_guidance(deps):
    deps.page.aria_snapshot = AsyncMock(return_value=(
        '- group "Calendar" [ref=e4]:\n  - button "10:00" [ref=e5]\n'
        '- dialog "Choose service" [ref=f21e6]:\n  - button "Basic" [ref=f21e7]'))

    async def flow():
        initial = await observe(deps.page)
        focused = reframe(initial, ref='e4')
        assert focused.blocks_background
        assert 'Choose service' in focused.snapshot and '10:00' in focused.snapshot
        missing = reframe(focused, ref='f21e99')
        assert 'absent' in missing.snapshot and 'refresh=true' in missing.snapshot
        assert missing.text == initial.text

    asyncio.run(flow())


def test_scoped_empty_date_includes_parent_refs_without_adjacent_slots(deps):
    deps.page.aria_snapshot = AsyncMock(return_value=(
        '- table [ref=f4e1]:\n  - row [ref=f4e2]:\n'
        '    - cell "Tuesday 17 November" [ref=f4e3]\n'
        '    - cell "Wednesday 18 November" [ref=f4e4]:\n      - button "10:00" [ref=f4e5]'))

    async def flow():
        initial = await observe(deps.page)
        focused = reframe(initial, ref='f4e3')
        assert 'table [ref=f4e1]' in focused.snapshot
        assert 'row [ref=f4e2]' in focused.snapshot
        assert '1 lines before excerpt limits' in focused.snapshot
        assert 'Wednesday' not in focused.snapshot and '10:00' not in focused.snapshot

    asyncio.run(flow())


@pytest.mark.parametrize('tool', ['click', 'select'])
def test_successful_choice_is_recorded_and_user_correction_replaces_it(deps, tool):
    deps.page.controls['e4'] = FakeControl(deps.page, 'Basic service',
                                          role='combobox' if tool == 'select' else 'button')
    args = {'option': 'Basic'} if tool == 'select' else {}

    async def flow():
        result = await deps.perform(tool, ref='e4', choice=FormChoice(field='service', value='Basic'), **args)
        assert 'Choice recorded (assumed)' in result
        assert deps.assumptions == [{'site': 'example.com', 'field': 'service', 'value': 'Basic'}]
        result = await deps.perform(tool, ref='e4', choice=FormChoice(field='service', value='Basic', source='user'), **args)
        assert 'Choice recorded (user)' in result
        assert deps.assumptions == []

    asyncio.run(flow())


def test_failed_choice_is_not_reported_as_selected(deps):
    deps.page.controls['e4'] = FakeControl(deps.page, 'Basic service')
    deps.page.controls['e4'].enabled = False
    result = asyncio.run(deps.perform('click', ref='e4', choice=FormChoice(field='service', value='Basic')))
    assert 'visible and enabled' in result
    assert deps.assumptions == []


def test_assumed_choice_cannot_bypass_final_action_approval(deps):
    asyncio.run(approved_action(deps, 'click', ref='e1', choice=FormChoice(field='service', value='Basic')))
    assert deps.page.clicks == 1
    assert deps.store.actions(deps.owner, deps.run_id)[0]['status'] == 'submitted'


def test_changing_choice_metadata_cannot_bypass_repeat_detection(deps):
    deps.page.controls['e4'] = FakeControl(deps.page, 'Basic service')

    async def flow():
        await deps.observe()
        for value in ('Basic', 'Basic service', 'Default service'):
            result = await deps.perform('click', ref='e4', choice=FormChoice(field='service', value=value))
        assert 'already attempted twice' in result
        assert deps.page.clicks == 2
        assert deps.assumptions[0]['value'] == 'Basic service'

    asyncio.run(flow())


def test_text_wait_ignores_detached_frames_but_requires_visible_text(deps):
    def frame(result):
        return SimpleNamespace(get_by_text=lambda *args, **kwargs: SimpleNamespace(
            first=SimpleNamespace(is_visible=AsyncMock(**result))))

    deps.page.frames = [frame({'side_effect': Error('Frame was detached')}),
                        frame({'return_value': False})]
    with pytest.raises(PlaywrightTimeout, match='did not appear'):
        asyncio.run(deps._wait_for_text('Calendar', seconds=0.05))
    deps.page.frames.append(frame({'return_value': True}))
    asyncio.run(deps._wait_for_text('Calendar', seconds=0.05))


@pytest.mark.parametrize('body,pending', [
    ('', True), ('  - generic [ref=f1e1]: ', True),
    ('  - status [ref=f1e1]: Loading…', True),
    ('  - button "选择服务" [ref=f1e1]', False),
    ('  - generic [ref=f1e1]: Vali teenus', False),
])
def test_embedded_form_readiness_uses_content_not_frame_reference(body, pending):
    snapshot = '- iframe [ref=e1]:\n' + body + '\n- button "Footer link" [ref=e2]'
    assert unreadable_frames(snapshot) == (['e1'] if pending else [])


def test_navigation_observation_waits_for_form_and_keeps_latest_refs(deps, monkeypatch):
    deps.page.aria_snapshot = AsyncMock(side_effect=[
        '- iframe [ref=e1]:\n  - generic [ref=f1e1]: ',
        '- iframe [ref=e1]:\n  - button "Choose service" [ref=f1e3]',
    ])
    monkeypatch.setattr('app.browser.observe.asyncio.sleep', AsyncMock())
    result = asyncio.run(observe(deps.page, wait_for_frames=True))
    assert 'Choose service' in result.snapshot and 'f1e3' in result.snapshot
    assert 'f1e1' not in result.text
    assert deps.page.aria_snapshot.await_count == 2
