from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import transcription
from app.auth import SESSION_COOKIE
from app.accounts import get_accounts
from app.main import app


@pytest.fixture
def voice_api(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_API_TOKEN', 'a' * 40)
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'accounts.sqlite3'))
    monkeypatch.delenv('GEMINI_TRANSCRIPTION_MODEL', raising=False)
    monkeypatch.delenv('GEMINI_TRANSCRIPTION_LANGUAGE', raising=False)
    monkeypatch.setattr(transcription, 'api_key', lambda: 'server-secret')
    calls = []

    class Client:
        aio = property(lambda self: self)
        auth_tokens = property(lambda self: self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **options):
            calls.append(options['config'])
            return SimpleNamespace(name='auth_tokens/single-use')

    monkeypatch.setattr(transcription.genai, 'Client', lambda **kwargs: Client())
    with TestClient(app) as client:
        yield client, calls


def request(client, body=None):
    return client.post('/api/voice/token', json=body or {}, headers={'Authorization': 'Bearer ' + 'a' * 40})


def test_session_is_single_use_and_constrained_to_dictation(voice_api):
    client, calls = voice_api
    response = request(client)
    assert response.status_code == 201
    assert response.headers['cache-control'] == 'no-store'
    session = response.json()['session']
    assert session['token'] == 'auth_tokens/single-use'
    assert 'server-secret' not in response.text
    assert session['model'] == 'gemini-3.5-transcribe-live'
    assert session['apiVersion'] == 'v1alpha'
    assert session['languageCode'] == 'en-US'
    assert session['responseModalities'] == ['TEXT']
    assert datetime.fromisoformat(session['expiresAt']) - datetime.fromisoformat(session['connectBefore']) == timedelta(minutes=4)
    token = calls[0]
    assert token.uses == 1
    assert token.expire_time - token.new_session_expire_time == timedelta(minutes=4)
    assert token.live_connect_constraints.model == session['model']
    config = token.live_connect_constraints.config
    assert config.input_audio_transcription.language_codes == ['en-US']
    assert config.realtime_input_config.automatic_activity_detection.disabled
    assert 'Do not answer' in str(config.system_instruction)


@pytest.mark.parametrize('language,code', [('en', 'en-US'), ('fi', 'fi-FI'), ('auto', None)])
def test_language_hint_matches_the_source_app(voice_api, language, code):
    client, calls = voice_api
    result = request(client, {'language': language})
    assert result.status_code == 201
    assert result.json()['session']['languageCode'] == code
    assert calls[0].live_connect_constraints.config.input_audio_transcription.language_codes == ([code] if code else None)


def test_server_can_choose_the_default_language_and_native_audio_model(voice_api, monkeypatch):
    client, calls = voice_api
    monkeypatch.setenv('GEMINI_TRANSCRIPTION_LANGUAGE', 'fi-FI')
    monkeypatch.setenv('GEMINI_TRANSCRIPTION_MODEL', 'gemini-2.5-flash-native-audio-latest')
    result = request(client).json()['session']
    assert result['languageCode'] == 'fi-FI'
    assert result['responseModalities'] == ['AUDIO']
    assert calls[0].live_connect_constraints.config.response_modalities == ['AUDIO']


def test_unauthorized_and_invalid_requests_never_issue_tokens(voice_api):
    client, calls = voice_api
    assert client.post('/api/voice/token', json={}).status_code == 401
    assert client.get('/api/voice').status_code == 401
    assert request(client, {'language': 'unknown'}).status_code == 422
    assert request(client, {'model': 'another-model'}).status_code == 422
    assert calls == []


def test_account_cookie_requires_same_origin_header_and_matching_user(voice_api):
    client, calls = voice_api
    store = get_accounts()
    user, magic = store.create_user('voice-user')
    _, cookie = store.login(magic_token=magic)
    client.cookies.set(SESSION_COOKIE, cookie)
    assert client.post('/api/voice/token', json={}).status_code == 403
    headers = {'X-Guuey-Request': '1', 'X-Guuey-User': 'another-user'}
    assert client.post('/api/voice/token', json={}, headers=headers).status_code == 401
    assert calls == []
    headers['X-Guuey-User'] = user['id']
    assert client.post('/api/voice/token', json={}, headers=headers).status_code == 201
    assert len(calls) == 1


def test_missing_key_and_provider_failures_keep_credentials_private(voice_api, monkeypatch):
    client, calls = voice_api
    monkeypatch.setattr(transcription, 'api_key', lambda: None)
    assert request(client).status_code == 503
    status = client.get('/api/voice', headers={'Authorization': 'Bearer ' + 'a' * 40})
    assert status.json() == {'available': False, 'model': None}
    assert calls == []
    monkeypatch.setattr(transcription, 'api_key', lambda: 'server-secret')

    def fail(**_kwargs):
        raise RuntimeError('server-secret must not reach the browser')

    monkeypatch.setattr(transcription.genai, 'Client', fail)
    result = request(client)
    assert result.status_code == 502
    assert 'server-secret' not in result.text
