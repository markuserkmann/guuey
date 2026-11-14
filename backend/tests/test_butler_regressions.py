import pytest
from app.runs import RunStore
from app.store import ChatStore


def test_empty_completed_result_cannot_claim_success(tmp_path):
    path = str(tmp_path / 'runs.db')
    store = RunStore(path)
    chat = ChatStore(path).create('owner')
    run = store.create('owner', chat['id'], 'r1', 'Find something', 'auto')
    store.claim()
    store.finish(run['id'], 'completed', 'finished')
    assert store.get('owner', run['id'])['status'] == 'failed'


def test_continuation_rejected_after_newer_turn(tmp_path):
    path = str(tmp_path / 'runs.db')
    store = RunStore(path)
    chat = ChatStore(path).create('owner')['id']
    first = store.create('owner', chat, 'r1', 'budget 1000', 'auto')
    store.claim()
    store.checkpoint(first['id'], 'answer_ready', {}, {'reply': 'partial', 'sources': []})
    store.finish(first['id'], 'partial', 'finished')
    second = store.create('owner', chat, 'r2', 'budget 800', 'auto')
    store.claim()
    store.checkpoint(second['id'], 'answer_ready', {}, {'reply': 'new answer', 'sources': []})
    store.finish(second['id'], 'completed', 'finished')
    with pytest.raises(ValueError, match='newer'):
        store.create('owner', chat, 'continue:'+first['id'], 'budget 1000', 'deep', first['id'])


def test_relative_dates_use_request_timezone_and_frozen_time():
    from app.task_context import dated_request
    message, context = dated_request('Dinner next Friday at 19:00; events during the next 14 days.',
                                     '2026-11-13T23:30:00+00:00', 'Europe/Tallinn')
    assert context['local_date'] == '2026-11-14'
    assert '2026-11-20 at 19:00' in message
    assert context['resolved_dates'][1] == {'expression': 'the next 14 days', 'start_inclusive': '2026-11-14', 'end_exclusive': '2026-11-28'}
    assert dated_request('next Friday', '2026-11-13T23:30:00+00:00', 'Europe/Tallinn') == dated_request('next Friday', '2026-11-13T23:30:00+00:00', 'Europe/Tallinn')


def test_timezone_is_persisted_and_part_of_request_identity(tmp_path):
    path = str(tmp_path / 'runs.db')
    store = RunStore(path)
    chat = ChatStore(path).create('owner')['id']
    run = store.create('owner', chat, 'r1', 'next Friday', 'auto', timezone='Europe/Tallinn')
    assert RunStore(path).get('owner', run['id'])['timezone'] == 'Europe/Tallinn'
    with pytest.raises(ValueError, match='different content'):
        store.create('owner', chat, 'r1', 'next Friday', 'auto', timezone='UTC')
    with pytest.raises(ValueError, match='timezone'):
        store.create('owner', chat, 'r2', 'next Friday', 'auto', timezone='Nowhere/Invalid')


def test_followup_receives_saved_contract_beyond_recent_messages(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import worker
    from app.orchestration.decisions import RouteDecision
    path = str(tmp_path / 'runs.db')
    monkeypatch.setenv('CHAT_DATABASE_PATH', path)
    chats, runs = ChatStore(path), RunStore(path)
    chat = chats.create('owner')['id']
    first = runs.create('owner', chat, 'r1', 'Laptop under 1000', 'auto')
    runs.claim()
    contract = {'destination': 'Finland', 'requirements': [{'id': 'ram', 'description': '16 GB RAM'}, {'id': 'linux', 'description': 'Linux support'}]}
    runs.checkpoint(first['id'], 'answer_ready', {'stages': [{'research_contract': contract}]}, {'reply': 'partial', 'sources': []})
    runs.finish(first['id'], 'partial', 'finished')
    for i in range(8):
        chats.append_exchange('owner', chat, f'question {i}', 'answer')
    second = runs.create('owner', chat, 'r2', 'Change only the budget to 800', 'auto')
    runs.claim()
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='research', confidence=1))
    captured = []
    def research(message, history, **kwargs):
        captured.extend(history)
        return SimpleNamespace(reply='fixture answer', sources=[], debug={'has_gaps': False})
    monkeypatch.setattr(worker, 'run_turn', research)
    worker.execute(second['id'])
    assert all(value in captured[-1].content for value in ('Finland', '16 GB RAM', 'Linux support', 'unrelated new task'))
    other = chats.create('owner')['id']
    third = runs.create('owner', other, 'r3', 'New dinner task', 'auto')
    runs.claim()
    captured.clear()
    worker.execute(third['id'])
    assert captured == []


