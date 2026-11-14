"""Dedicated local supervisor with isolated, deadline-bounded run processes."""
import json
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
from app.orchestration.decisions import DecisionUnavailable, RouteDecision, decide
from app.orchestration.orchestrator import RefinementComplete, run_turn
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


def execute(run_id):
    run_started = time.monotonic()
    store = RunStore()
    with store.connect() as db:
        row = dict(db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone())
    debug = {'stages': []}
    mode_token = research_mode.set(row['mode'])
    deadline_token = research_deadline.set(run_started + RESEARCH_LIMITS[row['mode']][1] - 3)
    debug_lock = RLock()

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
        timestamp = time.monotonic()
        if output != previous_output and (previous_output is None or timestamp - last_output_at >= 0.15):
            store.update_live_output(run_id, output)
            previous_output, last_output_at = output, timestamp

    output_observer = model_output_events.set(record_output)
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
        try:
            decision = decide(message, context)
            debug['router'] = 'decisions'
        except DecisionUnavailable:
            debug['router'] = 'fallback'
            if api_key():
                agent = Agent(GoogleModel(model_name(), provider=google_provider(api_key())), output_type=RouteDecision,
                              instructions='Classify as chat, research, browser, or clarification. Browser covers signing in to websites, filling and submitting forms, and ordering, booking or buying on the user\'s behalf. Research requires external evidence. Preparing a booking request or appointment enquiry for review is research. Recommend deep for multiple items or complex constraints. Do not perform the task.')
                try:
                    response = call_model(agent, context + '\nCurrent request: ' + message, stage='routing', model_settings={'max_tokens': 256})
                    decision = response.output
                    debug['routing_usage'] = asdict(response.usage)
                except ModelAPIError:
                    raise
                except Exception:
                    decision = RouteDecision(workflow='research', confidence=0, deep_recommended=False)
                    debug['router'] = 'degraded'
            else:
                decision = RouteDecision(workflow='chat', confidence=0)
        debug['decision'] = decision.model_dump()
        store.checkpoint(run_id, 'planning', debug)
        if decision.workflow == 'browser':
            if BrowserSettings.from_env().enabled:
                execute_browser(run_id, row, message, context, debug, store)
                return
            store.checkpoint(run_id, 'unsupported', debug, {'reply': 'This request needs browser interaction, which is not available yet.', 'sources': []})
            store.finish(run_id, 'partial', 'browser_unavailable')
            return
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
                    store.checkpoint(run_id, 'answer_ready', debug, previous['result'])
                    store.finish(run_id, 'partial', 'finished')
                    return
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
        debug['wall_s'] = round(time.monotonic() - run_started, 3)
        store.checkpoint(run_id, 'answer_ready', debug)
        partial = best_result.debug.get('has_gaps', False)
        store.finish(run_id, 'partial' if partial else 'completed', 'finished')
    except Exception as exc:
        if isinstance(exc, ResearchDeadline) or isinstance(exc, ModelRequestTimeout) and remaining_seconds() <= 1:
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
        research_deadline.reset(deadline_token)
        research_mode.reset(mode_token)
        model_call_events.reset(observer)
        model_output_events.reset(output_observer)


def supervise():
    store = RunStore()
    with store.connect() as db:
        interrupted = [row['id'] for row in db.execute("SELECT id FROM runs WHERE status='running'")]
    for run_id in interrupted:
        store.finish(run_id, 'partial', 'worker_interrupted')
    active = {}
    stopping = {}
    try:
        while True:
            for run_id, (process, started, budget, owner) in list(active.items()):
                run = store.get(owner, run_id)
                expired = (time.time() >= run['deadline_at'] if run['deadline_at'] is not None
                           else time.monotonic() - started >= budget)
                if expired or run['status'] == 'cancelled':
                    if run['deadline_at'] is not None:
                        stopping.setdefault(run_id, time.monotonic())
                        store.finish(run_id, 'partial' if run['result'] else 'failed', 'budget_exhausted')
                    if run['deadline_at'] is None or time.monotonic() - stopping[run_id] >= 12:
                        process.kill()
                        process.wait()
                        store.finish(run_id, 'partial' if run['result'] else 'failed', 'budget_exhausted')
                if process.poll() is not None:
                    store.finish(run_id, 'failed', 'worker_exit')
                    del active[run_id]
                    stopping.pop(run_id, None)
            if len(active) < 2:
                row = store.claim()
                if row:
                    process = subprocess.Popen([sys.executable, '-m', 'app.worker', row['id']])
                    active[row['id']] = (process, time.monotonic(), RESEARCH_LIMITS[row['mode']][1], row['owner'])
            time.sleep(0.25)
    finally:
        for process, *_ in active.values():
            process.kill()
            process.wait()


if __name__ == '__main__':
    execute(sys.argv[1]) if len(sys.argv) > 1 else supervise()
