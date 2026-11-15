from datetime import UTC, datetime

import pytest

from app import worker
from app.browser.store import BrowserStore
from app.orchestration.browser_handoff import browser_handoff, handoff_context
from app.orchestration.contracts import Requirement, ResearchContract
from app.orchestration.decisions import RouteDecision
from app.orchestration.grounding import GroundedAnswer, LLMAnswer, expand_llm_answer, render_answer
from app.orchestration.interpretation import query_key
from app.orchestration.orchestrator import RefinementComplete, TurnResult
from app.orchestration.refinement import fallback_gap_queries, finish_research_result, prioritize_refinement_queries
from app.runs import RunStore
from app.store import ChatStore


REQUEST = 'Find a sauna with wheelchair access.'
URL = 'https://harborsauna.fi/access'
TEXT = 'Harbor Sauna has wheelchair access.'


def task():
    return ResearchContract(activity='service', original_request=REQUEST, requirements=[
        Requirement(id='access', description='Wheelchair access')])


def result(*, complete=False, interactive=False, knowledge=False):
    contract = task()
    source = {'url': URL, 'title': 'Harbor Sauna', 'text': TEXT if complete else 'Harbor Sauna offers sauna hire.',
              'retrieval': 'live', 'retrieved_at': datetime.now(UTC).isoformat()}
    answer = GroundedAnswer()
    if complete or interactive:
        answer = GroundedAnswer.model_validate({'findings': [{'name': 'Harbor Sauna', 'source_url': URL,
            'source_kind': 'service', 'checks': [{'requirement_id': 'access', 'requirement': 'Wheelchair access',
                'status': 'met' if complete else 'unknown', 'statement': TEXT if complete else '',
                'reason': 'Requires an interactive configurator' if interactive else '',
                'evidence': [{'source_url': URL, 'quote': TEXT}] if complete else []}]}]})
    if knowledge:
        answer = GroundedAnswer.model_validate({'knowledge': {'verdict': 'Check the provider website for access.',
            'points': [{'statement': source['text'], 'evidence': [{'source_url': URL, 'quote': source['text']}]}],
            'unresolved_requirements': ['access']}})
    turn = TurnResult(reply='', sources=[source], debug={
        'research_contract': contract.model_dump(), 'output': answer.model_dump_json()})
    return finish_research_result(turn, contract)


def setup_run(tmp_path, monkeypatch, *, mode='auto', enabled=True, opt_out=False, message=REQUEST):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'recovery.sqlite3'))
    monkeypatch.setenv('BROWSER_ENABLED', str(enabled).lower())
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='research', confidence=1))
    runs = RunStore()
    chat = ChatStore().create('owner')
    message += ' without a browser' if opt_out else ''
    run = runs.create('owner', chat['id'], 'request', message, mode)
    runs.claim()
    browser_calls = []

    def browse(run_id, row, message, context, debug, store, **options):
        browser_calls.append({'context': context, 'debug': debug, 'options': options})
        BrowserStore(store).start(row['owner'], run_id)
        store.checkpoint(run_id, 'browser_done', debug, {'reply': 'Access checked on the provider site.', 'sources': []})
        store.finish(run_id, 'completed', 'browser_done')

    monkeypatch.setattr(worker, 'execute_browser', browse)
    return runs, run, browser_calls


def test_unverified_provider_links_get_targeted_research_before_browser():
    turn = result()
    assert 'provider links' in turn.reply
    assert turn.debug['has_gaps']
    assert turn.debug['next_action']['kind'] == 'research'
    assert browser_handoff(turn, task()) is None
    handoff = browser_handoff(turn, task(), search_exhausted=True)
    assert handoff['reason'] == 'research_exhausted'
    assert handoff['gaps'][0]['requirement_id'] == 'access'
    assert handoff['sources'] == [{'url': URL, 'title': 'Harbor Sauna'}]
    assert 'does not authorize booking' in handoff_context(handoff)


def test_empty_shortlist_without_requested_count_still_gets_finite_gap_queries():
    turn = result()
    answer = GroundedAnswer.model_validate_json(turn.debug['output']).model_dump()
    queries = prioritize_refinement_queries([], answer, task(), set(), turn.sources)
    assert queries and 'Wheelchair access' in queries[0].query
    assert 'official source' in queries[0].query
    assert fallback_gap_queries(answer, task(), {query_key(query) for query in queries}, turn.sources) == []


