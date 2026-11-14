import asyncio
import statistics
import time

from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from app.browser.actions import BrowserDeps, BrowserStop
from app.browser.agent import BrowserReport, build_agent
from app.browser.config import BrowserSettings, BrowserUnavailable
from app.browser.connection import BrowserResources, open_browser
from app.browser.store import BrowserStore, SessionConflict
from app.model_calls import error_details
from app.research_modes import research_deadline
from app.secrets import redact


def execute_browser(run_id, row, message, context, debug, store):
    settings = BrowserSettings.from_env()
    browser_store = BrowserStore(store, settings)
    try:
        browser_store.start(row['owner'], run_id)
    except (SessionConflict, ValueError):
        return
    debug['browser'] = {'backend': settings.backend, 'steps': 0, 'timings': {}, 'outcome': None,
                        'limits': {'deadline_s': settings.deadline_s, 'max_steps': settings.max_steps,
                                   'tool_retries': settings.tool_retries, 'thinking_level': settings.thinking_level,
                                   'model_max_tokens': settings.model_max_tokens, 'model_timeout_s': settings.model_timeout_s}}
    store.checkpoint(run_id, 'browser_starting', debug)
    deadline_token = research_deadline.set(float('inf'))
    try:
        asyncio.run(_execute(run_id, row['owner'], message, context, debug, browser_store))
    finally:
        research_deadline.reset(deadline_token)


async def _execute(run_id, owner, message, context, debug, store):
    settings = store.settings
    started = time.monotonic()
    deps = None
    phase = 'browser_failed'

    def network_error(error):
        errors = debug['browser'].setdefault('network_errors', [])
        if len(errors) < 10:
            errors.append(deps.clean_data(error) if deps else error)

    async def request_guard(request):
        if deps is None:
            return
        try:
            await deps.active()
            if request.is_navigation_request() or request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                deps.check_scope(request.url)
        except BrowserStop:
            raise ValueError('The browser task was stopped') from None

    async def supervise(operation):
        async def monitor():
            while True:
                await deps.active()
                await asyncio.sleep(1)

        work = asyncio.create_task(operation)
        watcher = asyncio.create_task(monitor())
        try:
            done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
            return await work
        finally:
            work.cancel()
            watcher.cancel()
            await asyncio.gather(work, watcher, return_exceptions=True)

    async def run_agent(agent):
        result = await agent.run('Conversation context:\n' + context + '\nCurrent request:\n' + message,
                                 deps=deps, usage_limits=UsageLimits(request_limit=settings.max_steps * 2 + settings.tool_retries,
                                                                   tool_calls_limit=settings.max_steps))
        await deps.active()
        if not deps.pending_action:
            await deps.refresh_session()
        return result

    try:
        agent = build_agent(settings)
        async with asyncio.timeout(settings.deadline_s) as timeout:
            resources = BrowserResources()
            async with open_browser(settings, on_network_error=network_error, request_guard=request_guard,
                                    resources=resources) as (page, live_view):
                debug['browser']['timings']['connect_s'] = round(time.monotonic() - started, 3)
                store.set_view(owner, run_id, live_view)
                deps = BrowserDeps(page, store, owner, run_id, settings, debug=debug, timeout=timeout,
                                   hard_deadline=started + 3540, resources=resources)
                deps.phase('browser_working')
                result = await supervise(run_agent(agent))
                report = result.output
                phase = {'done': 'browser_done', 'needs_user': 'browser_needs_user', 'blocked': 'browser_blocked', 'failed': 'browser_failed'}[report.outcome]
    except BrowserStop as exc:
        report, phase = BrowserReport(outcome=exc.outcome, summary=exc.summary), exc.phase
    except SessionConflict as exc:
        report = BrowserReport(outcome='needs_user', summary=str(exc))
        phase = 'browser_needs_user'
    except TimeoutError:
        report = BrowserReport(outcome='needs_user', summary='The browser task reached its time limit.')
        phase = 'budget_exhausted'
    except UsageLimitExceeded:
        report = BrowserReport(outcome='needs_user', summary='The browser task reached its step limit.')
        phase = 'browser_needs_user'
    except UnexpectedModelBehavior:
        validation = debug['browser'].get('validation_errors', [])
        tool = validation[-1]['tool'] if validation else None
        summary = (f'The browser could not correct the arguments for {tool} after {settings.tool_retries} correction attempts.'
                   if tool else 'The browser model could not produce a valid response within its correction limit.')
        debug['error'] = {'code': 'browser_tool_validation_failed' if tool else 'browser_response_invalid',
                          'type': 'UnexpectedModelBehavior', 'message': summary, 'retryable': False,
                          'retry_after': None, 'status_code': None}
        report = BrowserReport(outcome='needs_user', summary=summary)
        phase = 'browser_needs_user'
    except BrowserUnavailable as exc:
        restricted = '1337' in str(exc) and 'Invalid target' in str(exc)
        report = BrowserReport(outcome='blocked' if restricted else 'failed',
                               summary='This site is restricted on the browser provider.' if restricted else redact(str(exc))[:2000])
        phase = 'browser_blocked' if restricted else 'browser_failed'
    except Exception as exc:
        details = error_details(exc)
        if deps:
            details = deps.clean_data(details)
        debug['error'] = details
        report = BrowserReport(outcome='failed', summary=details['message'] if isinstance(exc, ModelAPIError) else 'The browser task could not be completed.')
    finally:
        if deps and deps.lease:
            store.release_session(deps.lease)
    if deps and deps.pending_action:
        report = BrowserReport(outcome='failed', summary=deps.unknown_submission())
        phase = 'submission_unknown'
        action = next(item for item in store.actions(owner, run_id) if item['id'] == deps.pending_action['id'])
        if store.runtime(owner, run_id)['status'] == 'running' and action['status'] == 'submitted':
            store.update_action(owner, run_id, action['id'], 'unverified')
    if store.runtime(owner, run_id)['status'] != 'running':
        if deps:
            deps.secrets.clear()
            deps.secret_values.clear()
        return
    browser_debug = debug['browser']
    browser_debug['outcome'] = report.outcome
    if deps:
        browser_debug['steps'] = len(deps.step_seconds)
        browser_debug['timings']['median_step_s'] = round(statistics.median(deps.step_seconds), 3) if deps.step_seconds else None
        browser_debug['timings']['first_observation_s'] = round(deps.first_observation_at - started, 3) if deps.first_observation_at else None
    result = {'reply': report.summary, 'sources': [source.model_dump() for source in report.sources],
              'browser': {'outcome': report.outcome, 'actions': store.actions(owner, run_id)}}
    if deps:
        result = deps.clean_data(result)
        debug = deps.clean_data(debug)
        deps.secrets.clear()
        deps.secret_values.clear()
    store.runs.checkpoint(run_id, phase, debug, result)
    store.runs.finish(run_id, 'completed' if report.outcome == 'done' else 'partial', phase)