def test_enquiry_is_prepared_only_and_bound_to_reviewed_text():
    import json
    from types import SimpleNamespace
    from app.enquiries import prepare_enquiry, render_enquiry
    source = {'url': 'https://example.com/repair', 'title': 'Repair Shop', 'text': 'Repair Shop iPhone 13 battery replacement'}
    answer = {'findings': [{'name': 'Repair Shop', 'source_url': source['url'], 'source_kind': 'service',
                           'checks': [{'requirement': 'Published price', 'status': 'unknown', 'statement': '', 'evidence': []}]}]}
    result = SimpleNamespace(sources=[source], debug={'research_contract': {'activity': 'service', 'requirements': [{'description': 'iPhone 13 battery replacement in Helsinki'}]}, 'output': json.dumps(answer)})
    action = prepare_enquiry('Prepare an appointment enquiry for iPhone 13 battery replacement.', result, 'run1')
    assert action['state'] == 'prepared'
    assert action['unresolved'] == ['Published price']
    assert 'iPhone 13' in action['text'] and 'Helsinki' in action['text']
    assert 'Nothing has been sent or booked' in render_enquiry(action)
    assert action['proposal_id'] == prepare_enquiry('Prepare an appointment enquiry for iPhone 13 battery replacement.', result, 'run1')['proposal_id']
    revised = prepare_enquiry('Prepare an appointment enquiry for iPhone 13 battery replacement. Budget €80.', result, 'run1')
    assert revised['proposal_id'] != action['proposal_id']
    result.sources = []
    assert prepare_enquiry('Prepare an appointment enquiry.', result, 'run1') is None


def test_budget_is_atomic_across_parallel_requests(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from app.retrieval_budget import reserve, record
    from app.research import SearchUnavailable
    path = str(tmp_path / 'usage.db')
    monkeypatch.setenv('EXA_USAGE_DATABASE', path)
    monkeypatch.setenv('EXA_REQUEST_LIMIT', '3')
    def attempt(_):
        try:
            ticket = reserve('search')
            record(ticket, {'costDollars': {'total': 0.005}})
            return True
        except SearchUnavailable:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(12))) == 3
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM requests WHERE cost IS NOT NULL').fetchone()[0] == 3


def test_worker_persists_prepared_enquiry_without_submission(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from app import worker
    from app.orchestration.decisions import RouteDecision
    path = str(tmp_path / 'runs.db')
    monkeypatch.setenv('CHAT_DATABASE_PATH', path)
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='research', confidence=1))
    source = {'url': 'https://example.com/repair', 'title': 'Repair Shop', 'text': 'Repair Shop iPhone 13 battery replacement'}
    answer = {'findings': [{'name': 'Repair Shop', 'source_url': source['url'], 'source_kind': 'service', 'checks': [{'requirement': 'Price', 'status': 'unknown', 'statement': '', 'evidence': []}]}]}
    contract = {'activity': 'service', 'destination': 'Helsinki', 'requirements': [{'id': 'model', 'description': 'iPhone 13 battery replacement'}]}
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: SimpleNamespace(reply='Unverified price', sources=[source], debug={'research_contract': contract, 'output': json.dumps(answer), 'has_gaps': True}))
    chats, runs = ChatStore(path), RunStore(path)
    chat = chats.create('owner')['id']
    run = runs.create('owner', chat, 'draft-request', 'Prepare an appointment enquiry for iPhone 13 battery replacement.', 'auto', timezone='Europe/Tallinn')
    runs.claim()
    worker.execute(run['id'])
    saved = RunStore(path).get('owner', run['id'])
    assert saved['status'] == 'partial'
    assert saved['result']['action']['state'] == 'prepared'
    assert 'Nothing has been sent or booked' in chats.detail('owner', chat)['messages'][-1]['content']
    assert runs.get('another-owner', run['id']) is None
    assert runs.create('owner', chat, 'draft-request', run['message'], 'auto', timezone='Europe/Tallinn')['id'] == run['id']
    assert len(chats.detail('owner', chat)['messages']) == 2


def test_model_null_clarification_does_not_stop_research():
    from app.orchestration.contracts import ResearchContract
    from app.orchestration.refinement import prepared_action
    from app.orchestration.grounding import GroundedAnswer
    contract = ResearchContract(activity='service', clarification_question='null', timezone='None', count_quote='')
    assert contract.clarification_question is None
    assert contract.timezone is None
    assert contract.count_quote is None
    assert prepared_action(contract, GroundedAnswer(), [])['kind'] != 'clarification'


