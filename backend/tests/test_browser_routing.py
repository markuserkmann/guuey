from datetime import UTC, datetime

import pytest

from app import worker
from app.browser.store import BrowserStore
from app.orchestration.browser_handoff import browser_handoff, unresolved_live_requirements
from app.orchestration.contracts import ResearchContract, Requirement
from app.orchestration.decisions import DecisionUncertain, DecisionUnavailable, RouteDecision, browser_action_requested, browser_preference, uncertain_workflow
from app.orchestration.grounding import GroundedAnswer
from app.orchestration.orchestrator import TurnResult
from app.orchestration.refinement import finish_research_result
from app.research import SearchUnavailable
from app.runs import RunStore
from app.store import ChatStore
from app.task_context import dated_request


REQUEST = 'Can you check availability of Ljudmilla in avancia eeden on upcoming tuesday'
URL = 'https://avancia.ee/eedeni-keskus/online-broneering/'


@pytest.mark.parametrize('message,expected', [
    (REQUEST + ' browser', True),
    ('Use the browser to check availability', True),
    ('Check the calendar using a web browser.', True),
    ('browser', True),
    (REQUEST, None),
    ('What is a browser?', None),
    ('How do I use a browser?', None),
    ('Compare browser performance', None),
    (REQUEST + "; don't use a browser", False),
    ('Check availability without opening a web browser', False),
    ('Find opening hours, no browser', False),
])
def test_explicit_browser_preference(message, expected):
    assert browser_preference(message) is expected


ACTION_REQUESTS = [
    'Order a pizza for delivery',
    'Please order "Margherita" from the restaurant',
    'Can you buy the cheapest laptop for me?',
    'I want you to purchase the headphones',
    'I need to order groceries',
    'Order the headphones using the discount code',
    'Find the cheapest laptop and buy it',
    'Find the cheapest laptop and then buy it',
    'Research the options, then place an order for the cheapest one',
    'Compare flights and book the cheapest one',
    'Book me a haircut tomorrow',
    'Reserve it for me',
    'Cancel my subscription',
    'Reschedule my appointment to Tuesday',
    'Please sign in to my account',
    'Sign up for an account on the website',
    'Can you fill out the delivery form?',
    'Submit the application',
    'Click the checkout button',
    'Update my delivery address',
    'Change my account settings',
    'Remove the headphones from my cart',
    'Find the cheapest laptop and add it to my basket',
    'Add milk and bread to my basket',
    'Add milk, bread and eggs to my cart',
    'Remove milk and bread from my cart',
    'Add "noise-cancelling headphones" to my cart without checking out',
    'Complete the checkout',
    'Send an email to the restaurant',
    'Draft a booking enquiry and send it to the salon',
    'Post this review on the website',
    'Upload the document to the portal',
    'Download my invoice',
]


@pytest.mark.parametrize('message', ACTION_REQUESTS)
def test_action_requests_route_to_browser(message):
    assert browser_action_requested(message)
    assert uncertain_workflow(message)[0] == 'browser'


@pytest.mark.parametrize('message', [
    'Find the cheapest laptop',
    'Compare laptops to buy',
    'Find out how to order groceries',
    'How do I order a pizza?',
    'Can you explain how to book and cancel an appointment?',
    'Draft an email asking the restaurant to reserve a table',
    'Write a guide about placing an order',
    'Implement an add to cart button',
    'Add a checkout button to the basket component',
    'Order the results by price',
    'Order these names alphabetically',
    'Book recommendations for a holiday',
    'Update this summary',
    'Download speeds of popular broadband plans',
    'Do not order anything',
    'Find a laptop, but do not buy it',
    "Don't book or cancel my appointment",
    'Compare prices without ordering anything',
    'Do not book a table and send a message',
    'Read this text: "order a pizza and send an email"',
    'Summarize this: `buy a laptop`',
])
def test_information_and_negated_actions_do_not_force_browser(message):
    assert not browser_action_requested(message)
    assert uncertain_workflow(message)[0] == 'research'


