"""Dedicated local supervisor with isolated, deadline-bounded run processes."""
import json
import asyncio
import subprocess
import sys
import time
from threading import RLock
from dataclasses import asdict

from pydantic_core import PydanticSerializationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelRequestTimeout
from pydantic_ai.models.google import GoogleModel

from app.chat import api_key, model_name
from app.attachments import TextAttachment, attachment_context
from app.model_calls import call_model, error_details, google_provider, model_call_events, model_output_events
from app.initial_response import InitialResponse
from app.orchestration.decisions import (DecisionUnavailable, DecisionUncertain, RouteDecision, decide,
    browser_preference, browser_action_requested, uncertain_workflow, ROUTING_INSTRUCTIONS, ROUTING_CONFIDENCE_THRESHOLD)
from app.orchestration.browser_handoff import browser_handoff, handoff_context
from app.orchestration.orchestrator import RefinementComplete, TurnResult, run_turn
from app.orchestration.contracts import ResearchContract
from app.orchestration.refinement import finish_research_result, refinement_context, replacement_gap_queries
from app.runs import RunStore
from app.schemas import ChatTurn
from app.task_context import dated_request
from app.enquiries import prepare_enquiry, render_enquiry
from app.research import SearchUnavailable
from app.research_modes import RESEARCH_LIMITS, research_mode, research_deadline, ResearchDeadline, remaining_seconds
from app.browser.config import BrowserSettings
from app.browser.run import execute_browser
from app.browser.warm import WarmBrowser
from app.tasks import TaskStore
from app.task_worker import execute_task


