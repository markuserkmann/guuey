import json

import pytest
from fastapi.testclient import TestClient

from app.auth import require_api_key
from app.browser.store import BrowserStore
from app.main import app, get_runs, get_store
from app.store import ChatStore


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'api.sqlite3'))
    monkeypatch.setenv('BROWSER_SESSION_KEY', 'ab' * 32)
    monkeypatch.setenv('AGENT_API_TOKEN', 'a' * 40)
    get_runs.cache_clear()
    get_store.cache_clear()
    app.dependency_overrides[require_api_key] = lambda: 'owner'
    runs = get_runs()
    chat = ChatStore(str(runs.path)).create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Sign in', 'auto')
    runs.claim()
    try:
        with TestClient(app) as client:
            yield client, BrowserStore(runs), run['id']
    finally:
        app.dependency_overrides.pop(require_api_key, None)
        get_runs.cache_clear()
        get_store.cache_clear()


def test_saved_login_import_list_delete(api):
    client, store, _ = api
    response = client.post('/api/browser/sessions/import', json={'site_url': 'https://example.com', 'cookies': 'session=private-value'})
    assert response.status_code == 200
    session = response.json()
    assert session['status'] == 'unverified'
    assert session['accepted'] == 1
    assert 'private-value' not in response.text
    assert 'private-value' not in client.get('/api/browser/sessions').text
    assert store.load_session('owner', 'example.com')['cookies'][0]['value'] == 'private-value'
    assert client.delete('/api/browser/sessions/' + session['id']).status_code == 204
    assert client.get('/api/browser/sessions').json() == []


def test_browser_routes_owner_scoped(api):
    client, store, run_id = api
    session = store.save_session('other', 'example.com', {'cookies': [], 'origins': []})
    assert client.delete('/api/browser/sessions/' + session['id']).status_code == 404
    assert client.get('/api/browser/sessions').json() == []
    request = store.create_request('owner', run_id, 'approval', 'example.com', {
        'summary': 'Place order', 'button_label': 'Order', 'url': 'https://example.com/checkout', 'site': 'example.com',
    }, screenshot=b'jpeg-fixture')
    app.dependency_overrides[require_api_key] = lambda: 'other'
    path = f"/api/runs/{run_id}/browser/requests/{request['id']}"
    assert client.post(path, json={'decision': 'approve'}).status_code == 404
    assert client.get(path + '/screenshot').status_code == 404
    assert client.get(f'/api/runs/{run_id}').status_code == 404


@pytest.mark.parametrize('decision', ['approve', 'decline'])
def test_approval_response_and_screenshot(api, decision):
    client, store, run_id = api
    request = store.create_request('owner', run_id, 'approval', 'example.com', {
        'summary': 'Place order', 'button_label': 'Order', 'url': 'https://example.com/checkout', 'site': 'example.com',
    }, screenshot=b'jpeg-fixture')
    path = f"/api/runs/{run_id}/browser/requests/{request['id']}"
    screenshot = client.get(path + '/screenshot')
    assert screenshot.content == b'jpeg-fixture'
    assert screenshot.headers['content-type'] == 'image/jpeg'
    assert screenshot.headers['cache-control'] == 'no-store'
    assert client.post(path, json={'decision': decision}).status_code == 200
    assert store.take_response('owner', run_id, request['id'])['response'] == {'decision': decision}
    assert client.post(path, json={'decision': decision}).status_code == 409


def test_secret_inputs_are_not_returned_or_persisted_in_plaintext(api):
    client, store, run_id = api
    request = store.create_request('owner', run_id, 'input', 'example.com', {'reason': 'Sign in', 'fields': [
        {'id': 'password', 'label': 'Password', 'kind': 'password'},
    ]})
    path = f"/api/runs/{run_id}/browser/requests/{request['id']}"
    result = client.post(path, json={'values': {'password': 'private-value'}})
    assert result.status_code == 200
    for response in (result, client.get(f'/api/runs/{run_id}'), client.get(f'/api/runs/{run_id}/debug')):
        assert 'private-value' not in response.text
    assert 'private-value' not in json.dumps(store.runs.stream_state('owner', run_id))
    assert store.take_response('owner', run_id, request['id'])['secrets'] == {'password': 'private-value'}


def test_validation_does_not_echo_secrets(api):
    client, store, run_id = api
    request = store.create_request('owner', run_id, 'takeover', 'example.com', {'reason': 'Finish'})
    path = f"/api/runs/{run_id}/browser/requests/{request['id']}"
    for response in (
        client.post(path, json={'done': 'private-value'}),
        client.post(path, json={'values': {'password': ['private-value']}}),
        client.post('/api/browser/sessions/import', json={'site_url': 'https://example.com', 'cookies': ['private-value']}),
        client.post('/api/browser/sessions/import', content='{"cookies":"private-value"', headers={'Content-Type': 'application/json'}),
    ):
        assert response.status_code == 422
        assert 'private-value' not in response.text