def availability_result(artifact='answer'):
    contract = ResearchContract(activity='service', original_request=REQUEST, artifact=artifact,
        exact_entities=['Ljudmilla', 'Avancia Eeden'], dates=['2026-11-17'], requirements=[
            Requirement(id='availability', kind='availability', description='Ljudmilla appointment slots on 2026-11-17')])
    source = {'url': URL, 'title': 'Online broneering', 'text': 'Ljudmilla works at Avancia Eeden.',
              'retrieval': 'live', 'retrieved_at': datetime.now(UTC).isoformat()}
    answer = GroundedAnswer.model_validate({'knowledge': {
        'verdict': 'Ljudmilla is on staff, but appointment slots require the booking calendar.',
        'points': [{'statement': source['text'], 'evidence': [{'source_url': URL, 'quote': source['text']}]}]}})
    return contract, TurnResult(reply='Staff information only', sources=[source], debug={
        'research_contract': contract.model_dump(), 'output': answer.model_dump_json(), 'has_gaps': False})


def test_staff_knowledge_does_not_complete_live_availability_request():
    contract, result = availability_result()
    result = finish_research_result(result, contract)
    assert result.debug['has_gaps']
    assert result.debug['next_action']['kind'] == 'research'
    assert result.debug['coverage']['gaps'][0]['requirement_id'] == 'availability'
    handoff = browser_handoff(result, contract)
    assert handoff['reason'] == 'unresolved_live_availability'
    assert handoff['sources'][0]['url'] == URL


@pytest.mark.parametrize('status', ['met', 'not_met'])
def test_verified_availability_or_unavailability_needs_no_browser(status):
    contract, result = availability_result()
    text = 'Ljudmilla has a slot on 2026-11-17 at 14:00.' if status == 'met' else 'Ljudmilla has no slots on 2026-11-17.'
    result.sources[0]['text'] = text
    answer = GroundedAnswer.model_validate({'findings': [{'name': 'Ljudmilla', 'source_url': URL,
        'checks': [{'requirement_id': 'availability', 'requirement': 'Tuesday slots', 'status': status,
                    'basis': 'confirmed', 'statement': text, 'evidence': [{'source_url': URL, 'quote': text}]}]}]})
    assert unresolved_live_requirements(contract, answer, result.sources) == []
    result.sources[0]['retrieved_at'] = '2020-01-01T00:00:00+00:00'
    assert unresolved_live_requirements(contract, answer, result.sources)


def setup_run(tmp_path, monkeypatch, message=REQUEST, enabled=True, force_browser=False):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'routing.sqlite3'))
    monkeypatch.setenv('BROWSER_ENABLED', str(enabled).lower())
    runs = RunStore()
    chat = ChatStore().create('owner')
    run = runs.create('owner', chat['id'], 'request', message, 'auto', timezone='Europe/Tallinn', force_browser=force_browser)
    with runs.connect() as db:
        db.execute('UPDATE runs SET created_at=? WHERE id=?', ('2026-11-14T22:48:10+00:00', run['id']))
    runs.claim()
    calls = []
    def browse(run_id, row, message, context, debug, store, **options):
        calls.append({'message': message, 'context': context, 'options': options, 'debug': debug})
        BrowserStore(store).start(row['owner'], run_id)
        store.checkpoint(run_id, 'browser_done', debug, {'reply': 'Calendar checked', 'sources': []})
        store.finish(run_id, 'completed', 'browser_done')
    monkeypatch.setattr(worker, 'execute_browser', browse)
    return runs, run, calls


def test_explicit_browser_bypasses_classifier_failure_and_search(tmp_path, monkeypatch):
    runs, run, calls = setup_run(tmp_path, monkeypatch, REQUEST + ' browser')
    monkeypatch.setattr(worker, 'decide', lambda *args: pytest.fail('Explicit browser choice must bypass classification'))
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: pytest.fail('Must not research first'))
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert saved['debug']['router'] == 'explicit_browser'
    assert saved['phase'] == 'browser_done'
    assert len(calls) == 1 and '2026-11-17' in calls[0]['message']


