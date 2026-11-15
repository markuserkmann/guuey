import json
import time
from uuid import uuid4

from pydantic_core import to_jsonable_python

from app.store import ChatStore, now
from app.browser.store import BrowserStore, SCHEMA, extend_schema


class RunStore(ChatStore):
    def __init__(self, filename=None):
        super().__init__(filename)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, message TEXT NOT NULL, mode TEXT NOT NULL,
                    status TEXT NOT NULL, phase TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, parent_id TEXT, result TEXT, debug TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(owner, request_id), UNIQUE(parent_id)
                );
                CREATE INDEX IF NOT EXISTS runs_chat ON runs(owner, chat_id, created_at);
                CREATE TABLE IF NOT EXISTS browser_warm_reservations (
                    owner TEXT NOT NULL, chat_id TEXT NOT NULL, token TEXT NOT NULL,
                    expires_at REAL NOT NULL, PRIMARY KEY(owner, chat_id)
                );
            ''')
            db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            if 'attachments' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute("ALTER TABLE runs ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
            if 'live_output' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute("ALTER TABLE runs ADD COLUMN live_output TEXT")
            if 'timezone' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute("ALTER TABLE runs ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'")
            if 'deadline_at' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute('ALTER TABLE runs ADD COLUMN deadline_at REAL')
            if 'browser_view' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute('ALTER TABLE runs ADD COLUMN browser_view TEXT')
            if 'worker_id' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute('ALTER TABLE runs ADD COLUMN worker_id TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS runs_worker ON runs(worker_id)')
            extend_schema(db)

    def get(self, owner, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE owner=? AND id=?', (owner, run_id)).fetchone()
        return {**self.decode(row), 'browser': self.browser_state(owner, run_id)} if row else None

    def browser_state(self, owner, run_id):
        return BrowserStore(self).state(owner, run_id)

    @staticmethod
    def decode(row):
        data = dict(row)
        data['result'] = json.loads(data['result']) if data['result'] else None
        data['debug'] = json.loads(data['debug'])
        data['attachments'] = json.loads(data['attachments'])
        data['live_output'] = json.loads(data['live_output']) if data.get('live_output') else None
        data.pop('owner', None)
        data.pop('browser_view', None)
        data.pop('worker_id', None)
        return data

    def create(self, owner, chat_id, request_id, message, mode, parent_id=None, attachments=None, timezone='UTC'):
        from app.task_context import validate_timezone
        validate_timezone(timezone)
        attachments = attachments or []
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM runs WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
            if old:
                if (old['chat_id'], old['message'], old['mode'], json.loads(old['attachments']), old['timezone']) != (chat_id, message, mode, attachments, timezone):
                    raise ValueError('Request ID already used with different content')
                return self.decode(old)
            if not db.execute('SELECT id FROM chats WHERE id=? AND owner=?', (chat_id, owner)).fetchone():
                raise LookupError('Chat not found')
            if db.execute("SELECT id FROM runs WHERE chat_id=? AND status IN ('queued','running')", (chat_id,)).fetchone():
                raise ValueError('A run is already active in this chat')
            if parent_id:
                old = db.execute('SELECT * FROM runs WHERE parent_id=? AND owner=?', (parent_id, owner)).fetchone()
                if old:
                    return self.decode(old)
                parent = db.execute('SELECT * FROM runs WHERE id=? AND owner=? AND chat_id=?', (parent_id, owner, chat_id)).fetchone()
                if not parent or parent['status'] != 'partial' or parent['mode'] == 'deep':
                    raise ValueError('This run cannot be continued')
                if (parent['deadline_at'] is not None or parent['phase'].startswith('browser_')
                        or json.loads(parent['debug']).get('decision', {}).get('workflow') == 'browser'):
                    raise ValueError('Browser runs cannot be continued with Deep')
                if db.execute('SELECT id FROM runs WHERE chat_id=? AND rowid > (SELECT rowid FROM runs WHERE id=?) LIMIT 1',
                              (chat_id, parent_id)).fetchone():
                    raise ValueError('A newer turn exists; continue the current task instead')
            identifier, timestamp = str(uuid4()), now()
            db.execute('INSERT INTO runs(id,owner,chat_id,request_id,message,mode,status,phase,created_at,updated_at,parent_id,attachments) VALUES(?,?,?,?,?,?,?, ?,?,?,?,?)',
                       (identifier, owner, chat_id, request_id, message, mode, 'queued', 'queued', timestamp, timestamp, parent_id, json.dumps(attachments)))
            db.execute('UPDATE runs SET timezone=? WHERE id=?', (timezone, identifier))
        return self.get(owner, identifier)

    def claim(self, *, max_running=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if max_running is not None and db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0] >= max_running:
                return None
            row = db.execute("""SELECT runs.* FROM runs WHERE status='queued' AND NOT EXISTS (
                SELECT 1 FROM browser_warm_reservations reservation
                WHERE reservation.owner=runs.owner AND reservation.chat_id=runs.chat_id
                  AND reservation.expires_at>?
            ) ORDER BY created_at LIMIT 1""", (time.time(),)).fetchone()
            if row:
                db.execute("UPDATE runs SET status='running', phase='routing', updated_at=?,worker_id=id WHERE id=?", (now(), row['id']))
                return dict(row)
        return None

    def reserve_warm_browser(self, owner, chat_id, token, expires_at):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM browser_warm_reservations WHERE expires_at<=?', (time.time(),))
            if not db.execute('SELECT 1 FROM chats WHERE owner=? AND id=?', (owner, chat_id)).fetchone():
                return False
            cursor = db.execute('INSERT OR IGNORE INTO browser_warm_reservations VALUES (?, ?, ?, ?)',
                                (owner, chat_id, token, expires_at))
            return cursor.rowcount == 1

    def claim_warm_browser(self, owner, chat_id, token, *, max_running=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            reservation = db.execute('''SELECT 1 FROM browser_warm_reservations
                WHERE owner=? AND chat_id=? AND token=? AND expires_at>?''',
                                     (owner, chat_id, token, time.time())).fetchone()
            if not reservation:
                return None
            if max_running is not None and db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0] >= max_running:
                return None
            row = db.execute("SELECT * FROM runs WHERE owner=? AND chat_id=? AND status='queued' ORDER BY created_at LIMIT 1",
                             (owner, chat_id)).fetchone()
            if row:
                db.execute("UPDATE runs SET status='running', phase='routing', updated_at=?,worker_id=? WHERE id=?", (now(), token, row['id']))
                db.execute('DELETE FROM browser_warm_reservations WHERE owner=? AND chat_id=? AND token=?',
                           (owner, chat_id, token))
                return dict(row)
        return None

    def release_warm_browser(self, owner, chat_id, token):
        with self.connect() as db:
            db.execute('DELETE FROM browser_warm_reservations WHERE owner=? AND chat_id=? AND token=?',
                       (owner, chat_id, token))

    def warm_browser_expiry(self, owner, chat_id, token):
        with self.connect() as db:
            row = db.execute('SELECT expires_at FROM browser_warm_reservations WHERE owner=? AND chat_id=? AND token=?',
                             (owner, chat_id, token)).fetchone()
        return row['expires_at'] if row else None

    def worker_run(self, token):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE worker_id=? OR id=? ORDER BY rowid DESC LIMIT 1',
                             (token, token)).fetchone()
        return dict(row) if row else None

    def checkpoint(self, run_id, phase, debug, result=None):
        with self.connect() as db:
            db.execute("UPDATE runs SET phase=?, debug=?, result=COALESCE(?, result), updated_at=? WHERE id=? AND status='running'",
                       (phase, json.dumps(to_jsonable_python(debug)), json.dumps(to_jsonable_python(result)) if result else None, now(), run_id))

    def update_live_output(self, run_id, output):
        with self.connect() as db:
            db.execute("UPDATE runs SET live_output=?, updated_at=? WHERE id=? AND status='running'",
                       (json.dumps(output, ensure_ascii=False), now(), run_id))

    def stream_state(self, owner, run_id):
        with self.connect() as db:
            row = db.execute("SELECT id,status,phase,updated_at,live_output,result FROM runs WHERE owner=? AND id=?",
                             (owner, run_id)).fetchone()
        if row is None:
            return None
        state = dict(row)
        for key in ('live_output', 'result'):
            state[key] = json.loads(state[key]) if state[key] else None
        if state['result']:
            state['result']['sources'] = [{"title": source['title'], "url": source['url']} for source in state['result'].get('sources', [])]
        state['browser'] = self.browser_state(owner, run_id)
        return state

    def finish(self, run_id, status, phase, *, warm_token=None, warm_seconds=60):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if not row or row['status'] not in ('running', 'queued'):
                return
            db.execute('DELETE FROM browser_session_leases WHERE owner=? AND run_id=?', (row['owner'], run_id))
            uncertain = db.execute("SELECT summary FROM browser_actions WHERE owner=? AND run_id=? AND status IN ('submitted','unverified')",
                                   (row['owner'], run_id)).fetchall()
            db.execute("UPDATE browser_requests SET status='expired',secret_values=NULL WHERE owner=? AND run_id=? AND status IN ('pending','answered')",
                       (row['owner'], run_id))
            action_status = 'declined' if status == 'cancelled' else 'expired'
            db.execute("UPDATE browser_actions SET status=?,updated_at=? WHERE owner=? AND run_id=? AND status IN ('attempting','approved')",
                       (action_status, now(), row['owner'], run_id))
            db.execute("UPDATE browser_actions SET status='unverified',updated_at=? WHERE owner=? AND run_id=? AND status='submitted'",
                       (now(), row['owner'], run_id))
            result = json.loads(row['result']) if row['result'] else None
            if uncertain:
                result = {'reply': 'A browser submission was attempted but its result is uncertain. Check your account before trying again.',
                          'sources': [], 'browser': {'outcome': 'failed', 'actions': [dict(action) for action in db.execute(
                              'SELECT id,summary,status FROM browser_actions WHERE owner=? AND run_id=?', (row['owner'], run_id))]}}
                if status != 'cancelled':
                    status, phase = 'partial', 'submission_unknown'
            live = json.loads(row['live_output']) if row['live_output'] else None
            if not result and live and live.get('kind') == 'answer' and live.get('text'):
                result = {'reply': live['text'], 'sources': []}
                if status == 'completed':
                    status, phase = 'partial', 'incomplete_answer'
            if status == 'completed' and not result:
                status, phase = 'failed', 'missing_result'
            if warm_token and (status, phase) in {('completed', 'browser_done'), ('partial', 'browser_needs_user')}:
                db.execute('DELETE FROM browser_warm_reservations WHERE expires_at<=?', (time.time(),))
                db.execute('INSERT OR IGNORE INTO browser_warm_reservations VALUES (?, ?, ?, ?)',
                           (row['owner'], row['chat_id'], warm_token, time.time() + warm_seconds))
            if result:
                timestamp = now()
                if not row['parent_id']:
                    db.execute('INSERT INTO messages(id,chat_id,role,content,sources,created_at,attachments) VALUES(?,?,?,?,?,?,?)', (f"user:{row['request_id']}", row['chat_id'], 'user', row['message'], '[]', timestamp, row['attachments']))
                db.execute('INSERT INTO messages(id,chat_id,role,content,sources,created_at) VALUES(?,?,?,?,?,?)', (f"assistant:{row['id']}", row['chat_id'], 'assistant', result['reply'], json.dumps(result['sources']), timestamp))
                db.execute("UPDATE chats SET title=CASE WHEN title='New chat' THEN ? ELSE title END, updated_at=? WHERE id=?", (row['message'][:80], timestamp, row['chat_id']))
                db.execute('DELETE FROM chat_search WHERE chat_id=?', (row['chat_id'],))
                db.execute("INSERT INTO chat_search SELECT c.id,c.owner,c.title,COALESCE((SELECT group_concat(content, ' ') FROM messages WHERE chat_id=c.id),'') FROM chats c WHERE c.id=?", (row['chat_id'],))
            db.execute('UPDATE runs SET status=?,phase=?,updated_at=?,live_output=NULL,browser_view=NULL,result=? WHERE id=?',
                       (status, phase, now(), json.dumps(result) if result else None, run_id))

    def latest(self, owner, chat_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE owner=? AND chat_id=? ORDER BY created_at DESC LIMIT 1', (owner, chat_id)).fetchone()
        return {**self.decode(row), 'browser': self.browser_state(owner, row['id'])} if row else None
