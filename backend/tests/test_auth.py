import pytest
from fastapi import HTTPException

from app.auth import require_api_key


def test_unicode_token_returns_auth_error(monkeypatch):
    monkeypatch.setenv('AGENT_API_TOKEN', 'a' * 40)
    with pytest.raises(HTTPException) as error:
        require_api_key('Bearer ' + 'a' * 40 + '€')
    assert error.value.status_code == 401


def test_invalid_config_returns_configuration_error(monkeypatch):
    monkeypatch.setenv('AGENT_API_TOKEN', 'a' * 40 + '€')
    with pytest.raises(HTTPException) as error:
        require_api_key('Bearer ' + 'a' * 40)
    assert error.value.status_code == 503


def test_valid_token_still_authenticates(monkeypatch):
    monkeypatch.setenv('AGENT_API_TOKEN', 'a' * 40)
    assert len(require_api_key('Bearer ' + 'a' * 40)) == 64


def test_session_validation_requires_the_configured_token(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('AGENT_API_TOKEN', 'a' * 40)
    with TestClient(app) as client:
        assert client.get('/api/session').status_code == 401
        assert client.get('/api/session', headers={'Authorization': 'Bearer ' + 'b' * 40}).status_code == 401
        response = client.get('/api/session', headers={'Authorization': 'Bearer ' + 'a' * 40})
        assert response.json() == {'authenticated': True}