def test_background_knowledge_and_advice_do_not_complete_requested_research():
    turn = result(knowledge=True)
    assert turn.debug['has_gaps']
    assert turn.debug['next_action']['kind'] == 'research'
    assert turn.debug['next_action']['gaps'][0]['requirement_id'] == 'access'
    answer = GroundedAnswer.model_validate_json(turn.debug['output']).model_dump()
    assert fallback_gap_queries(answer, task(), set(), turn.sources)
    assert browser_handoff(turn, task(), search_exhausted=True)


def test_compact_knowledge_preserves_unresolved_requirement_ids():
    answer = expand_llm_answer(LLMAnswer.model_validate({'knowledge': {'unresolved_requirements': ['access']}}), [], task())
    assert answer.knowledge.unresolved_requirements == ['access']


@pytest.mark.parametrize('interactive', [False, True])
def test_interactive_or_unreadable_pages_can_use_browser_without_another_search(interactive):
    turn = result(interactive=interactive)
    if not interactive:
        turn.sources[0]['text'] = 'Please enable JavaScript to view this page.'
    assert browser_handoff(turn, task())['reason'] == 'interactive_evidence_required'


@pytest.mark.parametrize('artifact', ['message_draft', 'checklist', 'plan', 'form_draft'])
def test_recovery_does_not_turn_requested_artifacts_into_browser_work(artifact):
    contract = task().model_copy(update={'artifact': artifact})
    assert browser_handoff(result(interactive=True), contract, search_exhausted=True) is None


def test_material_clarification_and_complete_answers_do_not_escalate():
    contract = task().model_copy(update={'clarification_question': 'Which country should I search?'})
    assert browser_handoff(result(), contract, search_exhausted=True) is None
    assert browser_handoff(result(complete=True), task(), search_exhausted=True) is None
    assert browser_handoff(result(), None, search_exhausted=True) is None


def test_verified_negative_for_a_named_provider_does_not_trigger_another_check():
    turn = result(complete=True)
    answer = GroundedAnswer.model_validate_json(turn.debug['output'])
    answer.findings[0].checks[0].status = 'not_met'
    answer.findings[0].checks[0].statement = 'Harbor Sauna does not have wheelchair access.'
    answer.findings[0].checks[0].evidence[0].quote = answer.findings[0].checks[0].statement
    turn.sources[0]['text'] = answer.findings[0].checks[0].statement
    turn.debug['output'] = answer.model_dump_json()
    contract = task().model_copy(update={'exact_entities': ['Harbor Sauna']})
    assert browser_handoff(turn, contract, search_exhausted=True) is None


def test_complete_knowledge_with_how_to_steps_needs_no_recovery():
    contract = ResearchContract(original_request='Explain how to check wheelchair access.')
    turn = result(knowledge=True)
    answer = GroundedAnswer.model_validate_json(turn.debug['output'])
    answer.knowledge.unresolved_requirements = []
    turn.debug['output'] = answer.model_dump_json()
    assert browser_handoff(turn, contract, search_exhausted=True) is None
    assert fallback_gap_queries(answer.model_dump(), contract, set(), turn.sources) == []


def test_checkout_stock_excluded_from_search_can_still_be_checked_in_browser():
    contract = ResearchContract(activity='product', original_request='Find this laptop in stock')
    answer = GroundedAnswer.model_validate({'findings': [{'name': 'Laptop', 'source_url': URL, 'checks': [
        {'requirement_id': 'stock', 'requirement': 'In stock', 'status': 'unknown', 'statement': '',
         'refinement_excluded': True, 'reason': 'Stock shown at checkout', 'evidence': []}]}]})
    turn = TurnResult(reply='', sources=[], debug={'output': answer.model_dump_json()})
    assert browser_handoff(turn, contract) is None
    assert browser_handoff(turn, contract, search_exhausted=True)['reason'] == 'unresolved_live_availability'


def test_handoff_keeps_verified_facts_and_excludes_unsafe_source_urls():
    turn = result(complete=True)
    contract = task().model_copy(update={'requirements': [*task().requirements,
        Requirement(id='duration', description='Session duration')]})
    turn = finish_research_result(turn, contract)
    turn.sources.extend([{'url': 'http://127.0.0.1/admin', 'title': 'Private', 'text': ''}, turn.sources[0]])
    handoff = browser_handoff(turn, contract, search_exhausted=True)
    assert len(handoff['sources']) == 1
    assert handoff['verified_checks'][0]['statement'] == TEXT
    assert handoff['gaps'][0]['requirement_id'] == 'duration'


