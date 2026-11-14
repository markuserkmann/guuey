import json
from uuid import uuid4

from pydantic_core import to_jsonable_python

from app.store import ChatStore, now


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
            ''')
            db.execute('BEGIN IMMEDIATE')
            if 'attachments' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute("ALTER TABLE runs ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
            if 'live_output' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute("ALTER TABLE runs ADD COLUMN live_output TEXT")
            if 'timezone' not in {row['name'] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute("ALTER TABLE runs ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'")

    def get(self, owner, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE owner=? AND id=?', (owner, run_id)).fetchone()
        return self.decode(row) if row else None

    @staticmethod
    def decode(row):
        data = dict(row)
        data['result'] = json.loads(data['result']) if data['result'] else None
        data['debug'] = json.loads(data['debug'])
        data['attachments'] = json.loads(data['attachments'])
        data['live_output'] = json.loads(data['live_output']) if data.get('live_output') else None
        data.pop('owner', None)
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
                if db.execute('SELECT id FROM runs WHERE chat_id=? AND rowid > (SELECT rowid FROM runs WHERE id=?) LIMIT 1',
                              (chat_id, parent_id)).fetchone():
                    raise ValueError('A newer turn exists; continue the current task instead')
            identifier, timestamp = str(uuid4()), now()
            db.execute('INSERT INTO runs(id,owner,chat_id,request_id,message,mode,status,phase,created_at,updated_at,parent_id,attachments) VALUES(?,?,?,?,?,?,?, ?,?,?,?,?)',
                       (identifier, owner, chat_id, request_id, message, mode, 'queued', 'queued', timestamp, timestamp, parent_id, json.dumps(attachments)))
            db.execute('UPDATE runs SET timezone=? WHERE id=?', (timezone, identifier))
        return self.get(owner, identifier)

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM runs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE runs SET status='running', phase='routing', updated_at=? WHERE id=?", (now(), row['id']))
                return dict(row)
        return None

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
        return state

    def finish(self, run_id, status, phase):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if not row or row['status'] not in ('running', 'queued'):
                return
            result = json.loads(row['result']) if row['result'] else None
            live = json.loads(row['live_output']) if row['live_output'] else None
            if not result and live and live.get('kind') == 'answer' and live.get('text'):
                result = {'reply': live['text'], 'sources': []}
                if status == 'completed':
                    status, phase = 'partial', 'incomplete_answer'
            if status == 'completed' and not result:
                status, phase = 'failed', 'missing_result'
            if result:
                timestamp = now()
                if not row['parent_id']:
                    db.execute('INSERT INTO messages(id,chat_id,role,content,sources,created_at,attachments) VALUES(?,?,?,?,?,?,?)', (f"user:{row['request_id']}", row['chat_id'], 'user', row['message'], '[]', timestamp, row['attachments']))
                db.execute('INSERT INTO messages(id,chat_id,role,content,sources,created_at) VALUES(?,?,?,?,?,?)', (f"assistant:{row['id']}", row['chat_id'], 'assistant', result['reply'], json.dumps(result['sources']), timestamp))
                db.execute("UPDATE chats SET title=CASE WHEN title='New chat' THEN ? ELSE title END, updated_at=? WHERE id=?", (row['message'][:80], timestamp, row['chat_id']))
                db.execute('DELETE FROM chat_search WHERE chat_id=?', (row['chat_id'],))
                db.execute("INSERT INTO chat_search SELECT c.id,c.owner,c.title,COALESCE((SELECT group_concat(content, ' ') FROM messages WHERE chat_id=c.id),'') FROM chats c WHERE c.id=?", (row['chat_id'],))
            db.execute('UPDATE runs SET status=?,phase=?,updated_at=?,live_output=NULL,result=? WHERE id=?',
                       (status, phase, now(), json.dumps(result) if result else None, run_id))

    def latest(self, owner, chat_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE owner=? AND chat_id=? ORDER BY created_at DESC LIMIT 1', (owner, chat_id)).fetchone()
        return self.decode(row) if row else None