@pytest.mark.parametrize('message', ACTION_REQUESTS)
def test_action_requests_bypass_model_classification_and_research(tmp_path, monkeypatch, message):
    runs, run, calls = setup_run(tmp_path, monkeypatch, message)
    monkeypatch.setattr(worker, 'decide', lambda *args: pytest.fail('Action requests must bypass classification'))
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: pytest.fail('Action requests must bypass research'))
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert saved['debug']['router'] == 'browser_action'
    assert saved['debug']['decision']['workflow'] == 'browser'
    assert saved['phase'] == 'browser_done'
    assert len(calls) == 1


def test_order_request_remains_browser_when_browser_is_unavailable(tmp_path, monkeypatch):
    runs, run, calls = setup_run(tmp_path, monkeypatch, 'Order a pizza', enabled=False)
    monkeypatch.setattr(worker, 'decide', lambda *args: pytest.fail('Action requests must bypass classification'))
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: pytest.fail('Cannot replace ordering with research'))
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert saved['debug']['decision']['workflow'] == 'browser'
    assert saved['phase'] == 'browser_unavailable'
    assert saved['status'] == 'partial'
    assert not calls


def test_action_request_respects_explicit_browser_opt_out(tmp_path, monkeypatch):
    runs, run, calls = setup_run(tmp_path, monkeypatch, 'Order a pizza without using a browser')
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='browser', confidence=1))
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: TurnResult(reply='Browser unavailable', sources=[], debug={}))
    worker.execute(run['id'])
    assert not calls
    assert runs.get('owner', run['id'])['debug']['router'] == 'browser_opt_out'


def test_research_hands_unanswered_availability_to_browser_once(tmp_path, monkeypatch):
    runs, run, calls = setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='research', confidence=1))
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: availability_result()[1])
    session = object()
    worker.execute(run['id'], browser_session=session)
    saved = runs.get('owner', run['id'])
    assert len(calls) == 1
    assert calls[0]['options']['session'] is session
    assert URL in calls[0]['context'] and 'does not authorize booking' in calls[0]['context']
    assert '2026-11-17' in calls[0]['message']
    assert saved['phase'] == 'browser_done' and saved['result']['reply'] == 'Calendar checked'
    assert saved['debug']['research_decision']['workflow'] == 'research'
    assert saved['debug']['decision']['workflow'] == 'browser'
    assert saved['deadline_at'] is not None and saved['live_output'] is None
    with pytest.raises(ValueError, match='cannot be continued|Browser runs'):
        runs.create('owner', run['chat_id'], 'continue', REQUEST, 'deep', run['id'])


@pytest.mark.parametrize('enabled,opt_out,artifact', [(False, False, 'answer'), (True, True, 'answer'),
                                                     (True, False, 'message_draft')])
def test_fallback_respects_disabled_browser_opt_out_and_drafts(tmp_path, monkeypatch, enabled, opt_out, artifact):
    message = REQUEST + (' without using a browser' if opt_out else '')
    runs, run, calls = setup_run(tmp_path, monkeypatch, message, enabled)
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='browser' if opt_out else 'research', confidence=1))
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: availability_result(artifact)[1])
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert calls == [] and saved['phase'] == 'finished'
    if artifact == 'answer':
        assert saved['status'] == 'partial'


def test_failed_search_falls_back_to_browser(tmp_path, monkeypatch):
    runs, run, calls = setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='research', confidence=1))
    def unavailable(*args, **kwargs):
        raise SearchUnavailable('Search unavailable')
    monkeypatch.setattr(worker, 'run_turn', unavailable)
    worker.execute(run['id'])
    assert len(calls) == 1
    assert runs.get('owner', run['id'])['debug']['browser_handoff']['reason'] == 'retrieval_unavailable'


@pytest.mark.parametrize('message', [REQUEST, REQUEST + ' browser', 'Add a laptop to my basket'])
def test_unchecked_browser_allows_automatic_and_explicit_routing(tmp_path, monkeypatch, message):
    runs, run, calls = setup_run(tmp_path, monkeypatch, message)
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='browser', confidence=1))
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: pytest.fail('Must route directly to browser'))
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert len(calls) == 1
    assert saved['debug']['decision']['workflow'] == 'browser'
    assert saved['phase'] == 'browser_done'


