import json
from urllib.parse import urlsplit

from app.browser.config import BrowserUnavailable
from app.browser.connection import open_browser
from app.browser.driver import BROWSER_ERRORS
from app.browser.cookies import filter_storage_state
from app.browser.guard import check_url, site_of
from app.browser.observe import observe


LOGIN_FIELDS = 'input[type=password]:visible, input[autocomplete=one-time-code]:visible'


def login_url(url, site):
    check_url(url)
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or site_of(url) != site:
        raise ValueError('Authenticated browsing requires HTTPS on the login site')


def has_state(state):
    return bool(state['cookies'] or any(origin['localStorage'] for origin in state['origins']))


async def restore_state(page, state, *, apply_current=False):
    await page.context.add_cookies(state['cookies'])
    script = '''(() => {
        const origins = %s;
        for (const entry of origins) {
            if (location.origin !== entry.origin) continue;
            if (sessionStorage.getItem('__guuey_login_restored')) continue;
            for (const item of entry.localStorage) localStorage.setItem(item.name, item.value);
            sessionStorage.setItem('__guuey_login_restored', '1');
        }
    })();''' % json.dumps(state['origins'], ensure_ascii=True)
    await page.context.add_init_script(script)
    if apply_current:
        await page.evaluate('''origins => {
            for (const entry of origins) {
                if (location.origin !== entry.origin) continue;
                for (const item of entry.localStorage) localStorage.setItem(item.name, item.value);
                sessionStorage.setItem('__guuey_login_restored', '1');
            }
        }''', state['origins'])


async def verify_login(settings, state, url, quote, active):
    """Account evidence must survive a fresh session and be absent anonymously."""
    site = site_of(url)
    login_url(url, site)
    if urlsplit(url).query or urlsplit(url).fragment:
        raise ValueError('Use an account page without query parameters or fragments to verify a login')

    async def guard(request):
        try:
            await active()
        except Exception:
            raise ValueError('The browser task was stopped') from None
        if request.is_navigation_request() or request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            login_url(request.url, site)

    try:
        for authenticated in (False, True):
            await active()
            async with open_browser(settings, request_guard=guard) as (page, _):
                await page.context.clear_cookies()
                if authenticated:
                    await restore_state(page, state)
                await page.goto(url, wait_until='domcontentloaded', timeout=30_000)
                login_url(page.url, site)
                observation = await observe(page)
                present = quote in observation.text
                if not authenticated and present:
                    return None
                if authenticated:
                    if not present or await page.locator(LOGIN_FIELDS).count():
                        return None
                    fresh = filter_storage_state(await page.context.storage_state(), site)
                    return fresh if has_state(fresh) else None
    except (*BROWSER_ERRORS, BrowserUnavailable, ValueError):
        return None