def test_service_enquiry_uses_requirements_not_internal_prompt():
    import json
    from types import SimpleNamespace
    from app.enquiries import prepare_enquiry
    source = {'url': 'https://example.com/venue', 'title': 'Venue', 'text': 'Venue restaurant'}
    answer = {'findings': [{'name': 'Venue', 'source_url': source['url'], 'source_kind': 'service', 'checks': [{'requirement': '2 people at 19:00', 'status': 'unknown', 'statement': '', 'evidence': []}]}]}
    contract = {'activity': 'service', 'subject': 'vegan anniversary dinner', 'destination': 'Helsinki', 'dates': ['2026-11-20'], 'timezone': 'Europe/Tallinn', 'requirements': [{'description': '2 people at 19:00'}, {'description': 'At most €120 total'}]}
    result = SimpleNamespace(sources=[source], debug={'research_contract': contract, 'output': json.dumps(answer)})
    action = prepare_enquiry('Plan dinner. Prepare a booking request.\nRequest local date: 2026-11-14.', result, 'run')
    assert 'Prepare a booking' not in action['text']
    assert 'Request local date' not in action['text']
    assert all(value in action['text'] for value in ('Helsinki', 'vegan', '2 people at 19:00', '€120', '2026-11-20', 'Europe/Tallinn'))


def test_incomplete_candidate_cannot_claim_all_requirements_in_assessment():
    from app.orchestration.grounding import GroundedAnswer, render_answer
    answer = GroundedAnswer.model_validate({'findings': [{'name': 'Event', 'source_url': 'https://example.com', 'checks': [{'requirement': 'At most €20', 'status': 'unknown', 'statement': '', 'evidence': []}], 'assessment': 'Meets all requirements', 'assessment_evidence': [{'source_url': 'https://example.com', 'quote': 'Free Event'}]}]})
    reply, _ = render_answer(answer, [{'url': 'https://example.com', 'title': 'Event', 'text': 'Free Event'}])
    assert 'To confirm' in reply and 'Not found' not in reply
    assert 'Meets all requirements' not in reply


def test_page_question_reads_directly_without_prior_candidate_requirements(monkeypatch):
    from app.orchestration import orchestrator
    from app.orchestration.contracts import ResearchContract
    from app.schemas import ChatTurn
    captured = {}
    def extract(urls, **kwargs):
        captured['extract'] = (urls, kwargs)
        return [{'url': urls[0], 'title': 'Repair', 'text': 'Battery €99.90. Warranty 12 months.'}]
    def generate(message, history, sources, **kwargs):
        captured['history'] = history
        captured['contract'] = kwargs['contract']
        return 'Fixture page answer', {}
    monkeypatch.setattr(orchestrator, 'extract_pages', extract)
    monkeypatch.setattr(orchestrator, 'generate_reply', generate)
    monkeypatch.setattr(orchestrator, 'plan_searches', lambda *args: (_ for _ in ()).throw(AssertionError('Page read must not plan new searches')))
    result = orchestrator.run_turn('Read https://example.com/repair and tell me the price and warranty.', [ChatTurn(role='user', content='Compare Helsinki repairs')], search_requested=True, contract=ResearchContract(activity='service', destination='Helsinki'))
    assert captured['extract'][1]['fresh'] is True
    assert captured['history'] == []
    assert captured['contract'].activity == 'research'
    assert captured['contract'].requirements == []
    assert result.reply == 'Fixture page answer'


def test_summary_checks_do_not_replace_atomic_contract_evidence():
    from app.chat import missing_requirement_checks
    from app.orchestration.contracts import ResearchContract, Requirement
    from app.orchestration.grounding import GroundedAnswer
    contract = ResearchContract(activity='service', requirements=[Requirement(id='price', description='Published price', kind='price'), Requirement(id='location', description='Helsinki', kind='location')])
    answer = GroundedAnswer.model_validate({'response': 'Unstructured comparison', 'findings': [{'name': 'Shop', 'checks': [{'requirement': 'Service exists', 'status': 'met', 'statement': 'Repair available', 'evidence': []}]}]})
    assert missing_requirement_checks(answer, contract) == {'Shop': ['location', 'price']}
    answer.findings[0].checks = [answer.findings[0].checks[0].model_copy(update={'requirement_id': r.id, 'status': 'unknown'}) for r in contract.requirements]
    assert missing_requirement_checks(answer, contract) == {}


def test_unverified_reason_cannot_publish_unchecked_external_claim():
    from app.orchestration.grounding import RequirementCheck, check_text
    check = RequirementCheck(requirement='Friday availability', status='unknown', statement='', evidence=[], reason='The restaurant is open on Thursdays until 10 PM.')
    assert check_text(check, []) == ''
