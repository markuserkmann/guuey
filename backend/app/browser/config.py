import os
import re
from dataclasses import dataclass, field
from typing import Literal


class BrowserUnavailable(RuntimeError):
    pass


def _boolean(name, default):
    value = os.getenv(name, '').strip().lower()
    if not value:
        return default
    if value not in {'true', 'false', '1', '0'}:
        raise ValueError(f'{name} must be true or false')
    return value in {'true', '1'}


def _positive_int(name, default, maximum):
    try:
        value = int(os.getenv(name, '').strip() or default)
    except ValueError:
        raise ValueError(f'{name} must be a positive integer') from None
    if not 1 <= value <= maximum:
        raise ValueError(f'{name} must be between 1 and {maximum}')
    return value


@dataclass(frozen=True)
class BrowserSettings:
    enabled: bool = False
    backend: Literal['oxylabs', 'local'] = 'oxylabs'
    username: str = field(default='', repr=False)
    password: str = field(default='', repr=False)
    web_api_key: str = field(default='', repr=False)
    country: str = ''
    model: str = 'gemini-3.5-flash-lite'
    session_key: str = field(default='', repr=False)
    headless: bool = True
    deadline_s: int = 1800
    max_steps: int = 120
    request_timeout_s: int = 900
    tool_retries: int = 5
    thinking_level: str = 'minimal'
    model_max_tokens: int = 8192
    model_timeout_s: int = 180
    final_action_patterns: str = ''

    @classmethod
    def from_env(cls):
        backend = os.getenv('BROWSER_BACKEND', '').strip() or 'oxylabs'
        if backend not in {'oxylabs', 'local'}:
            raise ValueError('BROWSER_BACKEND must be oxylabs or local')
        country = os.getenv('BROWSER_COUNTRY', '').strip().upper()
        if country and not re.fullmatch('[A-Z]{2}', country):
            raise ValueError('BROWSER_COUNTRY must be a two-letter country code')
        patterns = os.getenv('BROWSER_FINAL_ACTION_PATTERNS', '').strip()
        thinking = os.getenv('BROWSER_THINKING_LEVEL', '').strip().lower() or 'minimal'
        if thinking not in {'minimal', 'low', 'medium', 'high'}:
            raise ValueError('BROWSER_THINKING_LEVEL must be minimal, low, medium or high')
        if patterns:
            try:
                re.compile(patterns)
            except re.error:
                raise ValueError('BROWSER_FINAL_ACTION_PATTERNS must be a valid regular expression') from None
        return cls(
            enabled=_boolean('BROWSER_ENABLED', False), backend=backend,
            username=os.getenv('OXY_AB_USERNAME', ''), password=os.getenv('OXY_AB_PASSWORD', ''),
            web_api_key=os.getenv('OXYLAB_KEY', '') or os.getenv('OXYLABS_WEB_API_KEY', ''),
            country=country, model=os.getenv('BROWSER_MODEL', '').strip() or 'gemini-3.5-flash-lite',
            session_key=os.getenv('BROWSER_SESSION_KEY', ''), headless=_boolean('BROWSER_HEADLESS', True),
            deadline_s=_positive_int('BROWSER_DEADLINE_S', 1800, 3540),
            max_steps=_positive_int('BROWSER_MAX_STEPS', 120, 1000),
            request_timeout_s=_positive_int('BROWSER_REQUEST_TIMEOUT_S', 900, 3540),
            tool_retries=_positive_int('BROWSER_TOOL_RETRIES', 5, 20),
            thinking_level=thinking,
            model_max_tokens=_positive_int('BROWSER_MODEL_MAX_TOKENS', 8192, 65536),
            model_timeout_s=_positive_int('BROWSER_MODEL_TIMEOUT_S', 180, 600),
            final_action_patterns=patterns,
        )

    @property
    def saved_logins_enabled(self):
        return bool(re.fullmatch('[0-9a-fA-F]{64}', self.session_key))

    def encryption_key(self):
        if not self.saved_logins_enabled:
            raise BrowserUnavailable('Saved logins and private inputs require BROWSER_SESSION_KEY with 64 hex characters.')
        return bytes.fromhex(self.session_key)
