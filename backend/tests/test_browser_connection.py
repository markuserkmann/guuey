import asyncio
import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.browser import connection, driver
from app.browser.config import BrowserSettings, BrowserUnavailable


@pytest.fixture(params=['playwright', 'patchright'])
def driver_name(request):
    return request.param


@pytest.fixture(params=driver.BROWSER_ERRORS, ids=['playwright', 'patchright'])
def error_type(request):
    return request.param


def test_oxylabs_credentials_stay_out_of_url():
    settings = BrowserSettings(username='user@example', password='secret:/?#', country='us')
    assert connection.endpoint_url(settings) == 'wss://hb.oxylabs.io/?page_handler=false&p_cc=US'
    token = connection.connection_headers(settings)['Authorization'][6:]
    assert base64.urlsafe_b64decode(token).decode() == 'user@example:secret:/?#'
    assert '+' not in token and '/' not in token


@pytest.mark.parametrize('message,retry', [('429 Too many requests', True), ('503 Unavailable', True),
    ('CDP_SESSION_IN_USE', True), ('Timeout', True), ('401 Unauthorized 503', False), ('400 Bad request', False), ('403 Forbidden', False),
    ('WebSocket error: 429', True), ('501 not implemented', True), ('502 bad gateway', True),
    ('CDP_BROWSER_START_FAILED_0', True), ('CDP_BROWSER_GONE', True),
    ('Target page, context or browser has been closed', True), ('Connection timed out', True),
    ('409 profile is already in use', False), ('CDP_BAD_REQUEST', False), ('1337 Invalid target', False)])
def test_connection_retry_classification(monkeypatch, message, retry, error_type):
    connect = AsyncMock(side_effect=[error_type(message), 'browser'])
    pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    sleep = AsyncMock()
    monkeypatch.setattr(connection.asyncio, 'sleep', sleep)
    settings = BrowserSettings(username='user', password='password')
    if retry:
        assert asyncio.run(connection.connect(pw, settings)) == 'browser'
        assert connect.await_count == 2
    else:
        with pytest.raises(BrowserUnavailable):
            asyncio.run(connection.connect(pw, settings))
        assert connect.await_count == 1
    assert sleep.await_count == int(retry)


def test_connection_error_hides_header_and_password(error_type):
    settings = BrowserSettings(username='user', password='private-password')
    header = connection.connection_headers(settings)['Authorization']
    pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(side_effect=error_type('401 ' + header + ' private-password'))))
    with pytest.raises(BrowserUnavailable) as exc:
        asyncio.run(connection.connect(pw, settings))
    assert header not in str(exc.value)
    assert 'private-password' not in str(exc.value)


def test_connection_retries_are_bounded_with_backoff(monkeypatch, error_type):
    connect = AsyncMock(side_effect=error_type('503 queue timeout'))
    pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    sleep = AsyncMock()
    monkeypatch.setattr(connection.asyncio, 'sleep', sleep)
    monkeypatch.setattr(connection.random, 'random', lambda: 0.5)
    with pytest.raises(BrowserUnavailable):
        asyncio.run(connection.connect(pw, BrowserSettings(username='user', password='password')))
    assert connect.await_count == 6
    assert [call.args[0] for call in sleep.await_args_list] == [1.125, 2.125, 4.125, 8.125, 16.125]
    assert all(call.kwargs['timeout'] == 60_000 for call in connect.await_args_list)


def test_remote_session_uses_default_context_and_closes(monkeypatch, driver_name):
    page = SimpleNamespace(set_default_timeout=Mock(), set_default_navigation_timeout=Mock(), on=Mock())
    cdp = SimpleNamespace(send=AsyncMock(return_value={'value': 'private-live-id'}), detach=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), route=AsyncMock(), new_cdp_session=AsyncMock(return_value=cdp))
    browser = SimpleNamespace(contexts=[context], close=AsyncMock())
    pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser), launch=AsyncMock()))
    @asynccontextmanager
    async def playwright():
        yield pw
    monkeypatch.setattr(driver, 'async_' + driver_name, playwright)
    async def flow():
        with pytest.raises(RuntimeError, match='fixture'):
            async with connection.open_browser(BrowserSettings(driver=driver_name, username='user', password='password')) as (actual, live):
                assert actual is page
                assert live == 'https://hb.oxylabs.io/novnc/?id=private-live-id'
                raise RuntimeError('fixture')
    asyncio.run(flow())
    browser.close.assert_awaited_once()
    pw.chromium.launch.assert_not_called()
    assert pw.chromium.connect_over_cdp.await_args.kwargs['headers']['Authorization'].startswith('Basic ')


def test_missing_remote_credentials_never_launch_local(monkeypatch, driver_name):
    pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(), launch=AsyncMock()))
    @asynccontextmanager
    async def playwright():
        yield pw
    monkeypatch.setattr(driver, 'async_' + driver_name, playwright)
    async def flow():
        async with connection.open_browser(BrowserSettings(driver=driver_name)):
            pytest.fail('Browser must not open')
    with pytest.raises(BrowserUnavailable):
        asyncio.run(flow())
    pw.chromium.launch.assert_not_called()
    pw.chromium.connect_over_cdp.assert_not_called()
