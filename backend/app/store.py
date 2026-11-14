from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(UTC).isoformat()


class ChatStore:
    def __init__(self, filename: str | None = None):
        self.path = Path(filename or os.getenv("CHAT_DATABASE_PATH", "runtime/chats.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chats_owner_updated
                    ON chats(owner, updated_at DESC);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    sources TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_chat_created
                    ON messages(chat_id, created_at);
                CREATE VIRTUAL TABLE IF NOT EXISTS chat_search USING fts5(
                    chat_id UNINDEXED, owner UNINDEXED, title, content
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            if "pinned" not in {row["name"] for row in db.execute("PRAGMA table_info(chats)")}:
                db.execute("ALTER TABLE chats ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            if "attachments" not in {row["name"] for row in db.execute("PRAGMA table_info(messages)")}:
                db.execute("ALTER TABLE messages ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, owner: str) -> dict:
        identifier, timestamp = str(uuid4()), now()
        with self.connect() as db:
            db.execute("INSERT INTO chats(id, owner, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                       (identifier, owner, "New chat", timestamp, timestamp))
            db.execute("INSERT INTO chat_search VALUES (?, ?, ?, '')",
                       (identifier, owner, "New chat"))
        return {"id": identifier, "title": "New chat", "updated_at": timestamp}

    def list(self, owner: str, query: str = "") -> list[dict]:
        tokens = re.findall(r"[\w-]+", query.casefold())[:8]
        fts_query = " AND ".join(f'"{token}"*' for token in tokens)
        with self.connect() as db:
            if fts_query:
                rows = db.execute("""
                    SELECT c.id, c.title, c.updated_at, c.pinned FROM chat_search s
                    JOIN chats c ON c.id=s.chat_id
                    WHERE chat_search MATCH ? AND s.owner=?
                    ORDER BY c.pinned DESC, c.updated_at DESC LIMIT 100
                """, (fts_query, owner)).fetchall()
            else:
                rows = db.execute("""
                    SELECT id, title, updated_at, pinned FROM chats WHERE owner=?
                    ORDER BY pinned DESC, updated_at DESC LIMIT 100
                """, (owner,)).fetchall()
        return [dict(row) for row in rows]

    def detail(self, owner: str, chat_id: str) -> dict | None:
        with self.connect() as db:
            chat = db.execute("SELECT id, title, updated_at, pinned FROM chats WHERE owner=? AND id=?",
                              (owner, chat_id)).fetchone()
            if chat is None:
                return None
            messages = db.execute("""
                SELECT id, role, content, sources, created_at, attachments FROM messages
                WHERE chat_id=? ORDER BY rowid
            """, (chat_id,)).fetchall()
        return {**dict(chat), "messages": [
            {**dict(row), "sources": json.loads(row["sources"]), "attachments": json.loads(row["attachments"])} for row in messages
        ]}

    def pin(self, owner: str, chat_id: str, pinned: bool) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "UPDATE chats SET pinned=? WHERE owner=? AND id=? RETURNING id,title,updated_at,pinned",
                (pinned, owner, chat_id),
            ).fetchone()
        return dict(row) if row else None

    def append_exchange(self, owner: str, chat_id: str, prompt: str, reply: str,
                        sources: list[dict] | None = None, attachments: list[dict] | None = None) -> dict | None:
        timestamp = now()
        user_message = {"id": str(uuid4()), "role": "user", "content": prompt,
                        "sources": [], "created_at": timestamp, "attachments": attachments or []}
        assistant_message = {"id": str(uuid4()), "role": "assistant", "content": reply,
                             "sources": sources or [], "created_at": timestamp}
        with self.connect() as db:
            chat = db.execute("SELECT title FROM chats WHERE owner=? AND id=?",
                              (owner, chat_id)).fetchone()
            if chat is None:
                return None
            db.executemany("INSERT INTO messages(id, chat_id, role, content, sources, created_at, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", [
                (user_message["id"], chat_id, "user", prompt, "[]", timestamp, json.dumps(attachments or [])),
                (assistant_message["id"], chat_id, "assistant", reply,
                 json.dumps(sources or [], separators=(",", ":")), timestamp, "[]"),
            ])
            title = prompt.replace("\n", " ").strip()[:80] if chat["title"] == "New chat" else chat["title"]
            db.execute("UPDATE chats SET title=?, updated_at=? WHERE id=?",
                       (title, timestamp, chat_id))
            db.execute("DELETE FROM chat_search WHERE chat_id=?", (chat_id,))
            contents = db.execute("SELECT group_concat(content, ' ') FROM messages WHERE chat_id=?",
                                  (chat_id,)).fetchone()[0] or ""
            db.execute("INSERT INTO chat_search VALUES (?, ?, ?, ?)",
                       (chat_id, owner, title, contents))
        return {"user": user_message, "assistant": assistant_message}
