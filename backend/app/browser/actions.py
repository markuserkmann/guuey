import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import Error, TimeoutError as PlaywrightTimeout

from app.browser.config import BrowserSettings, BrowserUnavailable
from app.browser.connection import BrowserResources
from app.browser.consent import dismiss_cookie_dialog
from app.browser.guard import check_url, is_card_field, is_final_control, secret_allowed, site_of
from app.browser.observe import Observation, observe, reframe, scrub
from app.browser.schemas import FormChoice, InputPayload
from app.browser.store import BrowserStore, SessionConflict, SessionLease
from app.browser.cookies import filter_storage_state
from app.browser.sessions import LOGIN_FIELDS, has_state, login_url, restore_state, verify_login
from app.browser.progress import BrowserProgress
from app.browser.refs import ELEMENT_REF
from app.browser.navigation import navigation_key, page_urls


class BrowserStop(Exception):
    def __init__(self, outcome, phase, summary):
        super().__init__(summary)
        self.outcome, self.phase, self.summary = outcome, phase, summary


def interaction_error(error):
    message = str(error)
    if 'intercepts pointer events' in message:
        code = 'blocked_by_overlay'
    elif 'strict mode violation' in message:
        code = 'ambiguous'
    elif 'not visible' in message or 'element is hidden' in message:
        code = 'not_visible'
    elif 'detached' in message or 'not attached' in message or 'No element found' in message:
        code = 'stale_ref'
    else:
        code = 'timeout' if isinstance(error, PlaywrightTimeout) else 'navigation_failed'
    reason = next((line.strip() for line in message.splitlines()
                   if 'intercepts pointer events' in line), message.splitlines()[0] if message else code)
    return code, reason


CONTROL_ATTRIBUTES = r'''element => {
    const tag = element.tagName.toLowerCase();
    const type = element.type || '';
    const role = element.getAttribute('role') || ({button: 'button', a: 'link', textarea: 'textbox',
        select: 'combobox', option: 'option'}[tag]) || (tag === 'input'
        ? ({checkbox: 'checkbox', radio: 'radio', search: 'searchbox', submit: 'button', button: 'button'}[type] || 'textbox') : 'generic');
    const label = element.getAttribute('aria-label') ||
        (element.getAttribute('aria-labelledby') || '').split(/\s+/)
            .map(id => element.ownerDocument.getElementById(id)?.textContent || '').join(' ').trim() ||
        [...(element.labels || [])].map(label => label.innerText).join(' ') ||
        (tag === 'input' && ['submit', 'button', 'image'].includes(type)
            ? (element.getAttribute('alt') || element.value) : '') ||
        (['button', 'link', 'option', 'menuitem', 'radio', 'tab'].includes(role) || getComputedStyle(element).cursor === 'pointer'
            ? (element.innerText || element.textContent || '').trim() ||
                [...element.querySelectorAll('img[alt], [aria-label]')]
                    .map(child => child.getAttribute('aria-label') || child.getAttribute('alt')).join(' ') : '') ||
        element.getAttribute('placeholder') || element.getAttribute('title') || '';
    return {
    documentUrl: element.ownerDocument.URL,
    role, label, tag, type,
    visible: !!element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden',
    enabled: !element.matches(':disabled') && !element.closest('[aria-disabled="true"]'),
    search: type === 'search' || role === 'searchbox' || element.enterKeyHint === 'search' ||
        !!element.closest('[role="search"]') || /^(search|hae|etsi|sök|otsi|suche|rechercher|buscar)\b/i.test(label),
    autocomplete: element.autocomplete || '', name: element.name || '',
    id: element.id || '', placeholder: element.getAttribute('placeholder') || '',
    formAction: element.form ? (element.formAction || element.form.action) : '',
    href: element.href || '', target: element.target || '', expanded: element.getAttribute('aria-expanded'),
    hasPopup: element.getAttribute('aria-haspopup')
}}'''


