"""Dedicated local supervisor with isolated, deadline-bounded run processes."""
import json
import subprocess
import sys
import time
from dataclasses import asdict

from pydantic_core import PydanticSerializationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.google import GoogleModel

from app.chat import api_key, model_name
from app.attachments import TextAttachment, attachment_context
from app.model_calls import call_model, error_details, google_provider, model_call_events, model_output_events
from app.orchestration.decisions import DecisionUnavailable, RouteDecision, decide
from app.orchestration.orchestrator import run_turn
from app.orchestration.contracts import ResearchContract
from app.runs import RunStore
from app.schemas import ChatTurn


def execute(run_id):
    store = RunStore()
    with store.connect() as db:
        row = dict(db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone())
    debug = {'stages': []}

    def record_call(event):
        debug.setdefault('model_events', []).append(event)
        debug['operation'] = event
        phase = 'waiting_for_provider' if event['status'] == 'retrying' else event['stage']
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
        chat = store.detail(row['owner'], row['chat_id'])
        history = [ChatTurn.model_validate(item) for item in chat['messages'][-12:]]
        context = '\n'.join(f'{item.role}: {attachment_context(item.content, item.attachments)}' for item in history)[-16000:]
        try:
            decision = decide(message, context)
            debug['router'] = 'decisions'
        except DecisionUnavailable:
            debug['router'] = 'fallback'
            if api_key():
                agent = Agent(GoogleModel(model_name(), provider=google_provider(api_key())), output_type=RouteDecision,
                              instructions='Classify as chat, research, browser (interactive actions), or clarification. Research requires external evidence. Recommend deep for multiple items or complex constraints. Do not perform the task.')
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
            store.checkpoint(run_id, 'unsupported', debug, {'reply': 'This request needs browser interaction, which is not available yet.', 'sources': []})
            store.finish(run_id, 'partial', 'browser_unavailable')
            return
        rounds = 3 if row['mode'] == 'deep' else 2
        previous = store.get(row['owner'], row['parent_id']) if row['parent_id'] else None
        seed_sources = previous['result']['sources'] if previous and previous['result'] else []
        stalled = 0
        best_result = None
        best_score = (-1, -1, -1, -1)
        contract = None
        verification_cache = {}
        for index in range(rounds):
            previous_urls = {source["url"] for source in seed_sources}
            store.checkpoint(run_id, f'research_round_{index + 1}', debug)
            result = run_turn(message, history, search_requested=decision.workflow == 'research', seed_sources=seed_sources, contract=contract, verification_cache=verification_cache)
            if contract is None and result.debug.get('research_contract'):
                contract = ResearchContract.model_validate(result.debug['research_contract'])
            seed_sources = result.sources
            new_urls = {source["url"] for source in seed_sources} - previous_urls
            stalled = 0 if new_urls else stalled + 1
            debug['stages'].append(result.debug)
            coverage = result.debug.get('coverage', {})
            score = (coverage.get('verified', 0), coverage.get('supported_checks', 0),
                     coverage.get('offer_domains', 0), coverage.get('retained', 0))
            if best_result is None or score >= best_score:
                best_result, best_score = result, score
            store.checkpoint(run_id, 'answer_ready', debug, {'reply': best_result.reply, 'sources': best_result.sources})
            if decision.workflow != 'research' or not result.debug.get('validation_issues') and not result.debug.get('has_gaps', True):
                break
            if stalled >= 2:
                debug["stop_reason"] = "no_new_sources"
                break
            history.append(ChatTurn(role='assistant', content=result.reply))
            history.append(ChatTurn(role='user', content=
                'Continue researching the original request. Resolve missing evidence and find additional '
                'matching items to reach the requested count. Use candidate names from the findings. '
                'Search different retailers if offers share a domain. Do not replace destination availability '
                'with generic regional results. Target these per-candidate gaps: '
                + json.dumps(coverage.get('gaps', []), ensure_ascii=False)[:8000]
                + '\nValidation gaps: ' + str(result.debug.get('validation_issues', []))[:4000]))
        partial = best_result.debug.get('has_gaps', False)
        store.finish(run_id, 'partial' if partial else 'completed', 'finished')
    except Exception as exc:
        debug['error_type'] = type(exc).__name__
        debug['error'] = error_details(exc)
        try:
            store.checkpoint(run_id, 'error', debug)
        except (TypeError, ValueError, PydanticSerializationError):
            store.checkpoint(run_id, 'error', {'error_type': type(exc).__name__, 'error': error_details(exc), 'trace_error': 'serialization_failed'})
        current = store.get(row['owner'], run_id)
        phase = debug['error']['code'] if isinstance(exc, ModelAPIError) else 'provider_error'
        store.finish(run_id, 'partial' if current['result'] else 'failed', phase)
    finally:
        model_call_events.reset(observer)
        model_output_events.reset(output_observer)


def supervise():
    store = RunStore()
    with store.connect() as db:
        interrupted = [row['id'] for row in db.execute("SELECT id FROM runs WHERE status='running'")]
    for run_id in interrupted:
        store.finish(run_id, 'partial', 'worker_interrupted')
    active = {}
    try:
        while True:
            for run_id, (process, started, budget, owner) in list(active.items()):
                run = store.get(owner, run_id)
                expired = time.monotonic() - started >= budget
                if expired or run['status'] == 'cancelled':
                    process.kill()
                    process.wait()
                    store.finish(run_id, 'partial' if run['result'] else 'failed', 'budget_exhausted')
                if process.poll() is not None:
                    store.finish(run_id, 'failed', 'worker_exit')
                    del active[run_id]
            if len(active) < 2:
                row = store.claim()
                if row:
                    process = subprocess.Popen([sys.executable, '-m', 'app.worker', row['id']])
                    active[row['id']] = (process, time.monotonic(), 240 if row['mode'] == 'deep' else 120, row['owner'])
            time.sleep(0.25)
    finally:
        for process, *_ in active.values():
            process.kill()
            process.wait()


if __name__ == '__main__':
    execute(sys.argv[1]) if len(sys.argv) > 1 else supervise()
