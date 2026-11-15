"""Private Telegram links and a durable queue for recurring-task updates."""

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from app.accounts import AccountStore
from app.store import now


SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_links (
    owner TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, bot_id TEXT NOT NULL,
    chat_id BIGINT NOT NULL UNIQUE, username TEXT, first_name TEXT NOT NULL,
    linked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_pending_links (
    owner TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, bot_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE, expires_at DOUBLE PRECISION NOT NULL,
    chat_id BIGINT, username TEXT, first_name TEXT
);
CREATE TABLE IF NOT EXISTS telegram_deliveries (
    notification_id TEXT PRIMARY KEY REFERENCES task_notifications(id) ON DELETE CASCADE,
    link_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0, due_at DOUBLE PRECISION NOT NULL,
    claimed_at DOUBLE PRECISION, sent_at TEXT, message_id BIGINT, error TEXT
);
CREATE INDEX IF NOT EXISTS telegram_deliveries_due ON telegram_deliveries(status, due_at);
CREATE TABLE IF NOT EXISTS telegram_worker_state (
    bot_id TEXT PRIMARY KEY, next_update_id BIGINT NOT NULL DEFAULT 0,
    lease_owner TEXT, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0, seen_at DOUBLE PRECISION NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class TelegramSettings:
    token: str = field(default="", repr=False)
    username: str = ""
    app_url: str = ""

    @classmethod
    def from_env(cls):
        return cls(os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
                   os.getenv("TELEGRAM_BOT_USERNAME", "").strip().removeprefix("@"),
                   os.getenv("APP_PUBLIC_URL", "").strip().rstrip("/"))

    @property
    def configured(self):
        return bool(re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{30,}", self.token)
                    and re.fullmatch(r"[A-Za-z0-9_]{5,32}", self.username)
                    and self.username.lower().endswith("bot"))

    @property
    def bot_id(self):
        return self.token.partition(":")[0] if self.configured else ""

    @property
    def public_url(self):
        try:
            url = urlsplit(self.app_url)
            if (url.scheme in {"http", "https"} and url.hostname and not url.username
                    and not url.password and not url.query and not url.fragment
                    and len(self.app_url) <= 500):
                return self.app_url
        except ValueError:
            pass
        return ""


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def account_enabled(db, owner):
    user = db.execute("SELECT disabled FROM users WHERE id=?", (owner,)).fetchone()
    return user is None or not user["disabled"]


def unlink(db, owner):
    db.execute("""UPDATE telegram_deliveries SET status='cancelled',error=NULL
        WHERE status='pending' AND link_id IN (SELECT id FROM telegram_links WHERE owner=?)""", (owner,))
    db.execute("DELETE FROM telegram_links WHERE owner=?", (owner,))
    db.execute("DELETE FROM telegram_pending_links WHERE owner=?", (owner,))
    db.execute("UPDATE recurring_tasks SET telegram_enabled=0 WHERE owner=?", (owner,))


def queue_notification(db, task, notification_id):
    if task["telegram_enabled"]:
        db.execute("""INSERT INTO telegram_deliveries(notification_id,link_id,due_at)
            SELECT ?,id,? FROM telegram_links WHERE owner=?""", (notification_id, time.time(), task["owner"]))


class TelegramStore:
    def __init__(self, runs, settings=None):
        self.runs = runs
        self.settings = settings or TelegramSettings.from_env()
        AccountStore(runs.database)

    def status(self, owner):
        with self.runs.connect() as db:
            link = db.execute("SELECT * FROM telegram_links WHERE owner=? AND bot_id=?",
                              (owner, self.settings.bot_id)).fetchone()
            pending = db.execute("""SELECT * FROM telegram_pending_links
                WHERE owner=? AND bot_id=? AND expires_at>?""", (owner, self.settings.bot_id, time.time())).fetchone()
            worker = db.execute("SELECT seen_at FROM telegram_worker_state WHERE bot_id=?",
                                (self.settings.bot_id,)).fetchone()
            issue = db.execute("""SELECT d.status,d.error,n.title FROM telegram_deliveries d
                JOIN task_notifications n ON n.id=d.notification_id WHERE n.owner=?
                AND d.link_id=? AND d.status IN ('failed','unknown') ORDER BY n.created_at DESC LIMIT 1""",
                               (owner, link["id"] if link else "")).fetchone()
            queued = db.execute("""SELECT count(*) FROM telegram_deliveries d JOIN task_notifications n
                ON n.id=d.notification_id WHERE n.owner=? AND d.status IN ('pending','sending')""", (owner,)).fetchone()[0]
        return {
            "configured": self.settings.configured, "bot_username": self.settings.username if self.settings.configured else None,
            "worker_online": bool(worker and worker["seen_at"] > time.time() - 90),
            "linked": self.identity(link) if link else None,
            "pending": {"id": pending["id"], "expires_at": datetime.fromtimestamp(pending["expires_at"], UTC).isoformat(),
                        "candidate": self.identity(pending) if pending["chat_id"] else None} if pending else None,
            "queued_count": queued, "delivery_issue": dict(issue) if issue else None,
        }

    @staticmethod
    def identity(row):
        return {"telegram_id": str(row["chat_id"]), "username": row["username"], "first_name": row["first_name"]}

    def start_link(self, owner):
        if not self.settings.configured:
            raise ValueError("Telegram is not configured on this server.")
        nonce, attempt_id = secrets.token_urlsafe(32), str(uuid4())
        with self.runs.connect() as db:
            db.begin_write()
            db.execute("DELETE FROM telegram_pending_links WHERE expires_at<=?", (time.time(),))
            if db.execute("SELECT 1 FROM telegram_links WHERE owner=? AND bot_id=?", (owner, self.settings.bot_id)).fetchone():
                raise ValueError("Disconnect your current Telegram account before linking another.")
            # Retire links to a replaced bot before offering a new invitation.
            unlink(db, owner)
            db.execute("""INSERT INTO telegram_pending_links(owner,id,bot_id,token_hash,expires_at)
                VALUES(?,?,?,?,?)""", (owner, attempt_id, self.settings.bot_id, token_hash(nonce), time.time() + 600))
        return {"url": f"https://t.me/{self.settings.username}?start={nonce}", **self.status(owner)}

    def receive_link(self, nonce, chat_id, username, first_name):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce):
            return False
        with self.runs.connect() as db:
            db.begin_write()
            row = db.execute("""SELECT * FROM telegram_pending_links
                WHERE token_hash=? AND bot_id=? AND expires_at>? AND chat_id IS NULL""",
                             (token_hash(nonce), self.settings.bot_id, time.time())).fetchone()
            if not row or not account_enabled(db, row["owner"]):
                return False
            if db.execute("SELECT 1 FROM telegram_links WHERE chat_id=?", (chat_id,)).fetchone():
                return False
            db.execute("UPDATE telegram_pending_links SET chat_id=?,username=?,first_name=? WHERE id=?",
                       (chat_id, username, first_name, row["id"]))
        return True

    def confirm(self, owner, attempt_id):
        with self.runs.connect() as db:
            db.begin_write()
            pending = db.execute("""SELECT * FROM telegram_pending_links WHERE owner=? AND id=?
                AND bot_id=? AND expires_at>? AND chat_id IS NOT NULL""",
                                 (owner, attempt_id, self.settings.bot_id, time.time())).fetchone()
            if not pending:
                raise ValueError("This link is not ready or has expired. Start a new link if needed.")
            if db.execute("SELECT 1 FROM telegram_links WHERE chat_id=?", (pending["chat_id"],)).fetchone():
                raise ValueError("That Telegram account is already linked. Disconnect it first.")
            db.execute("""INSERT INTO telegram_links(owner,id,bot_id,chat_id,username,first_name,linked_at)
                VALUES(?,?,?,?,?,?,?)""", (owner, str(uuid4()), self.settings.bot_id, pending["chat_id"],
                                         pending["username"], pending["first_name"], now()))
            db.execute("DELETE FROM telegram_pending_links WHERE owner=?", (owner,))
        return self.status(owner)

    def disconnect(self, owner):
        with self.runs.connect() as db:
            db.begin_write()
            unlink(db, owner)

    def stop_chat(self, chat_id):
        with self.runs.connect() as db:
            db.begin_write()
            rows = db.execute("SELECT owner FROM telegram_links WHERE chat_id=? AND bot_id=?",
                              (chat_id, self.settings.bot_id)).fetchall()
            for row in rows:
                unlink(db, row["owner"])
            db.execute("DELETE FROM telegram_pending_links WHERE chat_id=? AND bot_id=?", (chat_id, self.settings.bot_id))

    def lease(self, worker_id):
        with self.runs.connect() as db:
            db.begin_write()
            db.execute("INSERT INTO telegram_worker_state(bot_id) VALUES(?) ON CONFLICT DO NOTHING", (self.settings.bot_id,))
            return bool(db.execute("""UPDATE telegram_worker_state SET lease_owner=?,lease_until=?,seen_at=?
                WHERE bot_id=? AND (lease_owner=? OR lease_until<?)""",
                                   (worker_id, time.time() + 90, time.time(), self.settings.bot_id, worker_id, time.time())).rowcount)

    def release(self, worker_id):
        with self.runs.connect() as db:
            db.execute("UPDATE telegram_worker_state SET lease_until=0,seen_at=0 WHERE bot_id=? AND lease_owner=?",
                       (self.settings.bot_id, worker_id))

    def offset(self, value=None):
        with self.runs.connect() as db:
            if value is not None:
                db.execute("UPDATE telegram_worker_state SET next_update_id=? WHERE bot_id=?",
                           (value, self.settings.bot_id))
            return db.execute("SELECT next_update_id FROM telegram_worker_state WHERE bot_id=?", (self.settings.bot_id,)).fetchone()[0]

    def claim_delivery(self):
        with self.runs.connect() as db:
            db.begin_write()
            # A crashed send may have reached Telegram. Do not send that alert twice.
            db.execute("""UPDATE telegram_deliveries SET status='unknown',error='Delivery could not be confirmed. Read the update in Guuey.'
                WHERE status='sending' AND claimed_at<?""", (time.time() - 120,))
            db.execute("""UPDATE telegram_deliveries SET status='cancelled' WHERE status='pending' AND NOT EXISTS (
                SELECT 1 FROM telegram_links l JOIN task_notifications n ON n.id=telegram_deliveries.notification_id
                JOIN recurring_tasks t ON t.id=n.task_id WHERE l.id=telegram_deliveries.link_id AND l.owner=n.owner
                AND t.telegram_enabled=1 AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=l.owner AND u.disabled=1))""")
            row = db.execute("""SELECT d.*,n.title,n.content,n.sources,n.chat_id AS app_chat_id,l.chat_id
                FROM telegram_deliveries d JOIN telegram_links l ON l.id=d.link_id
                JOIN task_notifications n ON n.id=d.notification_id
                WHERE d.status='pending' AND d.due_at<=? AND l.bot_id=? ORDER BY d.due_at LIMIT 1""",
                             (time.time(), self.settings.bot_id)).fetchone()
            if not row:
                return None
            db.execute("UPDATE telegram_deliveries SET status='sending',claimed_at=?,attempts=attempts+1 WHERE notification_id=?",
                       (time.time(), row["notification_id"]))
            return {**dict(row), "attempts": row["attempts"] + 1}

    def finish_delivery(self, delivery, status, *, error=None, delay=0, message_id=None):
        with self.runs.connect() as db:
            db.execute("""UPDATE telegram_deliveries SET status=?,error=?,due_at=?,message_id=?,sent_at=?
                WHERE notification_id=? AND status='sending'""",
                       (status, error, time.time() + delay, message_id, now() if status == "sent" else None, delivery["notification_id"]))


def notification_text(delivery, settings):
    text = f"{delivery['title']}\n\n{delivery['content']}"
    if len(text.encode("utf-16-le")) > 4400:
        text = text.encode("utf-16-le")[:4398].decode("utf-16-le", errors="ignore") + "…"
    urls = []
    for source in json.loads(delivery["sources"]):
        url = source.get("url", "")
        try:
            parsed = urlsplit(url)
            if (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                    and not parsed.password and len(url) <= 300 and url not in urls):
                urls.append(url)
        except ValueError:
            continue
        if len(urls) == 3:
            break
    if urls:
        text += "\n\nSources:\n" + "\n".join(urls)
    if settings.public_url:
        text += "\n\nOpen in Guuey: " + settings.public_url + "/?" + urlencode({"chat": delivery["app_chat_id"]})
    # Telegram counts UTF-16 units; emoji can use two units per Python character.
    return text.encode("utf-16-le")[:8000].decode("utf-16-le", errors="ignore")