@dataclass
class BrowserDeps:
    page: object
    store: BrowserStore
    owner: str
    run_id: str
    settings: BrowserSettings
    debug: dict = field(default_factory=dict)
    observation: Observation | None = None
    secrets: dict = field(default_factory=dict, repr=False)
    secret_values: list[str] = field(default_factory=list, repr=False)
    restored_sites: set[str] = field(default_factory=set)
    restored_login_site: str | None = None
    lease: SessionLease | None = field(default=None, repr=False)
    session_status: str = 'unverified'
    visited_urls: set[str] = field(default_factory=set)
    pending_action: dict | None = field(default=None, repr=False)
    stopped: BrowserStop | None = None
    timeout: asyncio.Timeout | None = None
    hard_deadline: float = field(default_factory=lambda: time.monotonic() + 3540)
    step_seconds: list[float] = field(default_factory=list)
    first_observation_at: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    resources: BrowserResources = field(default_factory=BrowserResources)
    progress: BrowserProgress = field(default_factory=BrowserProgress)
    cookie_attempts: set = field(default_factory=set, repr=False)
    recovery_used: bool = False
    overlay_failures: int = 0
    last_target: dict | None = None
    assumptions: list[dict] = field(default_factory=list)
    refresh_after_action: bool = False
    navigation_urls: set = field(default_factory=set, repr=False)
    tab_parents: dict = field(default_factory=dict, repr=False)

    def remember_urls(self, urls):
        for url in urls:
            try:
                self.navigation_urls.add(navigation_key(url))
            except ValueError:
                continue

    def remember_choice(self, choice):
        item = self.clean_data({'site': site_of(self.page.url), 'field': choice.field, 'value': choice.value})
        self.assumptions = [old for old in self.assumptions
                            if (old['site'], old['field']) != (item['site'], item['field'])]
        if choice.source == 'assumed':
            self.assumptions.append(item)
        self.assumptions = self.assumptions[-12:]

    def summary_with_assumptions(self, summary):
        if not self.assumptions:
            return summary
        choices = ['- ' + ' '.join(f"{item['field']}: {item['value']} ({item['site']})".split())
                   for item in self.assumptions]
        return summary + '\n\nAssumptions used for this check (not confirmed preferences):\n' + '\n'.join(choices)

    async def recover_or_stop(self, reason):
        view = self.store.state(self.owner, self.run_id).get('live_view_url')
        if self.pending_action or self.recovery_used or not view:
            self.stop('needs_user', 'browser_needs_user', reason + ' Completion was not confirmed.')
        self.recovery_used = True
        await self._request_takeover(reason + ' Resolve the foreground dialog in Open live view, then confirm when finished.')
        self.progress = BrowserProgress()
        self.overlay_failures = 0
        return 'Manual recovery finished. Inspect the result before making further changes.\n' + await self.observe()

    async def dismiss_cookies(self):
        if self.pending_action:
            return False
        if await dismiss_cookie_dialog(self.page, self.active, self.check_scope, self.cookie_attempts):
            self.progress = BrowserProgress()
            self.overlay_failures = 0
            self.store.add_step(self.owner, self.run_id, 'cookie_consent', 'Dismissed cookie dialog',
                                url=self.clean(self.page.url))
            await self.settle()
            return True
        return False

    def clean(self, text):
        return scrub(str(text), self.secret_values)

    def clean_data(self, value):
        if isinstance(value, str):
            return self.clean(value)
        if isinstance(value, dict):
            return {key: self.clean_data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.clean_data(item) for item in value]
        return value

    def stop(self, outcome, phase, summary):
        self.stopped = BrowserStop(outcome, phase, self.clean(summary))
        raise self.stopped

    async def active(self):
        if self.stopped:
            raise self.stopped
        state = await asyncio.to_thread(self.store.runtime, self.owner, self.run_id)
        if state['status'] != 'running':
            self.stop('needs_user', 'cancelled', 'The browser task was stopped.')
        if state['deadline_at'] is not None and time.time() >= state['deadline_at']:
            self.stop('needs_user', 'budget_exhausted', 'The browser task reached its time limit.')

        if self.lease:
            try:
                self.store.require_session(self.lease, renew=True)
            except (SessionConflict, ValueError):
                self.stop('needs_user', 'browser_needs_user', 'The saved login changed, was deleted, or is no longer available. Start a new browser task.')

    def check_scope(self, url):
        check_url(url)
        if self.lease:
            login_url(url, self.lease.site)

    async def bind_session(self, site):
        login_url(self.page.url, site)
        if self.lease:
            if self.lease.site != site:
                raise ValueError('Use a separate browser task to sign in to another site')
            await self.active()
            return None
        self.settings.encryption_key()
        self.lease, state, self.session_status = self.store.acquire_session(self.owner, self.run_id, site)
        if self.session_status != 'saved':
            self.session_status = 'unverified'
        return state

    def remember_state(self, state):
        self.secret_values.extend(cookie['value'] for cookie in state['cookies'] if len(cookie['value']) >= 8)
        self.secret_values.extend(item['value'] for origin in state['origins'] for item in origin['localStorage'] if len(item['value']) >= 8)

    async def refresh_session(self):
        if not self.lease or not self.lease.session_id:
            return
        await self.active()
        self.check_scope(self.page.url)
        state = filter_storage_state(await self.page.context.storage_state(), self.lease.site)
        if not has_state(state) or await self.page.locator(LOGIN_FIELDS).count():
            self.store.expire_session(self.owner, self.lease.session_id, lease=self.lease)
            return
        self.remember_state(state)
        self.store.save_session(self.owner, self.lease.site, state, self.session_status, lease=self.lease)

    def phase(self, name):
        self.store.runs.checkpoint(self.run_id, name, self.debug)

    async def observe(self, *, query=None, offset=0, ref=None, wait_for_frames=False):
        self.observation = await observe(self.page, self.observation, self.secret_values, query=query, offset=offset,
                                         ref=ref, wait_for_frames=wait_for_frames)
        if self.page.url.startswith(('https://', 'http://')):
            self.visited_urls.add(self.clean(self.page.url))
            self.remember_urls([self.page.url, *page_urls(self.observation.text, self.page.url)])
            if self.first_observation_at is None:
                self.first_observation_at = time.monotonic()
        return self.observation.render()

    def locator(self, ref):
        if not re.fullmatch(ELEMENT_REF, ref):
            raise ValueError('Use an element reference from the latest observation')
        if self.observation and self.observation.blocks_background and f'[ref={ref}]' not in self.observation.foreground:
            raise ValueError('This control is behind the foreground popup. Use the product or form controls '
                             'in the foreground observation, or inspect the popup before continuing.')
        return self.page.locator('aria-ref=' + ref)

    async def control(self, locator):
        attrs = await locator.evaluate(CONTROL_ATTRIBUTES, timeout=1500)
        if not attrs['visible'] or not attrs['enabled']:
            raise ValueError('The target must be visible and enabled')
        if attrs.get('documentUrl') and attrs['documentUrl'] != self.page.url:
            self.check_scope(attrs['documentUrl'])
        self.last_target = self.clean_data({'role': attrs['role'], 'name': attrs['label']})
        return {'role': attrs['role'], 'name': attrs['label'], 'attrs': attrs}

    async def settle(self):
        try:
            await self.page.wait_for_load_state('domcontentloaded', timeout=15_000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(0.5)

    async def perform(self, tool, **arguments):
        async with self.lock:
            if self.stopped:
                raise self.stopped
            await self.active()
            started = time.monotonic()
            choice = arguments.pop('choice', None)
            if choice is not None:
                choice = FormChoice.model_validate(choice)
            cached_inspect = (tool == 'inspect' and not arguments.get('refresh', True)
                              and not self.refresh_after_action
                              and self.observation is not None and self.observation.url == self.clean(self.page.url))
            labels = {
                'open': 'Opened a page', 'click': 'Clicked a control', 'fill': 'Filled form fields',
                'select': 'Selected an option', 'press': 'Pressed a key', 'scroll': 'Scrolled the page',
                'back': 'Returned to the previous page', 'wait_for_text': 'Waited for page text',
                'search': 'Searched for a website', 'submit': 'Submitted a form',
                'ask_user': 'Requested information', 'request_takeover': 'Requested browser takeover',
                'save_login': 'Checked saved login', 'confirm': 'Checked the result',
                'open_login_link': 'Opened a private sign-in link',
                'inspect': 'Inspected page text',
            }
            if tool not in labels:
                raise ValueError('Unknown browser tool')
            if self.pending_action and tool not in {'confirm', 'wait_for_text', 'scroll'}:
                self.store.add_step(self.owner, self.run_id, tool, 'Waiting for action confirmation', 'waiting')
                return 'An action is awaiting confirmation. Call confirm before making any more changes.'
            if not cached_inspect and await self.dismiss_cookies():
                observation = await self.observe()
                if tool not in {'open', 'search'}:
                    return 'Cookie dialog dismissed. Choose a control from the refreshed observation.\n' + observation
            key = self.progress.key(tool, arguments, self.observation)
            if not self.pending_action and self.progress.repeats(key):
                self.progress.blocked += 1
                self.progress.outcomes.append(True)
                self.store.add_step(self.owner, self.run_id, tool, 'Stopped a repeated browser action',
                                    'error', error_code='repeated_action')
                if self.progress.blocked >= 3 or self.progress.exhausted:
                    return await self.recover_or_stop('The browser kept repeating actions without making progress.')
                observation = await self.observe()
                return ('This action was already attempted twice with the same relevant controls. It was not repeated. '
                        'If the selected option or field already matches the task, continue to the remaining fields. '
                        'Otherwise choose a different visible control or route, or ask for help.\n' + observation)
            code, status = None, 'ok'
            self.last_target = None
            observation_id = self.observation.id if self.observation else None
            try:
                note = await getattr(self, '_' + tool)(**arguments)
            except BrowserStop:
                raise
            except SessionConflict as exc:
                self.stop('needs_user', 'browser_needs_user', str(exc))
            except (ValueError, BrowserUnavailable) as exc:
                note, code, status = self.clean(exc), 'invalid_input', 'error'
            except Error as exc:
                message = str(exc)
                if '1337' in message and 'Invalid target' in message:
                    self.stop('blocked', 'browser_blocked', 'This site is restricted on the browser provider.')
                code, reason = interaction_error(exc)
                note, status = self.clean(f'{code}: {reason}. Resolve the foreground dialog or inspect a fresh target before trying again.'), 'error'
            await self.active()
            if status == 'ok' and choice is not None and tool in {'click', 'select'}:
                self.remember_choice(choice)
                note = (note or '') + f'\nChoice recorded ({choice.source}): {choice.field}: {choice.value}'
            try:
                if cached_inspect:
                    self.observation = reframe(self.observation, query=arguments.get('query'),
                                               offset=arguments.get('offset', 0), ref=arguments.get('ref'))
                    observation = 'Reading the last observation; no page refresh.\n' + self.observation.render()
                else:
                    await self.dismiss_cookies()
                    observation = await self.observe(query=arguments.get('query') if tool == 'inspect' else None,
                                                     offset=arguments.get('offset', 0) if tool == 'inspect' else 0,
                                                     ref=arguments.get('ref') if tool == 'inspect' else None,
                                                     wait_for_frames=tool in {'open', 'back'})
            except Error:
                observation = 'The page could not be observed. Wait or stop the task.'
                status, code = 'error', code or 'navigation_failed'
            unchanged = (bool(self.observation and self.observation.unchanged)
                         or tool != 'inspect' and key is not None
                         and key == self.progress.key(tool, arguments, self.observation))
            unsuccessful = status == 'error' or (
                unchanged and tool not in {'ask_user', 'request_takeover', 'save_login', 'confirm', 'search',
                                          'inspect', 'scroll', 'wait_for_text'})
            informational = status == 'ok' and tool in {'inspect', 'scroll', 'wait_for_text', 'search'}
            if status == 'ok' and tool in {'click', 'fill', 'select', 'press', 'submit', 'open', 'back'}:
                self.refresh_after_action = True
            elif status == 'ok' and tool in {'inspect', 'wait_for_text'} and not unchanged:
                self.refresh_after_action = False
            repeated_reads = self.progress.record_read(self.observation) if tool == 'inspect' and status == 'ok' and self.observation else 0
            if repeated_reads >= 3:
                note = (note or '') + ('\nThis same excerpt has already been read repeatedly. Keep the current search '
                        'and selections. Use another query or the exact continuation offset if information is missing; '
                        'otherwise act on the visible result or report the answer. Do not restart the flow.')
            if not informational:
                self.progress.record(key, unsuccessful or tool in {'open', 'back'}, unsuccessful=unsuccessful)
                if status == 'ok':
                    self.progress.reads.clear()
            label = f"Filled form fields ({len(arguments['fields'])} fields)" if tool == 'fill' else labels[tool]
            seq = self.store.add_step(self.owner, self.run_id, tool, label, status, self.clean(self.page.url), code)
            self.step_seconds.append(time.monotonic() - started)
            browser_debug = self.debug.setdefault('browser', {})
            browser_debug['steps'] = len(self.step_seconds)
            browser_debug['assumptions'] = self.assumptions
            browser_debug['last_step'] = {'tool': tool, 'status': status, 'error_code': code,
                                          'seq': seq, 'observation_id': observation_id,
                                          'target': self.last_target,
                                          'ref': arguments.get('ref') if re.fullmatch(ELEMENT_REF, str(arguments.get('ref', ''))) else None,
                                          'elapsed_s': round(self.step_seconds[-1], 3),
                                          'cached_observation': cached_inspect,
                                          'observation_chars': len(self.observation.text) if self.observation else 0,
                                          'recent_unsuccessful_steps': sum(self.progress.outcomes)}
            browser_debug['last_step']['repeated_reads'] = repeated_reads
            if status == 'error':
                browser_debug['last_step']['detail'] = self.clean(note or '')[:500]
            history = browser_debug.setdefault('step_diagnostics', [])
            history.append(self.clean_data(browser_debug['last_step']))
            del history[:-200]
            self.phase('browser_working')
            if repeated_reads >= 5:
                return await self.recover_or_stop('The browser repeatedly read the same page excerpt without advancing the task.')
            if code == 'blocked_by_overlay':
                self.overlay_failures += 1
            elif status == 'ok' and not informational:
                self.overlay_failures = 0
            if not self.pending_action and self.overlay_failures >= 2:
                return await self.recover_or_stop('A foreground overlay is blocking browser controls. ' + self.clean(note or '')[:500])
            if not self.pending_action and self.progress.exhausted:
                return await self.recover_or_stop(
                    'Six of the last eight browser actions failed or left the relevant controls unchanged. '
                    + self.clean(note or '')[:500])
            return self.clean(note or '') + '\n' + observation

    async def _inspect(self, query='', offset=0, ref=None, refresh=True):
        if len(query) > 200 or offset < 0:
            raise ValueError('Use a query of at most 200 characters and a non-negative offset')
        if ref is not None and not re.fullmatch(ELEMENT_REF, ref):
            raise ValueError('Use an element reference from the latest observation')
        return 'Inspecting the current page. Use only references shown in this observation.'

    async def _open(self, url, load_images=False):
        self.check_scope(url)
        site = site_of(url)
        if (self.observation and self.observation.url.startswith(('https://', 'http://'))
                and site == site_of(self.observation.url) and navigation_key(url) not in self.navigation_urls):
            raise ValueError('This URL was not supplied by the user or observed on the site. '
                             'Keep the current search and selections. Use its visible controls or copy an observed link; '
                             'do not invent paths or query parameters.')
        restored = False
        if site not in self.restored_sites:
            if self.settings.saved_logins_enabled and not self.lease:
                saved = next((item for item in self.store.list_sessions(self.owner) if item['site'] == site), None)
                if saved:
                    login_url(url, site)
                    self.lease, state, self.session_status = self.store.acquire_session(self.owner, self.run_id, site)
                    if self.session_status != 'saved':
                        self.session_status = 'unverified'
                    if state and has_state(state):
                        self.remember_state(state)
                        await restore_state(self.page, state)
                        restored = True
                        self.restored_login_site = site
                        self.store.add_step(self.owner, self.run_id, 'restore_login', 'Restored saved login for ' + site)
                    elif self.lease.session_id:
                        self.store.expire_session(self.owner, self.lease.session_id, lease=self.lease)
            self.restored_sites.add(site)
        self.resources.load_images = load_images
        await self.page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        self.check_scope(self.page.url)
        if restored and await self.page.locator(LOGIN_FIELDS).count():
            self.store.expire_session(self.owner, self.lease.session_id, lease=self.lease)
            self.session_status = 'unverified'
            return 'The saved login appears expired. Ask the user to sign in.'
        if self.restored_login_site == site:
            return (f'Saved login state was restored for {site}. This alone does not prove the session is authenticated. '
                    'Check the visible account menu or account page before reporting login status. '
                    'An interaction error does not mean the user is signed out.')

    async def _click(self, ref):
        return await self._submit(ref, kind='checkout_step')

    async def _fill(self, fields):
        prepared = []
        if not 1 <= len(fields) <= 10:
            raise ValueError('Fill between 1 and 10 fields at a time')
        for item in fields:
            locator = self.locator(item.ref)
            control = await self.control(locator)
            attrs = control['attrs']
            if is_card_field(attrs):
                raise ValueError('Card fields require browser takeover. Use a saved payment method.')
            if item.secret is not None:
                secret = self.secrets.get(item.secret)
                if not secret or secret.get('kind') == 'magic_link' or time.monotonic() >= secret.get('expires_at', float('inf')) or not secret_allowed(self.page.url, secret['site']):
                    raise ValueError('That private value is not available for this site')
                if attrs['formAction'] and not secret_allowed(attrs['formAction'], secret['site']):
                    raise ValueError('The form sends private values to another site')
                value = secret['value']
            else:
                if attrs['type'] == 'password' or 'one-time-code' in attrs['autocomplete']:
                    raise ValueError('Ask for this value with ask_user and use fill(secret=...)')
                value = item.value
            prepared.append((locator, item, value))
        mismatches = 0
        for locator, item, value in prepared:
            if item.secret is not None and not secret_allowed(self.page.url, self.secrets[item.secret]['site']):
                raise ValueError('The page changed sites before the private value could be filled')
            await locator.fill(value)
            if item.secret is None and await locator.input_value() != value:
                mismatches += 1
        return f'{mismatches} fields did not retain their values.' if mismatches else 'Fields filled.'

    async def _select(self, ref, option):
        locator = self.locator(ref)
        control = await self.control(locator)
        if is_card_field(control['attrs']):
            raise ValueError('Card fields require browser takeover')
        if control['attrs']['tag'] == 'select':
            await locator.select_option(label=option)
            return
        if control['role'] != 'combobox' and control['attrs'].get('hasPopup') != 'listbox':
            raise ValueError('Use select for a select element or listbox picker; use click on other visible choices')
        if control['attrs'].get('expanded') != 'true':
            await locator.click(timeout=5000, no_wait_after=True)
        root = locator.locator('xpath=ancestor::html')
        options = root.get_by_role('option', name=option, exact=True).filter(visible=True)
        await options.first.wait_for(state='visible', timeout=3000)
        if await options.count() != 1:
            raise ValueError('More than one visible option matches. Choose a specific option from the new observation')
        selected = await options.get_attribute('aria-selected')
        if selected == 'true':
            await locator.press('Escape')
            return 'This option is already selected.'
        await self._activate(options, await self.control(options), final=False, summary='Select ' + option)
        return 'Clicked option ' + option + '. Check the displayed selection before continuing.'

    async def _press(self, key):
        if key not in {'Enter', 'Tab', 'Escape', 'ArrowDown', 'ArrowUp'}:
            raise ValueError('Unsupported key')
        if key == 'Enter':
            focused = self.page.locator(':focus')
            control = await self.control(focused)
            if control['role'] in {'button', 'link'}:
                return await self._activate(focused, control, final=False, summary='Activate the focused control')
            # Implicit Enter submission may choose a different submit control than the focused field.
            form = focused.locator('xpath=ancestor::form[1]')
            if await form.count():
                buttons = form.locator('button:not([type]), button[type=submit], input[type=submit], input[type=image]')
                if await buttons.count() != 1:
                    raise ValueError('Use submit on a visible form button instead of Enter')
                button = buttons.first
                return await self._activate(button, await self.control(button), final=False, summary='Submit the focused form')
            if (control['role'] in {'textbox', 'searchbox'} and control['attrs'].get('search')
                    and not is_card_field(control['attrs']) and not is_final_control('', self.page.url, 'submit')):
                await self.page.keyboard.press('Enter')
                await self.settle()
                return 'Search submitted. Continue from the search results on this page.'
            raise ValueError('Use click or submit on a visible control instead of Enter')
        await self.page.keyboard.press(key)

    async def _scroll(self, direction):
        if direction not in {'up', 'down'}:
            raise ValueError('Unsupported scroll direction')
        await self.page.mouse.wheel(0, 600 if direction == 'down' else -600)

    async def _back(self):
        current, url = self.page, self.page.url
        response = await current.go_back(wait_until='domcontentloaded', timeout=30_000)
        if response is None and current.url == url and current in self.tab_parents:
            parent = self.tab_parents.pop(current)
            if not parent.is_closed():
                self.page = parent
                await current.close()
                return 'Returned to the page that opened this tab, with its search and selections preserved.'

    async def _wait_for_text(self, text, seconds=10):
        if not 0 < seconds <= 10:
            raise ValueError('Wait between 0 and 10 seconds')

        async def visible(frame):
            try:
                return await frame.get_by_text(text, exact=False).first.is_visible()
            except Error:
                return False

        try:
            async with asyncio.timeout(seconds):
                while True:
                    # Re-read frames because embedded forms may attach after navigation.
                    if any(await asyncio.gather(*(visible(frame) for frame in self.page.frames))):
                        return
                    await asyncio.sleep(0.25)
        except TimeoutError:
            raise PlaywrightTimeout('The requested visible text did not appear in any page frame.') from None

    async def _search(self, query):
        if self.settings.backend == 'oxylabs':
            return 'Oxylabs discovery requires OXYLAB_KEY. Ask the user for the website URL, then open it directly.'
        from app.research import search, SearchUnavailable
        try:
            sources = await asyncio.to_thread(search, query)
        except SearchUnavailable:
            return 'Website search is unavailable. Ask the user for the site URL.'
        return '\n'.join(self.clean(source['title'] + ' · ' + source['url']) for source in sources[:5])

    async def _submit(self, ref, kind='final', summary='Submit this action'):
        locator = self.locator(ref)
        return await self._activate(locator, await self.control(locator), kind == 'final', summary)

    async def _activate(self, locator, control, final, summary):
        attrs = control['attrs']
        role = 'submit' if attrs['type'] in {'submit', 'image'} and attrs['formAction'] else control['role']
        if attrs['href']:
            self.check_scope(attrs['href'])
        if attrs['formAction']:
            self.check_scope(attrs['formAction'])
        guarded = final or is_final_control(control['name'], self.page.url, role, self.settings.final_action_patterns)
        if guarded:
            return await self.final_submit(locator, control, summary)
        if attrs.get('target') == '_blank':
            parent = self.page
            async with parent.expect_popup(timeout=10_000) as opened:
                await locator.click(timeout=5_000, no_wait_after=True)
            page = await opened.value
            await page.wait_for_url(re.compile(r'^https?://'), wait_until='domcontentloaded', timeout=15_000)
            self.check_scope(page.url)
            page.set_default_timeout(15_000)
            page.set_default_navigation_timeout(30_000)
            self.tab_parents[page] = parent
            self.page = page
            await self.settle()
            return 'Switched to the tab opened by this link. Continue here; back returns to the previous results.'
        await locator.click(timeout=5_000, no_wait_after=True)
        await self.settle()

    async def request(self, kind, payload, screenshot=None):
        await self.active()
        timeout = min(self.settings.request_timeout_s, int(self.hard_deadline - time.monotonic() - 60))
        if timeout <= 0:
            self.stop('needs_user', 'budget_exhausted', 'There is not enough browser time left to wait for a response.')
        request = self.store.create_request(self.owner, self.run_id, kind, site_of(self.page.url), payload, screenshot, timeout)
        runtime = self.store.runtime(self.owner, self.run_id)
        if self.timeout:
            self.timeout.reschedule(asyncio.get_running_loop().time() + max(0, runtime['deadline_at'] - time.time()))
        while True:
            await self.active()
            try:
                response = await asyncio.to_thread(self.store.take_response, self.owner, self.run_id, request['id'])
            except ValueError:
                # Cancellation can arrive between active() and the response transaction.
                await self.active()
                raise
            if response is not None:
                self.phase('browser_working')
                return response
            current = await asyncio.to_thread(self.store.get_request, self.owner, self.run_id, request['id'])
            if current['status'] == 'expired':
                phase = 'approval_expired' if kind == 'approval' else 'browser_needs_user'
                self.stop('needs_user', phase, 'The browser request expired before a response was received.')
            await asyncio.sleep(0.5)

    async def _ask_user(self, reason, fields):
        payload = InputPayload(reason=self.clean(reason), fields=fields)
        site = site_of(self.page.url)
        private = {item.id: item.kind for item in fields if item.kind in {'password', 'otp', 'magic_link'}}
        if private:
            await self.bind_session(site)
        result = await self.request('input', self.clean_data(payload.model_dump()))
        if private:
            self.check_scope(self.page.url)
        for name, value in result['secrets'].items():
            self.secrets[name] = {'value': value, 'site': site, 'kind': private[name], 'expires_at': time.monotonic() + 600}
            self.secret_values.append(value)
            if private[name] == 'magic_link':
                parsed = urlsplit(value)
                self.secret_values.extend(token for _, token in parse_qsl(parsed.query) if token)
                if parsed.fragment:
                    self.secret_values.append(parsed.fragment)
        notes = [f"secret '{name}' received" for name in result['secrets']]
        if result['response'].get('values'):
            notes.append(json.dumps(result['response']['values'], ensure_ascii=False))
        return '\n'.join(notes)

    async def _request_takeover(self, reason):
        if self.settings.backend == 'local' and self.settings.headless:
            self.stop('needs_user', 'browser_needs_user', 'This step needs a visible browser. Run local Chromium with BROWSER_HEADLESS=false or use the remote live view.')
        if self.settings.saved_logins_enabled:
            await self.bind_session(site_of(self.page.url))
        await self.request('takeover', {'reason': self.clean(reason)})
        return 'The user finished the browser step. Check the page before continuing.'

    async def _open_login_link(self, secret):
        value = self.secrets.pop(secret, None)
        if not value or value.get('kind') != 'magic_link' or time.monotonic() >= value['expires_at']:
            raise ValueError('Request a new private sign-in link before continuing')
        login_url(value['value'], value['site'])
        self.check_scope(value['value'])
        await self.page.goto(value['value'], wait_until='domcontentloaded', timeout=30_000)
        self.check_scope(self.page.url)
        return 'The sign-in link was opened once. Check the account page.'

    async def _save_login(self, evidence_quote):
        await self.observe()
        if not evidence_quote.strip() or evidence_quote not in self.observation.text:
            raise ValueError('Account evidence must appear on the current page')
        if await self.page.locator(LOGIN_FIELDS).count():
            raise ValueError('The sign-in form is still visible')
        site = site_of(self.page.url)
        await self.bind_session(site)
        state = filter_storage_state(await self.page.context.storage_state(), site)
        if not has_state(state):
            raise ValueError('The browser has no login state to save')
        self.remember_state(state)
        self.session_status = 'unverified'
        self.store.save_session(self.owner, site, state, self.session_status, lease=self.lease)
        fresh = await verify_login(self.settings, state, self.page.url, evidence_quote, self.active)
        await self.active()
        if fresh:
            self.remember_state(fresh)
            await self.page.context.clear_cookies()
            await restore_state(self.page, fresh, apply_current=True)
            self.session_status = 'saved'
            self.store.save_session(self.owner, site, fresh, self.session_status, lease=self.lease)
        return ('Verified and saved login for ' if fresh else 'Stored login state, but fresh authentication could not be verified for ') + site

    async def final_submit(self, locator, control, summary):
        if control['role'] not in {'button', 'link'} or not control['name']:
            raise ValueError('Final actions require a named button or link')
        await self.observe()
        url, name, before = self.page.url, control['name'], self.observation.text
        action = self.store.create_action(self.owner, self.run_id, 'final', self.clean(summary), self.clean(name), self.clean(url),
                                         hashlib.sha256(before.encode()).hexdigest())
        try:
            screenshot = await self.page.screenshot(type='jpeg', quality=60, mask=[self.page.locator('input, textarea')])
            response = await self.request('approval', {
                'summary': self.clean(summary), 'button_label': self.clean(name), 'url': self.clean(url), 'site': site_of(url),
            }, screenshot)
            if response['response']['decision'] != 'approve':
                self.store.update_action(self.owner, self.run_id, action, 'declined')
                self.stop('needs_user', 'browser_needs_user', 'The action was declined. Nothing was submitted.')
            await self.active()
            current = await self.control(locator)
            await self.observe()
            if self.page.url != url or current != control or self.observation.text != before:
                self.store.update_action(self.owner, self.run_id, action, 'declined')
                return 'The page or target changed after approval. Review the page and propose the action again.'
            self.store.update_action(self.owner, self.run_id, action, 'approved')
            self.pending_action = {'id': action, 'name': self.clean(name), 'before': before, 'failures': 0}
            # A click timeout cannot establish whether the site received the submission.
            self.store.update_action(self.owner, self.run_id, action, 'submitted')
            self.phase('browser_verifying')
            await locator.click(timeout=15_000, no_wait_after=True)
            await self.settle()
            return 'The action was submitted once. Call confirm with exact new confirmation text.'
        except BaseException:
            if self.pending_action is None:
                runtime = self.store.runtime(self.owner, self.run_id)
                if runtime['status'] == 'running':
                    actions = self.store.actions(self.owner, self.run_id)
                    current = next(item for item in actions if item['id'] == action)
                    if current['status'] in {'attempting', 'approved'}:
                        self.store.update_action(self.owner, self.run_id, action, 'expired')
            raise

    async def _confirm(self, quote):
        if self.pending_action is None:
            raise ValueError('There is no submitted action to confirm')
        await self.observe()
        action = self.pending_action
        if quote.strip() and quote in self.observation.text and quote not in action['before']:
            self.store.update_action(self.owner, self.run_id, action['id'], 'verified', self.clean(quote))
            self.pending_action = None
            self.phase('browser_working')
            return 'The page confirms the action: ' + self.clean(quote)
        action['failures'] += 1
        if action['failures'] >= 2:
            self.store.update_action(self.owner, self.run_id, action['id'], 'unverified')
            self.stop('failed', 'submission_unknown', self.unknown_submission())
        return 'Confirmation must be exact new text on the current page. Do not submit again.'

    def unknown_submission(self):
        name = self.pending_action['name'] if self.pending_action else 'the final action'
        return f'I attempted “{name}” but could not confirm the result. Check your account before trying again.'
