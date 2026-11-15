from types import SimpleNamespace

import pytest

from app import worker
from app.orchestration.decisions import RouteDecision
from app.runs import RunStore
from app.store import ChatStore


def test_browser_disabled_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'worker.sqlite3'))
    monkeypatch.setenv('BROWSER_ENABLED', 'false')
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='browser', confidence=1))
    chats, runs = ChatStore(), RunStore()
    chat = chats.create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Order a pizza', 'auto')
    runs.claim()
    worker.execute(run['id'])
    saved = runs.get('owner', run['id'])
    assert saved['status'] == 'partial'
    assert saved['phase'] == 'browser_unavailable'
    with pytest.raises(ValueError, match='Browser runs'):
        runs.create('owner', chat['id'], 'continue', 'Order a pizza', 'deep', run['id'])


@pytest.mark.parametrize('explicit', [False, True])
def test_enabled_browser_requires_a_browser_choice(tmp_path, monkeypatch, explicit):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'enabled.sqlite3'))
    monkeypatch.setenv('BROWSER_ENABLED', 'true')
    monkeypatch.setattr(worker, 'decide', lambda *args: RouteDecision(workflow='browser', confidence=1))
    calls = []
    def execute(run_id, row, message, context, debug, store):
        calls.append((run_id, row['owner'], message))
        store.checkpoint(run_id, 'browser_done', debug, {'reply': 'Browser result', 'sources': []})
        store.finish(run_id, 'completed', 'browser_done')
    monkeypatch.setattr(worker, 'execute_browser', execute)
    runs = RunStore()
    chat = ChatStore(str(runs.path)).create('owner')
    message = 'Open the website in a browser' if explicit else 'Open the website'
    run = runs.create('owner', chat['id'], 'request', message, 'auto')
    runs.claim()
    worker.execute(run['id'])
    assert len(calls) == int(explicit)
    assert runs.get('owner', run['id'])['phase'] == ('browser_done' if explicit else 'awaiting_browser_choice')


@pytest.mark.parametrize('deadline,should_kill', [(2000, False), (999, False), (None, True)])
def test_supervisor_uses_deadline_at(monkeypatch, tmp_path, deadline, should_kill):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'deadline.sqlite3'))
    runs = RunStore()
    chat = ChatStore(str(runs.path)).create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Sign in', 'fast')
    with runs.connect() as db:
        db.execute('UPDATE runs SET deadline_at=? WHERE id=?', (deadline, run['id']))
    events = []
    process = SimpleNamespace(kill=lambda: events.append('kill'), wait=lambda: None, poll=lambda: None)
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args: process)
    monkeypatch.setattr(worker.time, 'time', lambda: 1000)
    clock = [0]
    monkeypatch.setattr(worker.time, 'monotonic', lambda: clock[0])
    sleeps = []

    def stop_after_check(_):
        sleeps.append(True)
        clock[0] = 1000
        if len(sleeps) == 2:
            assert ('kill' in events) is should_kill
            raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, 'sleep', stop_after_check)
    with pytest.raises(KeyboardInterrupt):
        worker.supervise()


@pytest.mark.parametrize('exit_cleanly', [True, False])
def test_cancelled_browser_gets_cleanup_window_before_forced_stop(monkeypatch, tmp_path, exit_cleanly):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'cleanup.sqlite3'))
    runs = RunStore()
    chat = ChatStore(str(runs.path)).create('owner')
    run = runs.create('owner', chat['id'], 'request', 'Sign in', 'fast')
    with runs.connect() as db:
        db.execute('UPDATE runs SET deadline_at=2000 WHERE id=?', (run['id'],))
    clock = [0]
    exited = [False]
    events = []
    process = SimpleNamespace(kill=lambda: events.append('kill'), wait=lambda: None,
                              poll=lambda: 0 if exited[0] else None)
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args: process)
    monkeypatch.setattr(worker.time, 'time', lambda: 1000)
    monkeypatch.setattr(worker.time, 'monotonic', lambda: clock[0])
    loops = []
    def step(_):
        loops.append(True)
        if len(loops) == 1:
            runs.finish(run['id'], 'cancelled', 'cancelled')
        elif len(loops) == 2:
            assert events == []
            exited[0] = exit_cleanly
            clock[0] = 11
        elif len(loops) == 3:
            assert events == []
            clock[0] = 13
        elif len(loops) == 4:
            assert events == ([] if exit_cleanly else ['kill'])
            raise KeyboardInterrupt
    monkeypatch.setattr(worker.time, 'sleep', step)
    with pytest.raises(KeyboardInterrupt):
        worker.supervise()


@pytest.mark.parametrize('worker_crash', [False, True])
def test_supervisor_tracks_warm_followup_and_releases_dead_worker(monkeypatch, tmp_path, worker_crash):
    monkeypatch.setenv('CHAT_DATABASE_PATH', str(tmp_path / 'warm-supervisor.sqlite3'))
    runs = RunStore()
    chat = ChatStore().create('owner')['id']
    first = runs.create('owner', chat, 'first', 'Open page', 'auto')['id']
    clock, loops, killed, followup = [0], [], [], []
    exited = [False]
    process = SimpleNamespace(kill=lambda: killed.append(True), wait=lambda: None,
                              poll=lambda: 1 if exited[0] else None)
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args: process)
    monkeypatch.setattr(worker.time, 'time', lambda: 1000)
    monkeypatch.setattr(worker.time, 'monotonic', lambda: clock[0])

    def step(_):
        loops.append(True)
        if len(loops) == 1:
            with runs.connect() as db:
                db.execute('UPDATE runs SET deadline_at=999 WHERE id=?', (first,))
            runs.checkpoint(first, 'browser_done', {}, {'reply': 'Done', 'sources': []})
            runs.finish(first, 'completed', 'browser_done', warm_token=first)
            clock[0] = 1000
        elif len(loops) == 2:
            assert not killed
            second = runs.create('owner', chat, 'second', 'Continue', 'fast')['id']
            assert runs.claim_warm_browser('owner', chat, first)['id'] == second
            followup.append(second)
            with runs.connect() as db:
                db.execute('UPDATE runs SET deadline_at=2000 WHERE id=?', (second,))
            exited[0] = worker_crash
        elif len(loops) == 3:
            assert not killed
            if worker_crash:
                assert runs.get('owner', followup[0])['phase'] == 'worker_exit'
                assert runs.warm_browser_expiry('owner', chat, first) is None
                raise KeyboardInterrupt
            runs.finish(followup[0], 'cancelled', 'cancelled')
        elif len(loops) == 4:
            assert not killed
            clock[0] += 13
        elif len(loops) == 5:
            assert killed == [True]
            assert runs.get('owner', first)['status'] == 'completed'
            assert runs.get('owner', followup[0])['status'] == 'cancelled'
            raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, 'sleep', step)
    with pytest.raises(KeyboardInterrupt):
        worker.supervise()
