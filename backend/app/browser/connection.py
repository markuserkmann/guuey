import asyncio
import base64
import random
import re
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from urllib.parse import quote

from playwright.async_api import Error, async_playwright

from app.browser.config import BrowserUnavailable
from app.browser.guard import check_url
from app.secrets import redact


ENDPOINT = 'hb.oxylabs.io'
RETRYABLE = re.compile(
    r'\b(?:429|5\d\d)\b|CDP_SESSION_IN_USE|CDP_SESSION_RATE_LIMIT_REACHED|'
    r'CDP_MAX_CONCURRENT_SESSIONS_REACHED|CDP_MAX_PERSISTENT_SESSIONS_REACHED|'
    r'CDP_NO_BROWSERS_AVAILABLE|CDP_BROWSER_OVERWORKED|CDP_BROWSER_START_FAILED|'
    r'CDP_BROWSER_GONE|CDP_BAD_PROXY|CDP_GENERAL_ERROR|timeout|timed out|ECONNRESET|'
    r'ECONNREFUSED|ETIMEDOUT|socket hang up|target.*closed', re.I,
)
BAD_REQUEST = re.compile(r'\b(400|401|403|409)\b|CDP_BAD_REQUEST|1337.*Invalid target', re.I)


@dataclass
class BrowserResources:
    load_images: bool = False

    def blocks(self, resource_type):
        return resource_type in {'media', 'font'} or (resource_type == 'image' and not self.load_images)


def endpoint_url(settings):
    query = 'page_handler=false'
    if settings.country:
        if not re.fullmatch('[A-Za-z]{2}', settings.country):
            raise BrowserUnavailable('BROWSER_COUNTRY must be a two-letter country code.')
        query += '&p_cc=' + settings.country.upper()
    return f'wss://{ENDPOINT}/?{query}'


def connection_headers(settings):
    if not settings.username or not settings.password:
        raise BrowserUnavailable('Configure OXY_AB_USERNAME and OXY_AB_PASSWORD to open the browser.')
    if ':' in settings.username:
        raise BrowserUnavailable('The browser username must not contain a colon.')
    token = base64.urlsafe_b64encode(f'{settings.username}:{settings.password}'.encode()).decode('ascii')
    return {'Authorization': 'Basic ' + token}


async def connect(pw, settings):
    url = endpoint_url(settings)
    headers = connection_headers(settings)
    for attempt in range(6):
        try:
            return await pw.chromium.connect_over_cdp(url, headers=headers, timeout=60_000)
        except Error as exc:
            message = str(exc)
            if attempt == 5 or BAD_REQUEST.search(message) or not RETRYABLE.search(message):
                message = message.replace(headers['Authorization'], '[redacted]').replace(headers['Authorization'][6:], '[redacted]')
                raise BrowserUnavailable(redact(message).replace(settings.password, '[redacted]')) from None
            await asyncio.sleep(min(60, 2 ** attempt + random.random() * 0.25))


@asynccontextmanager
async def open_browser(settings, on_network_error=None, request_guard=None, *, resources=None):
    resources = resources if resources is not None else BrowserResources()
    async with async_playwright() as pw:
        browser = None
        try:
            if settings.backend == 'local':
                browser = await pw.chromium.launch(headless=settings.headless)
                context = await browser.new_context()
            else:
                browser = await connect(pw, settings)
                if not browser.contexts:
                    raise BrowserUnavailable('The browser provider did not supply a browser context.')
                context = browser.contexts[0]
            page = await context.new_page()
            page.set_default_timeout(15_000)
            page.set_default_navigation_timeout(30_000)

            async def route_request(route):
                try:
                    check_url(route.request.url)
                    if request_guard:
                        await request_guard(route.request)
                except ValueError:
                    await route.abort()
                    return
                if resources.blocks(route.request.resource_type):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route('**/*', route_request)

            def response_received(response):
                description = response.headers.get('x-error-description')
                if description and on_network_error:
                    on_network_error({'status': response.status, 'description': redact(description)})

            page.on('response', response_received)
            live_view = None
            if settings.backend == 'oxylabs':
                cdp = await context.new_cdp_session(page)
                try:
                    live_id = (await cdp.send('__session_id'))['value']
                    live_view = 'https://hb.oxylabs.io/novnc/?id=' + quote(str(live_id), safe='')
                finally:
                    await cdp.detach()
            yield page, live_view
        except Error as exc:
            raise BrowserUnavailable(redact(str(exc))) from None
        finally:
            if browser is not None:
                with suppress(Error, TimeoutError):
                    async with asyncio.timeout(10):
                        await browser.close()
