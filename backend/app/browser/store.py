import json
import os
import time
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass, field
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.browser.config import BrowserSettings, BrowserUnavailable
from app.browser.cookies import filter_storage_state
from app.browser.guard import site_of
from app.browser.schemas import ApprovalPayload, BrowserResponse, InputPayload, TakeoverPayload
from app.secrets import redact
from app.store import now


SCHEMA = '''
    CREATE TABLE IF NOT EXISTS browser_steps (
        run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL, at TEXT NOT NULL,
        tool TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL, url TEXT, error_code TEXT,
        PRIMARY KEY (run_id, seq)
    );
    CREATE TABLE IF NOT EXISTS browser_requests (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), owner TEXT NOT NULL,
        kind TEXT NOT NULL, site TEXT NOT NULL, payload TEXT NOT NULL, screenshot BYTEA,
        secret_values BYTEA, response TEXT, status TEXT NOT NULL,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS browser_requests_run ON browser_requests(run_id, status);
    CREATE UNIQUE INDEX IF NOT EXISTS browser_requests_active ON browser_requests(run_id)
        WHERE status IN ('pending', 'answered');
    CREATE TABLE IF NOT EXISTS browser_actions (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), owner TEXT NOT NULL,
        kind TEXT NOT NULL, summary TEXT NOT NULL, target_name TEXT NOT NULL, url TEXT NOT NULL,
        status TEXT NOT NULL, before_text_hash TEXT, evidence TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS browser_sessions (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, site TEXT NOT NULL, status TEXT NOT NULL,
        state BYTEA NOT NULL, cookie_count INTEGER NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(owner, site)
    );
    CREATE TABLE IF NOT EXISTS browser_session_leases (
        owner TEXT NOT NULL, site TEXT NOT NULL, token TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL REFERENCES runs(id), session_id TEXT, revision INTEGER,
        expires_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(owner,site)
    );
'''
SESSION_COLUMNS = 'id,site,status,cookie_count,updated_at,revision'
LEASE_SECONDS = 30


class SessionConflict(ValueError):
    pass


@dataclass
class SessionLease:
    owner: str
    site: str
    run_id: str
    token: str = field(repr=False)
    session_id: str | None = None
    revision: int | None = None