def test_renderer_does_not_tell_user_to_repeat_research():
    assert 'Next step: open' not in result(complete=True).reply
    assert 'confirm suitability' not in result().reply
    text, _ = render_answer(GroundedAnswer(), [], task())
    assert 'Try a broader search' not in text


def test_worker_tries_deeper_search_before_offering_browser(tmp_path, monkeypatch):
    runs, run, browser_calls = setup_run(tmp_path, monkeypatch)
    research_calls = []

    def research(message, history, **kwargs):
        research_calls.append({'history': list(history), **kwargs})
        return result()

    monkeypatch.setattr(worker, 'run_turn', research)
    session = object()
    worker.execute(run['id'], browser_session=session)
    saved = runs.get('owner', run['id'])
    assert len(research_calls) == 2 and not browser_calls
    assert 'Wheelchair access' in research_calls[1]['history'][-1].content
    assert research_calls[1]['seed_sources'][0]['url'] == URL
    assert saved['status'] == 'partial'
    assert saved['debug']['recovery']['action'] == 'ask_user'
    assert saved['debug']['browser_offer']['reason'] == 'research_exhausted'
    assert saved['result']['next_action']['kind'] == 'browser_opt_in'
    assert saved['result']['sources'][0]['url'] == URL
    assert 'This will take longer.' in saved['result']['reply']
    assert saved['browser'] is None


def test_second_research_round_can_resolve_without_browser(tmp_path, monkeypatch):
    runs, run, browser_calls = setup_run(tmp_path, monkeypatch)
    calls = []

    def research(*args, **kwargs):
        calls.append(True)
        return result(complete=len(calls) == 2)

    monkeypatch.setattr(worker, 'run_turn', research)
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert len(calls) == 2 and not browser_calls
    assert saved['status'] == 'completed'
    assert 'open the provider link' not in saved['result']['reply']


