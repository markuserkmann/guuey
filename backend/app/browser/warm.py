import asyncio

from app.browser.connection import BrowserResources
from app.browser.store import SessionConflict


class WarmBrowser:
    """One chat's connection, owned by a task on the worker's persistent event loop."""

    def __init__(self, loop, token):
        self.loop = loop
        self.token = token
        self.page = None
        self.live_view = None
        self.resources = BrowserResources()
        self.deps = None
        self.previous = None
        self.task = None
        self.stop = None
        self.ready = None

    async def acquire(self, settings, opener):
        if self.task is not None and self.page is None:
            await self.close()
        reused = self.page is not None
        if not reused:
            self.stop = asyncio.Event()
            self.ready = self.loop.create_future()
            self.task = asyncio.create_task(self._hold(settings, opener))
            await asyncio.shield(self.ready)
        return self.page, self.live_view, reused

    async def _hold(self, settings, opener):
        try:
            async with opener(settings, on_network_error=self.network_error,
                              request_guard=self.request_guard, resources=self.resources) as (page, view):
                self.page, self.live_view = page, view
                self.ready.set_result(None)
                await self.stop.wait()
        except Exception as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
            else:
                raise
        finally:
            self.page = None

    async def request_guard(self, request):
        if self.deps is None:
            raise ValueError('The browser is waiting for a task')
        from app.browser.actions import BrowserStop
        try:
            await self.deps.active()
            if request.is_navigation_request() or request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                self.deps.check_scope(request.url)
        except BrowserStop:
            raise ValueError('The browser task was stopped') from None

    def network_error(self, error):
        if self.deps:
            errors = self.deps.debug['browser'].setdefault('network_errors', [])
            if len(errors) < 10:
                errors.append(self.deps.clean_data(error))

    def attach(self, deps):
        old = self.previous
        if old:
            if (old.owner, old.store.runs.path) != (deps.owner, deps.store.runs.path):
                raise ValueError('Browser session owner changed')
            deps.secret_values = list(old.secret_values)
            deps.restored_sites = set(old.restored_sites)
            deps.navigation_urls.update(old.navigation_urls)
            deps.tab_parents.update(old.tab_parents)
            deps.restored_login_site = old.restored_login_site
            deps.assumptions = [dict(item) for item in old.assumptions]
            if old.lease:
                lease, _, status = deps.store.acquire_session(deps.owner, deps.run_id, old.lease.site)
                if status == 'expired' or (lease.session_id, lease.revision) != (old.lease.session_id, old.lease.revision):
                    deps.store.release_session(lease)
                    raise SessionConflict('The saved login changed while the browser was waiting. Start a new browser task.')
                deps.lease, deps.session_status = lease, status
            old.secret_values.clear()
        self.previous = None
        self.deps = deps

    def park(self, deps):
        if deps:
            self.page = deps.page
        self.deps = None
        self.previous = deps

    def valid(self):
        if self.page is None or self.page.is_closed():
            return False
        old = self.previous
        if old and old.lease:
            with old.store.runs.connect() as db:
                row = db.execute('SELECT id,revision,status FROM browser_sessions WHERE owner=? AND site=?',
                                 (old.owner, old.lease.site)).fetchone()
            current = (row['id'], row['revision']) if row else (None, None)
            if (row and row['status'] == 'expired') or current != (old.lease.session_id, old.lease.revision):
                return False
        return True

    async def close(self):
        self.deps = None
        if self.stop:
            self.stop.set()
        if self.task:
            if self.page is None and not self.task.done():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.previous:
            self.previous.secrets.clear()
            self.previous.secret_values.clear()
        self.previous = None
        self.page = None
        self.live_view = None
        self.task = None
        self.resources = BrowserResources()