def test_checked_browser_forces_browser_without_classification(tmp_path, monkeypatch):
    runs, run, calls = setup_run(tmp_path, monkeypatch, 'Find laptop prices', force_browser=True)
    monkeypatch.setattr(worker, 'decide', lambda *args: pytest.fail('Debug override must bypass classification'))
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: pytest.fail('Debug override must bypass research'))
    worker.execute(run['id'])
    assert len(calls) == 1
    assert runs.get('owner', run['id'])['debug']['router'] == 'forced_browser'


@pytest.mark.parametrize('force_browser', [False, True])
def test_explicit_opt_out_blocks_search_failure_handoff(tmp_path, monkeypatch, force_browser):
    runs, run, calls = setup_run(tmp_path, monkeypatch, REQUEST + ' without a browser', force_browser=force_browser)
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='research', confidence=1))
    def unavailable(*args, **kwargs):
        raise SearchUnavailable('Search unavailable')
    monkeypatch.setattr(worker, 'run_turn', unavailable)
    worker.execute(run['id'])
    assert not calls
    assert runs.get('owner', run['id'])['status'] == 'failed'


@pytest.mark.parametrize('message,expected', [
    (REQUEST, 'browser'),
    ('Check appointment slots for tomorrow', 'browser'),
    ('Please sign in to my account', 'browser'),
    ('Can you fill the delivery form?', 'browser'),
    ('Reserve a table at the restaurant', 'browser'),
    ('Find the cheapest laptop and add it to my basket', 'browser'),
    ('Find laptop prices and availability', 'research'),
    ('Compare opening hours of nearby salons', 'research'),
    ('Read https://example.com', 'research'),
    ('Check it again', 'research'),
    ('Draft an email to check appointment slots', 'research'),
    ('How do I check appointment slots?', 'research'),
    ('Do not sign in to my account', 'research'),
    ('Check appointment slots without a browser', 'research'),
    ('Read this text: "check appointment slots"', 'research'),
    ("Read this text: 'check appointment slots'", 'research'),
    ('There are no appointment slots to check', 'research'),
    ('Fill the gaps in this summary', 'research'),
])
def test_fast_routing_rules(message, expected):
    assert uncertain_workflow(message)[0] == expected


@pytest.mark.parametrize('fallback', [False, True])
@pytest.mark.parametrize('message,expected', [(REQUEST, 'browser'), ('Find laptop prices', 'research')])
def test_uncertainty_uses_local_rules_without_an_extra_model_call(tmp_path, monkeypatch, fallback, message, expected):
    from dataclasses import dataclass
    from types import SimpleNamespace

    runs, run, calls = setup_run(tmp_path, monkeypatch, message)
    uncertain = RouteDecision(workflow='browser' if expected == 'research' else 'research', confidence=0.5)
    def decide(*args):
        if fallback:
            raise DecisionUnavailable('Offline')
        raise DecisionUncertain(uncertain)
    monkeypatch.setattr(worker, 'decide', decide)
    monkeypatch.setattr(worker, 'api_key', lambda: 'fixture-key')
    monkeypatch.setattr(worker, 'Agent', lambda *args, **kwargs: object())
    model_calls = []
    @dataclass
    class Usage:
        input_tokens: int = 10
    def classify(*args, **kwargs):
        model_calls.append(True)
        return SimpleNamespace(output=uncertain, usage=Usage())
    monkeypatch.setattr(worker, 'call_model', classify)
    monkeypatch.setattr(worker, 'run_turn', lambda *args, **kwargs: TurnResult(reply='Found prices', sources=[], debug={}))
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert saved['debug']['router'] == 'fast_rules'
    assert saved['debug']['decision']['workflow'] == expected
    assert saved['debug']['routing_check']['model_decision']['confidence'] == 0.5
    assert len(model_calls) == int(fallback)
    assert len(calls) == int(expected == 'browser')


def test_upcoming_weekday_uses_frozen_request_date_and_timezone():
    message, context = dated_request(REQUEST, '2026-11-14T22:48:10+00:00', 'Europe/Tallinn')
    assert 'on 2026-11-17' in message
    assert context['resolved_dates'] == [{'expression': 'upcoming tuesday', 'date': '2026-11-17'}]