@pytest.mark.parametrize('stop', ['stall', 'no_queries', 'deadline', 'provider_deadline'])
def test_terminal_search_paths_offer_browser_without_opening_it(tmp_path, monkeypatch, stop):
    runs, run, browser_calls = setup_run(tmp_path, monkeypatch, mode='deep')
    calls = []
    clock = [0]
    if stop == 'deadline':
        from types import SimpleNamespace
        monkeypatch.setattr(worker, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    def research(*args, **kwargs):
        calls.append(True)
        if stop == 'no_queries' and len(calls) == 2:
            raise RefinementComplete()
        if stop == 'provider_deadline' and len(calls) == 2:
            from app.research_modes import ResearchDeadline
            raise ResearchDeadline()
        if stop == 'deadline':
            clock[0] += 130
        return result()

    monkeypatch.setattr(worker, 'run_turn', research)
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert not browser_calls
    assert len(calls) == (1 if stop == 'deadline' else 2)
    assert saved['debug']['browser_offer']['reason'] == 'research_exhausted'
    assert saved['status'] == 'partial'
    assert saved['phase'] == 'awaiting_browser_choice'


@pytest.mark.parametrize('enabled,opt_out', [(False, False), (True, True)])
def test_recovery_respects_browser_settings_and_explains_the_obstacle(tmp_path, monkeypatch, enabled, opt_out):
    runs, run, browser_calls = setup_run(tmp_path, monkeypatch, enabled=enabled, opt_out=opt_out)
    calls = []

    def research(*args, **kwargs):
        calls.append(True)
        return result()

    monkeypatch.setattr(worker, 'run_turn', research)
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert len(calls) == 2 and not browser_calls
    assert saved['status'] == 'partial'
    assert 'Browser verification is' in saved['result']['reply']


def test_fast_mode_keeps_one_search_round_then_offers_browser(tmp_path, monkeypatch):
    runs, run, browser_calls = setup_run(tmp_path, monkeypatch, mode='fast')
    calls = []

    def research(*args, **kwargs):
        calls.append(True)
        return result()

    monkeypatch.setattr(worker, 'run_turn', research)
    worker.execute(run['id'])
    assert len(calls) == 1 and not browser_calls
    assert runs.get('owner', run['id'])['result']['next_action']['kind'] == 'browser_opt_in'


@pytest.mark.parametrize('mode', ['fast', 'auto'])
def test_reported_store_availability_still_offers_optional_browser_check(tmp_path, monkeypatch, mode):
    message = 'Find a store with packing tape in stock'
    runs, run, browser_calls = setup_run(tmp_path, monkeypatch, mode=mode, message=message)
    source = {'url': 'https://store.example/a', 'title': 'Store A',
              'text': 'Store A sells packing tape.\n\nStore A has availability.'}
    contract = ResearchContract(original_request=message, requested_count=1, requirements=[
        Requirement(id='type', description='Packing tape'),
        Requirement(id='availability', description='Current availability', kind='availability')])
    answer = GroundedAnswer.model_validate({'findings': [{'name': 'Store A', 'source_url': source['url'], 'checks': [
        {'requirement_id': 'type', 'requirement': 'Packing tape', 'status': 'met', 'statement': 'Sells packing tape',
         'evidence': [{'source_url': source['url'], 'quote': 'Store A sells packing tape.'}]},
        {'requirement_id': 'availability', 'requirement': 'Availability', 'status': 'unknown', 'basis': 'reported',
         'statement': 'Has availability', 'evidence': [{'source_url': source['url'], 'quote': 'Store A has availability.'}]}]}]})
    searches = []

    def research(*args, **kwargs):
        searches.append(True)
        return TurnResult(reply='', sources=[source], debug={
            'output': answer.model_dump_json(), 'research_contract': contract.model_dump()})

    monkeypatch.setattr(worker, 'run_turn', research)
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert searches == [True] and not browser_calls
    assert saved['phase'] == 'awaiting_browser_choice' and saved['status'] == 'partial'
    assert saved['result']['next_action']['kind'] == 'browser_opt_in'
    assert saved['debug']['browser_offer']['reason'] == 'unresolved_live_availability'
    assert saved['debug']['stages'][0]['coverage']['verified'] == 0
    assert 'Reported: Has availability' in saved['result']['reply']
    assert saved['result']['sources'][0]['url'] == source['url']


def test_exhausted_deep_continuation_offers_browser_with_saved_research(tmp_path, monkeypatch):
    runs, run, browser_calls = setup_run(tmp_path, monkeypatch, enabled=False)
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: result())
    worker.execute(run['id'])
    monkeypatch.setenv('BROWSER_ENABLED', 'true')
    child = runs.create('owner', run['chat_id'], 'continue', REQUEST, 'deep', run['id'])
    runs.claim()

    def exhausted(*args, **kwargs):
        raise RefinementComplete()

    monkeypatch.setattr(worker, 'run_turn', exhausted)
    worker.execute(child['id'])
    assert not browser_calls
    saved = runs.get('owner', child['id'])
    assert saved['debug']['browser_offer']['sources'][0]['url'] == URL
    assert saved['status'] == 'partial'


def test_retrieval_executes_fallback_queries_when_planner_gives_up_on_leads(monkeypatch):
    from app.orchestration import orchestrator
    from app.orchestration.contracts import SearchPlan

    prior = result()
    answer = GroundedAnswer.model_validate_json(prior.debug['output']).model_dump()
    calls = []
    monkeypatch.setattr(orchestrator, 'plan_searches', lambda *args: (SearchPlan(contract=task(), queries=[]), {}))
    monkeypatch.setattr(orchestrator, 'search', lambda query, **kwargs: calls.append(query) or result(complete=True).sources)
    monkeypatch.setattr(orchestrator, 'extract_pages', lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator, 'fill_gaps', lambda *args: (result(complete=True).reply, result(complete=True).debug))
    attempted = set()
    turn = orchestrator.run_turn(REQUEST, [], search_requested=True, contract=task(), prior_answer=answer,
                                 seed_sources=prior.sources, attempted_queries=attempted)
    assert calls and 'Wheelchair access' in calls[0]
    assert len(attempted) == len(calls)
    assert turn.debug['executed_queries'][0]['gap'] == 'Wheelchair access'
    with pytest.raises(RefinementComplete):
        orchestrator.run_turn(REQUEST, [], search_requested=True, contract=task(), prior_answer=answer,
                              seed_sources=prior.sources, attempted_queries=attempted)
