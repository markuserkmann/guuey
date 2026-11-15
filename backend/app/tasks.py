"""Persistent recurring research tasks, queued through the existing run worker."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.store import now
from app.task_context import validate_timezone
from app.telegram import TelegramSettings, queue_notification


SCHEMA = """
CREATE TABLE IF NOT EXISTS recurring_tasks (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_id TEXT NOT NULL REFERENCES chats(id),
    title TEXT NOT NULL, prompt TEXT NOT NULL, notification_condition TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL, timezone TEXT NOT NULL, enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_run_at TEXT NOT NULL,
    last_run_at TEXT, last_success_at TEXT, last_status TEXT, last_error TEXT,
    previous_result TEXT
);
CREATE INDEX IF NOT EXISTS recurring_tasks_due ON recurring_tasks(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS recurring_tasks_owner ON recurring_tasks(owner, created_at);
CREATE TABLE IF NOT EXISTS task_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    task_id TEXT NOT NULL REFERENCES recurring_tasks(id) ON DELETE CASCADE,
    scheduled_for TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'queued', note TEXT,
    UNIQUE(task_id, scheduled_for)
);
CREATE TABLE IF NOT EXISTS task_seen_items (
    task_id TEXT NOT NULL REFERENCES recurring_tasks(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL, PRIMARY KEY(task_id, item_key)
);
CREATE TABLE IF NOT EXISTS task_notifications (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES recurring_tasks(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id), chat_id TEXT NOT NULL,
    title TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL,
    created_at TEXT NOT NULL, read_at TEXT
);
CREATE INDEX IF NOT EXISTS task_notifications_owner ON task_notifications(owner, created_at);
"""

CVE_TEMPLATE = {
    "title": "Critical CVE watch",
    "prompt": (
        "Check newly published and materially updated CVEs using CVE.org, NVD, CISA, and "
        "the affected vendors' security advisories. Verify publication dates and affected "
        "versions. Include CVE IDs, severity and score source, exploitation status when "
        "confirmed, available patches or mitigations, and links to primary sources."
    ),
    "notification_condition": (
        "Notify me only about a newly published CVE rated Critical by its vendor or with "
        "a verified CVSS v3.x/v4.0 score of at least 9.0, or a material update raising an "
        "existing CVE to that severity. Do not repeat previously reported events."
    ),
    "interval_minutes": 60,
}


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=8000)
    notification_condition: str = Field(min_length=1, max_length=2000)
    interval_minutes: int = Field(default=60, ge=1, le=10080, strict=True)
    timezone: str = Field(default="UTC", max_length=100)
    enabled: bool = True
    telegram_enabled: bool = False

    _timezone = field_validator("timezone")(validate_timezone)


class TaskEnabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def public_task(row):
    value = dict(row)
    value.pop("owner", None)
    value.pop("previous_result", None)
    value["enabled"] = bool(value["enabled"])
    value["telegram_enabled"] = bool(value["telegram_enabled"])
    return value


class TaskStore:
    def __init__(self, runs):
        self.runs = runs

    def list(self, owner):
        with self.runs.connect() as db:
            rows = db.execute("SELECT * FROM recurring_tasks WHERE owner=? ORDER BY created_at DESC", (owner,)).fetchall()
        return [public_task(row) for row in rows]

    def get(self, owner, task_id):
        with self.runs.connect() as db:
            row = db.execute("SELECT * FROM recurring_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
        return public_task(row) if row else None

    def save(self, owner, body, task_id=None):
        timestamp = now()
        next_run = (datetime.fromisoformat(timestamp) + timedelta(minutes=body.interval_minutes)).isoformat()
        with self.runs.connect() as db:
            db.begin_write()
            if body.telegram_enabled:
                settings = TelegramSettings.from_env()
                if not settings.configured or not db.execute(
                    "SELECT 1 FROM telegram_links WHERE owner=? AND bot_id=?", (owner, settings.bot_id)
                ).fetchone():
                    raise ValueError("Link your Telegram account before enabling notifications.")
            if task_id:
                row = db.execute("SELECT * FROM recurring_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
                if not row:
                    raise LookupError("Task not found")
                if self.active(db, task_id):
                    raise ValueError("Wait for the current check to finish before editing this task.")
                if body.interval_minutes == row["interval_minutes"] and body.enabled == bool(row["enabled"]):
                    next_run = row["next_run_at"]
                db.execute("""UPDATE recurring_tasks SET title=?,prompt=?,notification_condition=?,
                    interval_minutes=?,timezone=?,enabled=?,updated_at=?,next_run_at=? WHERE id=?""",
                           (body.title, body.prompt, body.notification_condition, body.interval_minutes,
                            body.timezone, body.enabled, timestamp, next_run, task_id))
                db.execute("UPDATE chats SET title=? WHERE id=?", (body.title, row["chat_id"]))
                db.execute("UPDATE chat_search SET title=? WHERE chat_id=?", (body.title, row["chat_id"]))
            else:
                if db.execute("SELECT count(*) FROM recurring_tasks WHERE owner=?", (owner,)).fetchone()[0] >= 50:
                    raise ValueError("You can save up to 50 recurring tasks.")
                task_id, chat_id = str(uuid4()), str(uuid4())
                db.execute("INSERT INTO chats(id,owner,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                           (chat_id, owner, body.title, timestamp, timestamp))
                db.execute("INSERT INTO chat_search VALUES(?,?,?,'')", (chat_id, owner, body.title))
                db.execute("""INSERT INTO recurring_tasks
                    (id,owner,chat_id,title,prompt,notification_condition,interval_minutes,timezone,
                     enabled,created_at,updated_at,next_run_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (task_id, owner, chat_id, body.title, body.prompt, body.notification_condition,
                            body.interval_minutes, body.timezone, body.enabled, timestamp, timestamp, next_run))
            db.execute("UPDATE recurring_tasks SET telegram_enabled=? WHERE id=?", (body.telegram_enabled, task_id))
            if not body.telegram_enabled:
                db.execute("""UPDATE telegram_deliveries SET status='cancelled' WHERE status='pending'
                    AND notification_id IN (SELECT id FROM task_notifications WHERE task_id=?)""", (task_id,))
        return self.get(owner, task_id)

    @staticmethod
    def active(db, task_id):
        return db.execute("""SELECT r.id FROM runs r JOIN recurring_tasks t ON r.chat_id=t.chat_id
            WHERE t.id=? AND r.status IN ('queued','running')""", (task_id,)).fetchone()

    def set_enabled(self, owner, task_id, enabled):
        with self.runs.connect() as db:
            db.begin_write()
            row = db.execute("SELECT * FROM recurring_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if not row:
                raise LookupError("Task not found")
            next_run = row["next_run_at"]
            if enabled and not row["enabled"]:
                next_run = (datetime.now(UTC) + timedelta(minutes=row["interval_minutes"])).isoformat()
            db.execute("UPDATE recurring_tasks SET enabled=?,next_run_at=?,updated_at=? WHERE id=?",
                       (enabled, next_run, now(), task_id))
        return self.get(owner, task_id)

    def delete(self, owner, task_id):
        with self.runs.connect() as db:
            db.begin_write()
            if not db.execute("SELECT 1 FROM recurring_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone():
                raise LookupError("Task not found")
            if self.active(db, task_id):
                raise ValueError("Wait for the current check to finish before deleting this task.")
            db.execute("DELETE FROM recurring_tasks WHERE owner=? AND id=?", (owner, task_id))

    def enqueue_due(self, timestamp=None):
        timestamp = timestamp or now()
        queued = []
        with self.runs.connect() as db:
            db.begin_write()
            rows = db.execute("SELECT * FROM recurring_tasks WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at LIMIT 20",
                              (timestamp,)).fetchall()
            for row in rows:
                if self.active(db, row["id"]):
                    continue
                # Coalesce missed intervals into one check after downtime.
                due = datetime.fromisoformat(row["next_run_at"])
                interval = timedelta(minutes=row["interval_minutes"])
                steps = (datetime.fromisoformat(timestamp) - due) // interval + 1
                next_run = (due + steps * interval).isoformat()
                queued.append(self.enqueue(db, row, timestamp, row["next_run_at"]))
                db.execute("UPDATE recurring_tasks SET next_run_at=? WHERE id=?", (next_run, row["id"]))
        return queued

    def run_now(self, owner, task_id):
        with self.runs.connect() as db:
            db.begin_write()
            row = db.execute("SELECT * FROM recurring_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if not row:
                raise LookupError("Task not found")
            if self.active(db, task_id):
                raise ValueError("This task already has a check in progress.")
            timestamp = now()
            run_id = self.enqueue(db, row, timestamp, timestamp)
            db.execute("UPDATE recurring_tasks SET next_run_at=? WHERE id=?",
                       ((datetime.fromisoformat(timestamp) + timedelta(minutes=row["interval_minutes"])).isoformat(), task_id))
        return {"run_id": run_id}

    @staticmethod
    def enqueue(db, task, timestamp, scheduled_for):
        identifier = str(uuid4())
        db.execute("""INSERT INTO runs
            (id,owner,chat_id,request_id,message,mode,status,phase,created_at,updated_at,timezone)
            VALUES(?,?,?,?,?,'auto','queued','queued',?,?,?)""",
                   (identifier, task["owner"], task["chat_id"], f"task:{identifier}", task["prompt"],
                    timestamp, timestamp, task["timezone"]))
        db.execute("INSERT INTO task_runs(run_id,task_id,scheduled_for) VALUES(?,?,?)", (identifier, task["id"], scheduled_for))
        db.execute("UPDATE recurring_tasks SET last_run_at=?,last_status='queued',last_error=NULL WHERE id=?", (timestamp, task["id"]))
        return identifier

    def for_run(self, run_id):
        with self.runs.connect() as db:
            row = db.execute("SELECT t.* FROM recurring_tasks t JOIN task_runs r ON t.id=r.task_id WHERE r.run_id=?", (run_id,)).fetchone()
            if not row:
                return None
            keys = [item[0] for item in db.execute(f"SELECT item_key FROM task_seen_items WHERE task_id=? ORDER BY {db.order_column} DESC LIMIT 500", (row["id"],))]
        return {**dict(row), "seen_keys": keys}

    def history(self, owner, task_id):
        if not self.get(owner, task_id):
            raise LookupError("Task not found")
        with self.runs.connect() as db:
            rows = db.execute("""SELECT t.run_id,t.scheduled_for,t.outcome,t.note,r.status,r.phase,r.created_at,r.updated_at
                FROM task_runs t JOIN runs r ON t.run_id=r.id WHERE t.task_id=? ORDER BY r.created_at DESC LIMIT 50""", (task_id,)).fetchall()
        return [dict(row) for row in rows]

    def notifications(self, owner):
        with self.runs.connect() as db:
            rows = db.execute("SELECT * FROM task_notifications WHERE owner=? ORDER BY created_at DESC LIMIT 100", (owner,)).fetchall()
            unread = db.execute("SELECT count(*) FROM task_notifications WHERE owner=? AND read_at IS NULL", (owner,)).fetchone()[0]
        return {"unread_count": unread, "items": [
            {key: json.loads(value) if key == "sources" else value for key, value in dict(row).items() if key != "owner"}
            for row in rows
        ]}

    def mark_read(self, owner, notification_id):
        with self.runs.connect() as db:
            updated = db.execute("UPDATE task_notifications SET read_at=COALESCE(read_at,?) WHERE owner=? AND id=?",
                                 (now(), owner, notification_id))
            if not updated.rowcount:
                raise LookupError("Update not found")

    def mark_all_read(self, owner):
        with self.runs.connect() as db:
            db.execute("UPDATE task_notifications SET read_at=? WHERE owner=? AND read_at IS NULL", (now(), owner))


def finish_task(db, run, status, phase, result):
    """Finalize task state and its inbox entry in the run's completion transaction."""
    task = db.execute("SELECT t.* FROM recurring_tasks t JOIN task_runs r ON t.id=r.task_id WHERE r.run_id=?", (run["id"],)).fetchone()
    if not task:
        return False
    timestamp = now()
    evaluation = (result or {}).get("task_evaluation")
    successful = status == "completed" and evaluation is not None and evaluation.get("check_complete") is True
    outcome = "no_match" if successful else "cancelled" if status == "cancelled" else "failed"
    error = json.loads(run["debug"]).get("error", {}).get("message")
    note = evaluation.get("summary", "") if evaluation else error or f"Check did not complete ({phase.replace('_', ' ')})."
    if successful:
        new_items = []
        for item in evaluation.get("items", []):
            key = item["key"].strip().casefold()
            added = db.execute("INSERT INTO task_seen_items(task_id,item_key) VALUES(?,?) ON CONFLICT DO NOTHING", (task["id"], key))
            if added.rowcount:
                new_items.append(item)
        if new_items:
            outcome = "notified"
            notification_id = str(uuid4())
            content = "\n\n".join(item["message"] for item in new_items)
            sources = list({source["url"]: source for item in new_items for source in item["sources"]}.values())
            db.execute("""INSERT INTO task_notifications
                (id,owner,task_id,run_id,chat_id,title,content,sources,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                       (notification_id, task["owner"], task["id"], run["id"], task["chat_id"], task["title"], content, json.dumps(sources), timestamp))
            queue_notification(db, task, notification_id)
            db.execute("INSERT INTO messages(id,chat_id,role,content,sources,created_at) VALUES(?,?,'assistant',?,?,?)",
                       (f"assistant:{run['id']}", task["chat_id"], content, json.dumps(sources), timestamp))
            db.execute("UPDATE chats SET updated_at=? WHERE id=?", (timestamp, task["chat_id"]))
            db.execute("DELETE FROM chat_search WHERE chat_id=?", (task["chat_id"],))
            db.execute(f"""INSERT INTO chat_search SELECT c.id,c.owner,c.title,
                COALESCE((SELECT {db.text_aggregate}(content,' ') FROM messages WHERE chat_id=c.id),'')
                FROM chats c WHERE c.id=?""", (task["chat_id"],))
        db.execute("UPDATE recurring_tasks SET last_success_at=?,previous_result=? WHERE id=?",
                   (run["created_at"], result["reply"][:24000], task["id"]))
    db.execute("UPDATE task_runs SET outcome=?,note=? WHERE run_id=?", (outcome, note, run["id"]))
    db.execute("UPDATE recurring_tasks SET last_status=?,last_error=? WHERE id=?",
               (outcome, None if successful else note, task["id"]))
    return True