def extend_schema(db):
    columns = db.columns("browser_sessions")
    if 'revision' not in columns:
        db.execute('ALTER TABLE browser_sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
    if 'state_version' not in columns:
        db.execute('ALTER TABLE browser_sessions ADD COLUMN state_version INTEGER NOT NULL DEFAULT 1')
    if 'site' not in db.columns("browser_actions"):
        db.execute("ALTER TABLE browser_actions ADD COLUMN site TEXT NOT NULL DEFAULT ''")
        for row in db.execute('SELECT id,url FROM browser_actions').fetchall():
            try:
                site = site_of(row['url'])
            except ValueError:
                site = ''
            db.execute('UPDATE browser_actions SET site=? WHERE id=?', (site, row['id']))
    db.execute('CREATE INDEX IF NOT EXISTS browser_actions_site ON browser_actions(owner,site,status)')


ACTION_TRANSITIONS = {
    'attempting': {'approved', 'declined', 'expired'},
    'approved': {'submitted', 'declined', 'expired'},
    'submitted': {'verified', 'unverified'},
}


def encrypt(value, key, associated_data):
    nonce = os.urandom(12)
    plaintext = json.dumps(value, ensure_ascii=False).encode()
    return nonce + AESGCM(key).encrypt(nonce, plaintext, associated_data.encode())


def decrypt(value, key, associated_data):
    try:
        plaintext = AESGCM(key).decrypt(value[:12], value[12:], associated_data.encode())
    except (InvalidTag, ValueError):
        raise BrowserUnavailable('Browser data could not be decrypted with the configured key.') from None
    return json.loads(plaintext)


def expire_requests(db, owner, run_id):
    db.execute("""UPDATE browser_requests SET status='expired',secret_values=NULL
                  WHERE owner=? AND run_id=? AND status IN ('pending','answered') AND expires_at<=?""",
               (owner, run_id, now()))


def public_request(row):
    if row is None:
        return None
    return {key: row[key] for key in ('id', 'run_id', 'kind', 'site', 'status', 'created_at', 'expires_at')} | {
        'payload': json.loads(row['payload']), 'has_screenshot': row['screenshot'] is not None,
    }


class BrowserStore:
    def __init__(self, runs, settings=None):
        self.runs = runs
        self.settings = settings or BrowserSettings.from_env()

    def start(self, owner, run_id):
        with self.runs.connect() as db:
            db.begin_write()
            self._run(db, owner, run_id, running=True)
            changed = db.execute("UPDATE runs SET deadline_at=?,phase='browser_starting',updated_at=? WHERE owner=? AND id=? AND status='running' AND deadline_at IS NULL",
                       (time.time() + self.settings.deadline_s, now(), owner, run_id))
            if not changed.rowcount:
                raise SessionConflict('This browser run has already started')

    def runtime(self, owner, run_id):
        with self.runs.connect() as db:
            row = self._run(db, owner, run_id)
            return {key: row[key] for key in ('status', 'deadline_at')}

    def set_view(self, owner, run_id, url):
        with self.runs.connect() as db:
            self._run(db, owner, run_id, running=True)
            db.execute('UPDATE runs SET browser_view=? WHERE owner=? AND id=? AND status=?', (url, owner, run_id, 'running'))

    def actions(self, owner, run_id):
        with self.runs.connect() as db:
            self._run(db, owner, run_id)
            return [dict(row) for row in db.execute(
                'SELECT id,summary,target_name,status,evidence FROM browser_actions WHERE owner=? AND run_id=? ORDER BY created_at',
                (owner, run_id))]

    @staticmethod
    def _run(db, owner, run_id, running=False):
        row = db.execute('SELECT * FROM runs WHERE owner=? AND id=?', (owner, run_id)).fetchone()
        if row is None:
            raise LookupError('Run not found')
        if running and row['status'] != 'running':
            raise ValueError('Run is no longer running')
        return row

    def add_step(self, owner, run_id, tool, label, status='ok', url=None, error_code=None):
        if status not in {'ok', 'error', 'waiting'}:
            raise ValueError('Invalid browser step status')
        with self.runs.connect() as db:
            db.begin_write()
            self._run(db, owner, run_id, running=True)
            seq = db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM browser_steps WHERE run_id=?', (run_id,)).fetchone()[0]
            db.execute('INSERT INTO browser_steps VALUES(?,?,?,?,?,?,?,?)',
                       (run_id, seq, now(), tool, redact(label), status, redact(url) if url else None, error_code))
        return seq

    def create_request(self, owner, run_id, kind, site, payload, screenshot=None, timeout=None):
        models = {'input': InputPayload, 'approval': ApprovalPayload, 'takeover': TakeoverPayload}
        if kind not in models:
            raise ValueError('Invalid browser request kind')
        payload = models[kind].model_validate(payload).model_dump()
        site = site_of('https://' + site)
        if kind == 'input' and any(field['kind'] in {'password', 'otp', 'magic_link'} for field in payload['fields']):
            self.settings.encryption_key()
        if kind == 'approval' and (payload['site'] != site or site_of(payload['url']) != site):
            raise ValueError('Approval URL must belong to the requested site')
        timeout = self.settings.request_timeout_s if timeout is None else timeout
        if not isinstance(timeout, int) or not 1 <= timeout <= 3540:
            raise ValueError('Request timeout must be between 1 and 3540 seconds')
        identifier, timestamp = str(uuid4()), now()
        expiry = (datetime.now(UTC) + timedelta(seconds=timeout)).isoformat()
        with self.runs.connect() as db:
            db.begin_write()
            self._run(db, owner, run_id, running=True)
            expire_requests(db, owner, run_id)
            if db.execute("SELECT id FROM browser_requests WHERE owner=? AND run_id=? AND status IN ('pending','answered')",
                          (owner, run_id)).fetchone():
                raise ValueError('A browser request is already waiting')
            db.execute('''INSERT INTO browser_requests(id,run_id,owner,kind,site,payload,screenshot,status,created_at,expires_at)
                          VALUES(?,?,?,?,?,?,?,'pending',?,?)''',
                       (identifier, run_id, owner, kind, site, json.dumps(payload), screenshot, timestamp, expiry))
            db.execute(f'UPDATE runs SET phase=?,deadline_at={db.greatest}(COALESCE(deadline_at,0),?),updated_at=? WHERE owner=? AND id=?',
                       ('awaiting_' + kind, time.time() + timeout + 60, timestamp, owner, run_id))
        return self.get_request(owner, run_id, identifier)

    def get_request(self, owner, run_id, request_id):
        with self.runs.connect() as db:
            self._run(db, owner, run_id)
            expire_requests(db, owner, run_id)
            row = db.execute('SELECT * FROM browser_requests WHERE owner=? AND run_id=? AND id=?',
                             (owner, run_id, request_id)).fetchone()
            if row is None:
                raise LookupError('Browser request not found')
            return public_request(row)

    def answer_request(self, owner, run_id, request_id, response):
        body = BrowserResponse.model_validate(response).model_dump(exclude_none=True)
        conflict = False
        with self.runs.connect() as db:
            db.begin_write()
            self._run(db, owner, run_id, running=True)
            expire_requests(db, owner, run_id)
            row = db.execute('SELECT * FROM browser_requests WHERE owner=? AND run_id=? AND id=?',
                             (owner, run_id, request_id)).fetchone()
            if row is None:
                raise LookupError('Browser request not found')
            if row['status'] != 'pending':
                conflict = True
            else:
                secret_values = {}
                payload = json.loads(row['payload'])
                if row['kind'] == 'approval':
                    valid = set(body) == {'decision'}
                elif row['kind'] == 'takeover':
                    valid = body == {'done': True}
                else:
                    values = body.get('values', {})
                    valid = set(body) == {'values'} and set(values) == {field['id'] for field in payload['fields']}
                    if valid:
                        values = dict(values)
                        for field in payload['fields']:
                            value = values[field['id']]
                            if not value or (field['kind'] == 'choice' and value not in field['choices']):
                                valid = False
                            if field['kind'] in {'password', 'otp', 'magic_link'}:
                                secret_values[field['id']] = values.pop(field['id'])
                        body = {'values': values}
                if not valid:
                    raise ValueError('Response does not match the browser request')
                encrypted = encrypt(secret_values, self.settings.encryption_key(), f'{owner}|{request_id}') if secret_values else None
                db.execute("UPDATE browser_requests SET response=?,secret_values=?,status='answered' WHERE owner=? AND id=?",
                           (json.dumps(body), encrypted, owner, request_id))
        if conflict:
            raise ValueError('Browser request is no longer pending')
        return self.get_request(owner, run_id, request_id)

    def take_response(self, owner, run_id, request_id):
        with self.runs.connect() as db:
            db.begin_write()
            self._run(db, owner, run_id, running=True)
            expire_requests(db, owner, run_id)
            row = db.execute('SELECT * FROM browser_requests WHERE owner=? AND run_id=? AND id=?',
                             (owner, run_id, request_id)).fetchone()
            if row is None:
                raise LookupError('Browser request not found')
            if row['status'] != 'answered':
                return None
            secrets = decrypt(row['secret_values'], self.settings.encryption_key(), f'{owner}|{request_id}') if row['secret_values'] else {}
            db.execute("UPDATE browser_requests SET status='taken',secret_values=NULL WHERE owner=? AND id=?", (owner, request_id))
            return {'response': json.loads(row['response']), 'secrets': secrets}

    def screenshot(self, owner, run_id, request_id):
        with self.runs.connect() as db:
            self._run(db, owner, run_id)
            row = db.execute('SELECT screenshot FROM browser_requests WHERE owner=? AND run_id=? AND id=?',
                             (owner, run_id, request_id)).fetchone()
            if row is None or row['screenshot'] is None:
                raise LookupError('Screenshot not found')
            return row['screenshot']

    def create_action(self, owner, run_id, kind, summary, target_name, url, before_text_hash):
        site = site_of(url)
        identifier, timestamp = str(uuid4()), now()
        with self.runs.connect() as db:
            db.begin_write()
            self._run(db, owner, run_id, running=True)
            if db.execute("SELECT id FROM browser_actions WHERE owner=? AND site=? AND status IN ('attempting','approved','submitted','unverified')",
                          (owner, site)).fetchone():
                raise SessionConflict('An action on this site is awaiting approval or confirmation. Resolve it before submitting again.')
            db.execute('''INSERT INTO browser_actions(id,run_id,owner,kind,summary,target_name,url,status,before_text_hash,evidence,created_at,updated_at,site)
                          VALUES(?,?,?,?,?,?,?,'attempting',?,NULL,?,?,?)''',
                       (identifier, run_id, owner, kind, redact(summary), redact(target_name), redact(url), before_text_hash, timestamp, timestamp, site))
        return identifier

    def resolve_action(self, owner, action_id, decision, evidence):
        if decision not in {'confirmed', 'not_completed'} or not evidence.strip():
            raise ValueError('Provide the checked result and evidence from your account')
        with self.runs.connect() as db:
            db.begin_write()
            row = db.execute('SELECT a.status,r.status AS run_status FROM browser_actions a JOIN runs r ON r.id=a.run_id WHERE a.owner=? AND a.id=?',
                             (owner, action_id)).fetchone()
            if row is None:
                raise LookupError('Browser action not found')
            if row['status'] != 'unverified' or row['run_status'] in {'queued', 'running'}:
                raise SessionConflict('Only uncertain actions from finished runs can be resolved')
            db.execute('UPDATE browser_actions SET status=?,evidence=?,updated_at=? WHERE owner=? AND id=?',
                       ('verified' if decision == 'confirmed' else 'not_completed', redact(evidence), now(), owner, action_id))

    def unresolved_actions(self, owner):
        with self.runs.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,run_id,site,summary,status,created_at FROM browser_actions WHERE owner=? AND status='unverified' ORDER BY created_at",
                (owner,))]

    def update_action(self, owner, run_id, action_id, status, evidence=None):
        with self.runs.connect() as db:
            db.begin_write()
            self._run(db, owner, run_id, running=True)
            row = db.execute('SELECT status FROM browser_actions WHERE owner=? AND run_id=? AND id=?', (owner, run_id, action_id)).fetchone()
            if row is None:
                raise LookupError('Browser action not found')
            if status not in ACTION_TRANSITIONS.get(row['status'], set()):
                raise ValueError('Invalid browser action transition')
            if status == 'verified' and not evidence:
                raise ValueError('Verified actions require confirmation evidence')
            db.execute('UPDATE browser_actions SET status=?,evidence=?,updated_at=? WHERE owner=? AND id=?',
                       (status, redact(evidence) if evidence else None, now(), owner, action_id))

    def list_sessions(self, owner):
        with self.runs.connect() as db:
            return [dict(row) for row in db.execute(f'SELECT {SESSION_COLUMNS} FROM browser_sessions WHERE owner=? ORDER BY site', (owner,))]

    def _session_aad(self, owner, site, identifier, revision):
        return json.dumps([2, owner, site, identifier, revision], separators=(',', ':'))

    def _session_state(self, row):
        aad = (self._session_aad(row['owner'], row['site'], row['id'], row['revision']) if row['state_version'] == 2
               else f"{row['owner']}|{row['site']}|{row['id']}")
        return filter_storage_state(decrypt(row['state'], self.settings.encryption_key(), aad), row['site'])

    @staticmethod
    def _clear_inactive_leases(db):
        db.execute("""DELETE FROM browser_session_leases WHERE expires_at<=? OR run_id IN
                      (SELECT id FROM runs WHERE status!='running' OR deadline_at<=?)""", (time.time(), time.time()))

    def acquire_session(self, owner, run_id, site):
        site = site_of('https://' + site)
        with self.runs.connect() as db:
            db.begin_write()
            run = self._run(db, owner, run_id, running=True)
            if run['deadline_at'] is None or run['deadline_at'] <= time.time():
                raise SessionConflict('Browser run has expired or has not started')
            self._clear_inactive_leases(db)
            if db.execute('SELECT 1 FROM browser_session_leases WHERE owner=? AND site=?', (owner, site)).fetchone():
                raise SessionConflict('This saved login is in use by another browser task')
            row = db.execute('SELECT * FROM browser_sessions WHERE owner=? AND site=?', (owner, site)).fetchone()
            lease = SessionLease(owner, site, run_id, str(uuid4()), row['id'] if row else None, row['revision'] if row else None)
            state = self._session_state(row) if row and row['status'] != 'expired' else None
            db.execute('INSERT INTO browser_session_leases VALUES(?,?,?,?,?,?,?)',
                       (owner, site, lease.token, run_id, lease.session_id, lease.revision, min(time.time() + LEASE_SECONDS, run['deadline_at'])))
        return lease, state, row['status'] if row else 'unverified'

    def _require_lease(self, db, lease):
        run = self._run(db, lease.owner, lease.run_id, running=True)
        row = db.execute('SELECT * FROM browser_session_leases WHERE owner=? AND site=? AND token=? AND run_id=? AND expires_at>?',
                         (lease.owner, lease.site, lease.token, lease.run_id, time.time())).fetchone()
        session = db.execute('SELECT id,revision FROM browser_sessions WHERE owner=? AND site=?', (lease.owner, lease.site)).fetchone()
        current = (session['id'], session['revision']) if session else (None, None)
        if (row is None or run['deadline_at'] is None or run['deadline_at'] <= time.time()
                or current != (lease.session_id, lease.revision)
                or (row['session_id'], row['revision']) != current):
            raise SessionConflict('The saved login changed, was deleted, or its lease expired')
        return run

    def require_session(self, lease, renew=False):
        with self.runs.connect() as db:
            if renew:
                db.begin_write()
            run = self._require_lease(db, lease)
            if renew:
                db.execute('UPDATE browser_session_leases SET expires_at=? WHERE owner=? AND site=? AND token=?',
                           (min(time.time() + LEASE_SECONDS, run['deadline_at']), lease.owner, lease.site, lease.token))

    def release_session(self, lease):
        with self.runs.connect() as db:
            db.execute('DELETE FROM browser_session_leases WHERE owner=? AND site=? AND token=? AND run_id=?',
                       (lease.owner, lease.site, lease.token, lease.run_id))

    def save_session(self, owner, site, state, status='saved', *, lease=None, expected_revision=None):
        if status not in {'saved', 'unverified'}:
            raise ValueError('Invalid saved login status')
        site = site_of('https://' + site)
        key = self.settings.encryption_key()
        state = filter_storage_state(state, site)
        with self.runs.connect() as db:
            db.begin_write()
            self._clear_inactive_leases(db)
            if lease is not None:
                if (lease.owner, lease.site) != (owner, site):
                    raise SessionConflict('Saved login scope does not match this browser task')
                self._require_lease(db, lease)
            elif db.execute('SELECT 1 FROM browser_session_leases WHERE owner=? AND site=?', (owner, site)).fetchone():
                raise SessionConflict('This saved login is in use; stop its browser task before importing cookies')
            old = db.execute('SELECT id,revision FROM browser_sessions WHERE owner=? AND site=?', (owner, site)).fetchone()
            if expected_revision is not None and (old is None or old['revision'] != expected_revision):
                raise SessionConflict('The saved login changed or was deleted')
            identifier = old['id'] if old else str(uuid4())
            revision = old['revision'] + 1 if old else 1
            encrypted = encrypt(state, key, self._session_aad(owner, site, identifier, revision))
            db.execute('''INSERT INTO browser_sessions(id,owner,site,status,state,cookie_count,updated_at,revision,state_version)
                          VALUES(?,?,?,?,?,?,?,?,2) ON CONFLICT(owner,site)
                          DO UPDATE SET state=excluded.state,status=excluded.status,cookie_count=excluded.cookie_count,
                          updated_at=excluded.updated_at,revision=excluded.revision,state_version=2''',
                       (identifier, owner, site, status, encrypted, len(state['cookies']), now(), revision))
            if lease is not None:
                db.execute('UPDATE browser_session_leases SET session_id=?,revision=? WHERE owner=? AND site=? AND token=?',
                           (identifier, revision, owner, site, lease.token))
                lease.session_id, lease.revision = identifier, revision
            return dict(db.execute(f'SELECT {SESSION_COLUMNS} FROM browser_sessions WHERE owner=? AND id=?', (owner, identifier)).fetchone())

    def load_session(self, owner, site):
        site = site_of('https://' + site)
        with self.runs.connect() as db:
            row = db.execute("SELECT * FROM browser_sessions WHERE owner=? AND site=? AND status IN ('saved','unverified')", (owner, site)).fetchone()
        if row is None:
            return None
        return self._session_state(row)

    def expire_session(self, owner, session_id, *, lease=None):
        with self.runs.connect() as db:
            db.begin_write()
            if lease is not None:
                self._require_lease(db, lease)
            changed = db.execute("UPDATE browser_sessions SET status='expired',updated_at=? WHERE owner=? AND id=?", (now(), owner, session_id)).rowcount
            if not changed:
                raise LookupError('Saved login not found')

    def delete_session(self, owner, session_id):
        with self.runs.connect() as db:
            db.begin_write()
            if not db.execute('DELETE FROM browser_sessions WHERE owner=? AND id=?', (owner, session_id)).rowcount:
                raise LookupError('Saved login not found')
            db.execute("""UPDATE browser_requests SET status='expired',secret_values=NULL WHERE owner=?
                          AND status IN ('pending','answered') AND run_id IN
                          (SELECT run_id FROM browser_session_leases WHERE owner=? AND session_id=?)""",
                       (owner, owner, session_id))
            db.execute('DELETE FROM browser_session_leases WHERE owner=? AND session_id=?', (owner, session_id))

    def state(self, owner, run_id):
        with self.runs.connect() as db:
            run = self._run(db, owner, run_id)
            if run['deadline_at'] is not None:
                expire_requests(db, owner, run_id)
            steps = [dict(row) for row in db.execute('SELECT * FROM browser_steps WHERE run_id=? ORDER BY seq DESC LIMIT 30', (run_id,))][::-1]
            request = db.execute("SELECT * FROM browser_requests WHERE owner=? AND run_id=? AND status='pending'", (owner, run_id)).fetchone()
            actions = [dict(row) for row in db.execute('SELECT id,summary,status FROM browser_actions WHERE owner=? AND run_id=? ORDER BY created_at', (owner, run_id))]
            if not steps and request is None and not actions and not run['browser_view'] and run['deadline_at'] is None:
                return None
            return {'steps': steps, 'request': public_request(request), 'actions': actions,
                    'live_view_url': run['browser_view'] if run['status'] == 'running' else None}
