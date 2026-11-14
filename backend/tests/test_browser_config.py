import json
import base64

import pytest

from app.browser.config import BrowserSettings
from app.model_calls import error_details
from app.secrets import SECRET_ENV_NAMES, redact


def test_browser_defaults_and_secrets_not_in_repr(monkeypatch):
    for name in ('BROWSER_ENABLED', 'BROWSER_BACKEND', 'BROWSER_MODEL', 'BROWSER_DEADLINE_S'):
        monkeypatch.setenv(name, '')
    monkeypatch.setenv('OXY_AB_PASSWORD', 'private-password')
    monkeypatch.setenv('BROWSER_SESSION_KEY', 'ab' * 32)
    settings = BrowserSettings.from_env()
    assert settings.enabled is False
    assert settings.backend == 'oxylabs'
    assert settings.deadline_s == 1800
    assert settings.saved_logins_enabled
    assert 'private-password' not in repr(settings)
    assert 'ab' * 32 not in repr(settings)


def test_browser_reasoning_limits_can_be_overridden(monkeypatch):
    values = {'BROWSER_TOOL_RETRIES': '7', 'BROWSER_THINKING_LEVEL': 'medium',
              'BROWSER_MODEL_MAX_TOKENS': '12288', 'BROWSER_MODEL_TIMEOUT_S': '240',
              'BROWSER_MAX_STEPS': '180', 'BROWSER_DEADLINE_S': '2400'}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = BrowserSettings.from_env()
    assert (settings.tool_retries, settings.thinking_level, settings.model_max_tokens, settings.model_timeout_s) == (7, 'medium', 12288, 240)
    assert (settings.max_steps, settings.deadline_s) == (180, 2400)


@pytest.mark.parametrize('name,value', [('BROWSER_ENABLED', 'perhaps'), ('BROWSER_BACKEND', 'unknown'),
    ('BROWSER_COUNTRY', 'FIN'), ('BROWSER_MAX_STEPS', '0'), ('BROWSER_DEADLINE_S', '3600'),
    ('BROWSER_REQUEST_TIMEOUT_S', 'nan'), ('BROWSER_FINAL_ACTION_PATTERNS', '['),
    ('BROWSER_THINKING_LEVEL', 'unknown'), ('BROWSER_TOOL_RETRIES', '0'),
    ('BROWSER_TOOL_RETRIES', '21'), ('BROWSER_MODEL_MAX_TOKENS', '65537'),
    ('BROWSER_MODEL_TIMEOUT_S', '601')])
def test_invalid_settings_fail_without_echoing_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        BrowserSettings.from_env()


def test_connection_errors_redacted(monkeypatch):
    for index, name in enumerate(SECRET_ENV_NAMES):
        monkeypatch.setenv(name, f'secret-{index}-value')
    message = ' '.join(f'secret-{index}-value' for index in range(len(SECRET_ENV_NAMES)))
    assert 'secret-' not in redact(message)
    assert 'secret-' not in json.dumps(error_details(RuntimeError(message)))
    assert redact('wss://user:unknown-password@hb.oxylabs.io') == 'wss://[redacted]@hb.oxylabs.io'
    monkeypatch.setenv('OXY_AB_PASSWORD', 'password"with\\escapes')
    assert 'password' not in redact(json.dumps('password"with\\escapes'))


@pytest.mark.parametrize('name', ['OXYLAB_KEY', 'OXYLABS_WEB_API_KEY'])
def test_oxylabs_discovery_key_aliases(monkeypatch, name):
    monkeypatch.delenv('OXYLAB_KEY', raising=False)
    monkeypatch.delenv('OXYLABS_WEB_API_KEY', raising=False)
    monkeypatch.setenv(name, 'private-discovery-key')
    settings = BrowserSettings.from_env()
    assert settings.web_api_key == 'private-discovery-key'
    assert 'private-discovery-key' not in repr(settings)
    assert 'private-discovery-key' not in redact('error private-discovery-key')


def test_encoded_browser_credentials_are_redacted(monkeypatch):
    monkeypatch.setenv('OXY_AB_USERNAME', 'user')
    monkeypatch.setenv('OXY_AB_PASSWORD', '?>')
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        token = encode(b'user:?>').decode()
        assert token not in redact('Authorization: Basic ' + token)