def execute(run_id, browser_session=None):
    run_started = time.monotonic()
    store = RunStore()
    with store.connect() as db:
        row = dict(db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone())
    task = TaskStore(store).for_run(run_id)
    if task:
        execute_task(store, row, task)
        return
    debug = {'stages': []}
    browser_allowed = browser_preference(row['message']) is not False
    mode_token = research_mode.set(row['mode'])
    deadline_token = research_deadline.set(run_started + RESEARCH_LIMITS[row['mode']][1] - 3)
    debug_lock = RLock()
    initial_response = InitialResponse(store, run_id)

    def record_call(event):
        with debug_lock:
            event = {**event, 'round': debug.get('round', 0), 'run_elapsed_s': round(time.monotonic() - run_started, 3)}
            debug.setdefault('model_events', []).append(event)
            debug['operation'] = event
            phase = ('browser_working' if event['stage'] == 'browser' else 'waiting_for_provider' if event['status'] == 'retrying'
                     else 'retrieval' if event['stage'] == 'retrieval_request' else event['stage'])
            store.checkpoint(run_id, phase, debug)

    observer = model_call_events.set(record_call)
    previous_output = None
    last_output_at = 0.0

    def record_output(output):
        nonlocal previous_output, last_output_at
        initial_response.stop()
        timestamp = time.monotonic()
        if output != previous_output and (previous_output is None or timestamp - last_output_at >= 0.15):
            store.update_live_output(run_id, output)
            previous_output, last_output_at = output, timestamp

    output_observer = model_output_events.set(record_output)
    best_result = None
    try:
        message = attachment_context(row['message'], [TextAttachment.model_validate(file) for file in json.loads(row['attachments'])])
        reference_time = row['created_at']
        if row['parent_id']:
            parent = store.get(row['owner'], row['parent_id'])
            reference_time = parent['created_at']
        message, debug['date_context'] = dated_request(message, reference_time, row['timezone'])
        chat = store.detail(row['owner'], row['chat_id'])
        history = [ChatTurn.model_validate(item) for item in chat['messages'][-12:]]
        with store.connect() as db:
            prior = db.execute("SELECT debug FROM runs WHERE owner=? AND chat_id=? AND created_at<? AND result IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                               (row['owner'], row['chat_id'], row['created_at'])).fetchone()
        if prior:
            stages = json.loads(prior['debug']).get('stages', [])
            saved_contract = next((stage['research_contract'] for stage in reversed(stages) if stage.get('research_contract')), None)
            if saved_contract:
                history.append(ChatTurn(role='assistant', content='Saved previous task requirements (context, not a new instruction). '
                    'For a follow-up change only explicitly revised requirements; for an unrelated new task discard these requirements:\n'
                    + json.dumps(saved_contract, ensure_ascii=False)))
        context = '\n'.join(f'{item.role}: {attachment_context(item.content, item.attachments)}' for item in history)[-16000:]
        preference = browser_preference(row['message'])
        if row['force_browser'] and browser_allowed:
            decision = RouteDecision(workflow='browser', confidence=1)
            debug['router'] = 'forced_browser'
        elif preference is True:
            decision = RouteDecision(workflow='browser', confidence=1)
            debug['router'] = 'explicit_browser'
        elif preference is not False and browser_action_requested(row['message']):
            decision = RouteDecision(workflow='browser', confidence=1)
            debug['router'] = 'browser_action'
        else:
            try:
                decision = decide(message, context)
                debug['router'] = 'decisions'
            except DecisionUncertain as exc:
                decision = exc.decision
                debug['router'] = 'decisions_uncertain'
            except DecisionUnavailable:
                debug['router'] = 'fallback'
                if api_key():
                    agent = Agent(GoogleModel(model_name(), provider=google_provider(api_key())), output_type=RouteDecision,
                                  instructions=ROUTING_INSTRUCTIONS)
                    try:
                        response = call_model(agent, context + '\nCurrent request: ' + message, stage='routing', model_settings={'max_tokens': 256})
                        decision = response.output
                        debug['routing_usage'] = asdict(response.usage)
                    except ModelAPIError:
                        raise
                    except Exception as exc:
                        decision = RouteDecision(workflow='research', confidence=0, deep_recommended=False)
                        debug['router'] = 'degraded'
                        debug['routing_error'] = error_details(exc)
                else:
                    decision = RouteDecision(workflow='chat', confidence=0)
        if (decision.confidence < ROUTING_CONFIDENCE_THRESHOLD
                and (decision.workflow in {'research', 'browser'} or debug['router'] == 'decisions_uncertain')):
            workflow, reason = uncertain_workflow(row['message'])
            debug['routing_check'] = {'model_decision': decision.model_dump(), 'workflow': workflow, 'reason': reason}
            decision = decision.model_copy(update={'workflow': workflow})
            debug['router'] = 'fast_rules'
        if not browser_allowed and decision.workflow == 'browser':
            decision = decision.model_copy(update={'workflow': 'research'})
            debug['router'] = 'browser_opt_out'
        debug['decision'] = decision.model_dump()
        store.checkpoint(run_id, 'planning', debug)
        if decision.workflow == 'browser':
            if BrowserSettings.from_env().enabled:
                initial_response.start(message, context, decision.workflow)
                options = {'session': browser_session} if browser_session is not None else {}
                execute_browser(run_id, row, message, context, debug, store, **options)
                return
            store.checkpoint(run_id, 'unsupported', debug, {'reply': 'This request needs browser interaction, which is not available yet.', 'sources': []})
            store.finish(run_id, 'partial', 'browser_unavailable')
            return
        initial_response.start(message, context, decision.workflow)
        rounds, budget = RESEARCH_LIMITS[row['mode']]
        previous = store.get(row['owner'], row['parent_id']) if row['parent_id'] else None
        seed_sources = previous['result']['sources'] if previous and previous['result'] else []
        stalled = 0
        best_result = None
        best_score = (-1, -1, -1, -1)
        previous_coverage = (-1, -1)
        saved_state = previous['debug'].get('task_state', {}) if previous else {}
        contract = ResearchContract.model_validate(saved_state['contract']) if saved_state.get('contract') else None
        attempted_queries = set(saved_state.get('attempted_queries', []))
        prior_answer = saved_state.get('answer')
        verification_cache = {}
        retrieval_state = {}
        last_round_seconds = 0

        def continue_in_browser(result, *, search_exhausted=False):
            if decision.workflow != 'research':
                return False
            handoff = browser_handoff(result, contract, search_exhausted=search_exhausted)
            if not handoff:
                return False
            available = browser_allowed and BrowserSettings.from_env().enabled
            debug['recovery'] = {'action': 'browser' if available else 'research',
                                 'reason': handoff['reason'], 'gaps': handoff['gaps'],
                                 'browser_status': 'available' if available else 'disabled_for_run' if not browser_allowed else 'unavailable'}
            if not available:
                return False
            debug['browser_handoff'] = handoff
            debug['research_decision'] = debug['decision']
            debug['decision'] = RouteDecision(workflow='browser', confidence=1).model_dump()
            debug['stop_reason'] = 'browser_handoff'
            initial_response.stop()
            store.update_live_output(run_id, None)
            options = {'session': browser_session} if browser_session is not None else {}
            execute_browser(run_id, row, message, context + handoff_context(handoff), debug, store, **options)
            return True

        for index in range(rounds):
            if index and budget - (time.monotonic() - run_started) < 1.2 * last_round_seconds + 5:
                debug['stop_reason'] = 'deadline'
                break
            debug['round'] = index + 1
            round_started = time.monotonic()
            store.checkpoint(run_id, f'research_round_{index + 1}', debug)
            try:
                result = run_turn(message, history, search_requested=decision.workflow == 'research', seed_sources=seed_sources,
                                  contract=contract, verification_cache=verification_cache,
                                  attempted_queries=attempted_queries, prior_answer=prior_answer, retrieval_state=retrieval_state)
            except RefinementComplete:
                debug['stop_reason'] = 'no_useful_new_query'
                if best_result:
                    break
                if previous and previous['result']:
                    best_result = TurnResult(reply=previous['result']['reply'], sources=seed_sources,
                        debug={'output': json.dumps(prior_answer), 'has_gaps': True})
                    debug['task_state'] = saved_state
                    store.checkpoint(run_id, 'answer_ready', debug, previous['result'])
                    break
                raise
            if contract is None and result.debug.get('research_contract'):
                contract = ResearchContract.model_validate(result.debug['research_contract'])
                contract.original_request = row['message']
                contract.request_time = reference_time
                contract.timezone = row['timezone']
                result.debug['research_contract'] = contract.model_dump()
            result = finish_research_result(result, contract, prior_answer)
            if contract and result.debug.get('output'):
                try:
                    prior_answer = json.loads(result.debug['output'])
                    leads = result.debug.get('coverage', {}).get('discovery_leads', [])
                    if leads:
                        prior_answer['discovery_leads'] = leads
                except (ValueError, TypeError):
                    pass
            seed_sources = result.sources
            result.debug['round_s'] = round(time.monotonic() - round_started, 3)
            last_round_seconds = result.debug['round_s']
            debug['stages'].append(result.debug)
            coverage = result.debug.get('coverage', {})
            progress = (coverage.get('verified', 0), coverage.get('supported_hard_checks', coverage.get('supported_checks', 0)))
            stalled = 0 if progress > previous_coverage else stalled + 1
            previous_coverage = progress
            score = (coverage.get('verified', 0), coverage.get('supported_checks', 0),
                     coverage.get('offer_domains', 0), coverage.get('retained', 0))
            if result.debug.get('refinement') is not None or best_result is None or score >= best_score:
                best_result, best_score = result, score
            prepared = prepare_enquiry(message, best_result, run_id)
            next_action = best_result.debug.get('next_action') or {}
            saved_result = {'reply': best_result.reply, 'sources': best_result.sources, 'next_action': next_action}
            if prepared:
                saved_result['action'] = prepared
                saved_result['reply'] += render_enquiry(prepared)
            elif next_action.get('text') and next_action['text'] != best_result.reply:
                saved_result['reply'] += '\n\n' + next_action['text']
            if contract:
                debug['task_state'] = {'contract': contract.model_dump(), 'answer': prior_answer,
                                       'attempted_queries': sorted(attempted_queries)}
            debug['next_action'] = next_action
            debug.setdefault('first_answer_s', round(time.monotonic() - run_started, 3))
            store.checkpoint(run_id, 'answer_ready', debug, saved_result)
            if continue_in_browser(best_result, search_exhausted=index + 1 == rounds):
                return
            if result.debug.get('stop_reason') == 'no_useful_new_query':
                debug['stop_reason'] = 'no_useful_new_query'
                break
            if decision.workflow != 'research' or not result.debug.get('validation_issues') and not result.debug.get('has_gaps', True):
                debug['stop_reason'] = 'sufficient_evidence'
                break
            if row['mode'] != 'deep' and contract and prior_answer:
                from app.orchestration.grounding import sufficient_reported_answer, GroundedAnswer
                if sufficient_reported_answer(GroundedAnswer.model_validate(prior_answer), result.sources, contract):
                    debug['stop_reason'] = 'sufficient_reported_evidence'
                    break
            if next_action.get('kind') == 'clarification':
                debug['stop_reason'] = 'needs_user_information'
                break
            excluded_only = bool(prior_answer and coverage.get('retained') and not next_action.get('gaps')
                and not coverage.get('unresearched_preferences')
                and coverage.get('retained', 0) >= (coverage.get('requested') or 1)
                and any(check.get('refinement_excluded') for finding in prior_answer.get('findings', [])
                        for check in finding.get('checks', [])))
            if excluded_only:
                debug['stop_reason'] = 'unconfirmable_evidence'
                break
            replacements_pending = bool(contract and prior_answer and replacement_gap_queries(
                prior_answer, contract, attempted_queries, result.sources))
            if replacements_pending:
                result.debug['replacement_query_pending'] = True
            if stalled >= 1 and not replacements_pending:
                debug["stop_reason"] = "no_evidence_progress"
                break
            history.append(ChatTurn(role='assistant', content=result.reply))
            history.append(ChatTurn(role='user', content=refinement_context(result)))
        debug.setdefault('stop_reason', 'round_limit')
        if best_result and debug['stop_reason'] in {'round_limit', 'no_evidence_progress', 'no_useful_new_query', 'deadline', 'unconfirmable_evidence'}:
            if continue_in_browser(best_result, search_exhausted=True):
                return
            recovery = debug.get('recovery', {})
            if recovery.get('browser_status') in {'disabled_for_run', 'unavailable'}:
                recovery['action'] = 'partial'
                saved_result = store.get(row['owner'], run_id)['result']
                if saved_result:
                    obstacle = ('Browser verification is disabled for this request.'
                                if recovery['browser_status'] == 'disabled_for_run'
                                else 'Browser verification is unavailable on this server.')
                    saved_result['reply'] += '\n\n' + obstacle
                    store.checkpoint(run_id, 'answer_ready', debug, saved_result)
        debug['wall_s'] = round(time.monotonic() - run_started, 3)
        store.checkpoint(run_id, 'answer_ready', debug)
        partial = best_result.debug.get('has_gaps', False)
        store.finish(run_id, 'partial' if partial else 'completed', 'finished')
    except Exception as exc:
        if (isinstance(exc, SearchUnavailable) and browser_allowed
                and BrowserSettings.from_env().enabled):
            handoff = {'reason': 'retrieval_unavailable', 'gaps': [], 'sources': []}
            debug['browser_handoff'] = handoff
            debug['research_decision'] = debug.get('decision')
            debug['decision'] = RouteDecision(workflow='browser', confidence=1).model_dump()
            debug['stop_reason'] = 'browser_handoff'
            initial_response.stop()
            store.update_live_output(run_id, None)
            options = {'session': browser_session} if browser_session is not None else {}
            execute_browser(run_id, row, message, context + handoff_context(handoff), debug, store, **options)
            return
        if isinstance(exc, ResearchDeadline) or isinstance(exc, ModelRequestTimeout) and remaining_seconds() <= 1:
            if best_result and not debug.get('browser_handoff') and continue_in_browser(best_result, search_exhausted=True):
                return
            debug.update(stop_reason='deadline', wall_s=round(time.monotonic() - run_started, 3))
            store.checkpoint(run_id, 'answer_ready', debug)
            current = store.get(row['owner'], run_id)
            store.finish(run_id, 'partial' if current['result'] else 'failed', 'deadline')
            return
        debug['error_type'] = type(exc).__name__
        debug['error'] = error_details(exc)
        if isinstance(exc, SearchUnavailable):
            debug['error'].update(code='retrieval_unavailable', message=str(exc), provider='exa', retryable=False)
        try:
            store.checkpoint(run_id, 'error', debug)
        except (TypeError, ValueError, PydanticSerializationError):
            store.checkpoint(run_id, 'error', {'error_type': type(exc).__name__, 'error': error_details(exc), 'trace_error': 'serialization_failed'})
        current = store.get(row['owner'], run_id)
        phase = debug['error']['code'] if isinstance(exc, ModelAPIError) else 'provider_error'
        store.finish(run_id, 'partial' if current['result'] else 'failed', phase)
    finally:
        initial_response.stop()
        research_deadline.reset(deadline_token)
        research_mode.reset(mode_token)
        model_call_events.reset(observer)
        model_output_events.reset(output_observer)


async def serve_chat(run_id):
    store = RunStore()
    row = store.worker_run(run_id)
    owner, chat_id = row['owner'], row['chat_id']
    session = WarmBrowser(asyncio.get_running_loop(), run_id)
    try:
        while True:
            await asyncio.to_thread(execute, row['id'], session)
            expiry = store.warm_browser_expiry(owner, chat_id, session.token)
            if not expiry or not session.valid():
                break
            while time.time() < expiry and session.valid():
                next_row = store.claim_warm_browser(owner, chat_id, session.token, max_running=2)
                if next_row:
                    row = next_row
                    break
                await asyncio.sleep(0.25)
            else:
                break
    finally:
        store.release_warm_browser(owner, chat_id, session.token)
        await session.close()


def supervise():
    store = RunStore()
    tasks = TaskStore(store)
    with store.connect() as db:
        interrupted = [row['id'] for row in db.execute("SELECT id FROM runs WHERE status='running'")]
        db.execute('DELETE FROM browser_warm_reservations')
    for run_id in interrupted:
        store.finish(run_id, 'partial', 'worker_interrupted')
    active = {}
    stopping = {}
    next_schedule_check = 0.0
    try:
        while True:
            if time.monotonic() >= next_schedule_check:
                tasks.enqueue_due()
                next_schedule_check = time.monotonic() + 5
            for token, (process, started, budget, owner, previous_id) in list(active.items()):
                run = store.worker_run(token)
                run_id = run['id']
                if run_id != previous_id:
                    started, budget = time.monotonic(), RESEARCH_LIMITS[run['mode']][1]
                    active[token] = (process, started, budget, owner, run_id)
                    stopping.pop(token, None)
                expired = (time.time() >= run['deadline_at'] if run['deadline_at'] is not None
                           else time.monotonic() - started >= budget)
                if (expired and run['status'] == 'running') or run['status'] == 'cancelled' or token in stopping:
                    if run['deadline_at'] is not None:
                        stopping.setdefault(token, time.monotonic())
                        store.finish(run_id, 'partial' if run['result'] else 'failed', 'budget_exhausted')
                    if run['deadline_at'] is None or time.monotonic() - stopping[token] >= 12:
                        process.kill()
                        process.wait()
                        store.finish(run_id, 'partial' if run['result'] else 'failed', 'budget_exhausted')
                elif run['status'] != 'running':
                    expiry = store.warm_browser_expiry(owner, run['chat_id'], token)
                    if expiry is None or expiry <= time.time():
                        stopping.setdefault(token, time.monotonic())
                if process.poll() is not None:
                    run = store.worker_run(token)
                    run_id = run['id']
                    store.finish(run_id, 'failed', 'worker_exit')
                    store.release_warm_browser(owner, run['chat_id'], token)
                    del active[token]
                    stopping.pop(token, None)
            row = store.claim(max_running=2)
            if row:
                process = subprocess.Popen([sys.executable, '-m', 'app.worker', row['id']])
                active[row['id']] = (process, time.monotonic(), RESEARCH_LIMITS[row['mode']][1], row['owner'], row['id'])
            time.sleep(0.25)
    finally:
        for process, *_ in active.values():
            process.kill()
            process.wait()


if __name__ == '__main__':
    asyncio.run(serve_chat(sys.argv[1])) if len(sys.argv) > 1 else supervise()