def test_takeover_expiry_and_cancel(api):
    client, store, run_id = api
    request = store.create_request('owner', run_id, 'takeover', 'example.com', {'reason': 'Finish'})
    path = f"/api/runs/{run_id}/browser/requests/{request['id']}"
    assert client.post(path, json={'done': False}).status_code == 409
    with store.runs.connect() as db:
        db.execute("UPDATE browser_requests SET expires_at='2000-01-01T00:00:00+00:00'")
    assert client.post(path, json={'done': True}).status_code == 409
    assert store.get_request('owner', run_id, request['id'])['status'] == 'expired'
    request = store.create_request('owner', run_id, 'takeover', 'example.com', {'reason': 'Finish'})
    path = f"/api/runs/{run_id}/browser/requests/{request['id']}"
    assert client.post(path, json={'done': True}).status_code == 200
    assert store.take_response('owner', run_id, request['id'])['response'] == {'done': True}
    client.post(f'/api/runs/{run_id}/cancel')
    assert client.post(path, json={'done': True}).status_code == 409


def test_missing_key_disables_import_and_health_flag(api, monkeypatch):
    client, _, _ = api
    assert client.get('/api/health').json()['saved_logins_enabled'] is True
    monkeypatch.delenv('BROWSER_SESSION_KEY')
    assert client.get('/api/health').json()['saved_logins_enabled'] is False
    response = client.post('/api/browser/sessions/import', json={'site_url': 'https://example.com', 'cookies': 'a=private'})
    assert response.status_code == 503


def test_empty_import_does_not_replace_saved_session(api):
    client, store, _ = api
    client.post('/api/browser/sessions/import', json={'site_url': 'https://example.com', 'cookies': 'a=private'})
    response = client.post('/api/browser/sessions/import', json={'site_url': 'https://example.com', 'cookies': '[{"name":"x","value":"v","domain":"other.com"}]'})
    assert response.status_code == 422
    assert store.load_session('owner', 'example.com')['cookies'][0]['value'] == 'private'


def test_browser_routes_require_authentication(api):
    client, _, run_id = api
    app.dependency_overrides.pop(require_api_key)
    assert client.get('/api/browser/sessions').status_code == 401
    assert client.post('/api/browser/sessions/import', json={'site_url': 'https://example.com', 'cookies': 'a=b'}).status_code == 401
    assert client.delete('/api/browser/sessions/missing').status_code == 401
    assert client.post(f'/api/runs/{run_id}/browser/requests/missing', json={'done': True}).status_code == 401
    assert client.get(f'/api/runs/{run_id}/browser/requests/missing/screenshot').status_code == 401


def test_import_conflicts_with_live_session_and_delete_revokes_it(api):
    client, store, run_id = api
    session = store.save_session('owner', 'example.com', {'cookies': [{'name': 'sid', 'value': 'private', 'domain': '.example.com'}]})
    store.start('owner', run_id)
    lease = store.acquire_session('owner', run_id, 'example.com')[0]
    response = client.post('/api/browser/sessions/import', json={'site_url': 'https://example.com', 'cookies': 'sid=replacement'})
    assert response.status_code == 409
    assert store.load_session('owner', 'example.com')['cookies'][0]['value'] == 'private'
    assert client.delete('/api/browser/sessions/' + session['id']).status_code == 204
    with pytest.raises(ValueError):
        store.require_session(lease)


def test_action_resolution_is_authenticated_owner_scoped_and_terminal(api):
    client, store, run_id = api
    action = store.create_action('owner', run_id, 'final', 'Order', 'Order', 'https://example.com/checkout', 'hash')
    store.update_action('owner', run_id, action, 'approved')
    store.update_action('owner', run_id, action, 'submitted')
    store.update_action('owner', run_id, action, 'unverified')
    url = f'/api/browser/actions/{action}/resolve'
    body = {'decision': 'confirmed', 'evidence': 'Order 123 is in my account'}
    assert client.post(url, json=body).status_code == 409
    store.runs.finish(run_id, 'partial', 'submission_unknown')
    app.dependency_overrides[require_api_key] = lambda: 'other'
    assert client.get('/api/browser/actions').json() == []
    assert client.post(url, json=body).status_code == 404
    app.dependency_overrides[require_api_key] = lambda: 'owner'
    assert client.get('/api/browser/actions').json()[0]['id'] == action
    assert client.post(url, json={'decision': 'confirmed', 'evidence': '   '}).status_code == 409
    assert client.post(url, json=body).status_code == 204
    assert store.actions('owner', run_id)[0]['status'] == 'verified'
    assert client.get('/api/browser/actions').json() == []
    assert client.post(url, json=body).status_code == 409
    app.dependency_overrides.pop(require_api_key)
    assert client.post(url, json=body).status_code == 401
    assert client.get('/api/browser/actions').status_code == 401
