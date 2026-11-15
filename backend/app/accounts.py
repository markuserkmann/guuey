import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pwdlib import PasswordHash

from app.schemas import ChatSource, ChatTurn
from app.store import ChatStore, now
from app.database import INTEGRITY_ERRORS, database_target

SESSION_SECONDS = 30 * 24 * 60 * 60
password_hasher = PasswordHash.recommended()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value):
        raise ValueError("Use 1–64 letters, numbers, dots, underscores or hyphens for the username")
    return value


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("Passwords must contain 12–1024 characters")
    return password_hasher.hash(password)


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    return password_hasher.hash(secrets.token_urlsafe(32))


class SeedMessage(ChatTurn):
    model_config = ConfigDict(extra="forbid")
    sources: list[ChatSource] = Field(default_factory=list)


class SeedChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    pinned: bool = False
    messages: list[SeedMessage] = Field(default_factory=list)


class SeedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chats: list[SeedChat]


class LoginRateLimited(Exception):
    pass


class AccountStore(ChatStore):
    def __init__(self, filename: str | None = None):
        super().__init__(filename)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT,
                    magic_hash TEXT UNIQUE,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at DOUBLE PRECISION NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    bucket TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seeded_chats (
                    user_id TEXT NOT NULL REFERENCES users(id),
                    seed_key TEXT NOT NULL,
                    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    PRIMARY KEY(user_id, seed_key)
                );
            """)

    def has_users(self) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM users WHERE disabled=0 LIMIT 1").fetchone() is not None

    def user(self, user_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT id,username FROM users WHERE id=? AND disabled=0", (user_id,)).fetchone()
        return dict(row) if row else None

    def create_user(self, username: str, password: str | None = None, *, magic: bool = True) -> tuple[dict, str | None]:
        username = normalize_username(username)
        if password is None and not magic:
            raise ValueError("A password or magic link is required")
        password_hash = hash_password(password) if password is not None else None
        token = secrets.token_urlsafe(32) if magic else None
        user = {"id": str(uuid4()), "username": username}
        with self.connect() as db:
            try:
                db.execute("INSERT INTO users(id,username,password_hash,magic_hash,created_at) VALUES(?,?,?,?,?)",
                           (user["id"], username, password_hash, digest(token) if token else None, now()))
            except INTEGRITY_ERRORS:
                raise ValueError("Username already exists") from None
        return user, token

    def update_credentials(self, username: str, *, password: str | None = None,
                           magic: bool | None = None, disabled: bool | None = None) -> str | None:
        username = normalize_username(username)
        password_hash = hash_password(password) if password is not None else None
        token = secrets.token_urlsafe(32) if magic else None
        with self.connect() as db:
            db.begin_write()
            user = db.execute("SELECT id,password_hash,magic_hash FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                raise ValueError("User not found")
            if magic is False and not user["password_hash"] and password_hash is None:
                raise ValueError("Set a password before revoking the only sign-in method, or disable the user")
            if password_hash is not None:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user["id"]))
            if magic is not None:
                db.execute("UPDATE users SET magic_hash=? WHERE id=?", (digest(token) if token else None, user["id"]))
            if disabled is not None:
                db.execute("UPDATE users SET disabled=? WHERE id=?", (disabled, user["id"]))
            db.execute("DELETE FROM auth_sessions WHERE user_id=?", (user["id"],))
        return token

    def limit_login(self, client: str, username: str | None = None) -> None:
        # Keep login limits shared across API processes and restarts.
        buckets = [("ip:" + digest(client), 30)]
        if username is not None:
            buckets.append(("user:" + digest(username.strip().lower()), 10))
        timestamp = time.time()
        with self.connect() as db:
            db.begin_write()
            db.execute("DELETE FROM login_attempts WHERE expires_at<=?", (timestamp,))
            for bucket, limit in buckets:
                row = db.execute("SELECT attempts FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
                if row and row["attempts"] >= limit:
                    raise LoginRateLimited
            for bucket, _ in buckets:
                db.execute("""INSERT INTO login_attempts VALUES(?,1,?)
                    ON CONFLICT(bucket) DO UPDATE SET attempts=login_attempts.attempts+1""", (bucket, timestamp + 300))

    def login(self, *, username: str | None = None, password: str = "", magic_token: str | None = None,
              previous_session: str | None = None) -> tuple[dict, str] | None:
        with self.connect() as db:
            if magic_token is not None:
                row = db.execute("SELECT * FROM users WHERE magic_hash=? AND disabled=0", (digest(magic_token),)).fetchone()
            else:
                row = db.execute("SELECT * FROM users WHERE username=? AND disabled=0",
                                 ((username or "").strip().lower(),)).fetchone()
                stored_hash = row["password_hash"] if row and row["password_hash"] else dummy_password_hash()
                valid = password_hasher.verify(password, stored_hash)
                if not valid or not row or not row["password_hash"]:
                    return None
            if not row:
                return None
            token = secrets.token_urlsafe(32)
            db.begin_write()
            # A credential reset or disable during password verification must win.
            current = db.execute("SELECT * FROM users WHERE id=? AND disabled=0", (row["id"],)).fetchone()
            if not current or (current["password_hash"], current["magic_hash"]) != (row["password_hash"], row["magic_hash"]):
                return None
            db.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (time.time(),))
            if previous_session:
                db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest(previous_session),))
            db.execute("INSERT INTO auth_sessions VALUES(?,?,?)", (digest(token), row["id"], time.time() + SESSION_SECONDS))
        return {"id": row["id"], "username": row["username"]}, token

    def session_owner(self, token: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self.connect() as db:
            row = db.execute("""SELECT u.id FROM auth_sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0""", (digest(token), time.time())).fetchone()
        return row["id"] if row else None

    def logout(self, token: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest(token),))

    def seed(self, username: str, data: SeedFile) -> int:
        username = normalize_username(username)
        keys = [chat.key for chat in data.chats]
        if len(keys) != len(set(keys)):
            raise ValueError("Seed chat keys must be unique")
        count = 0
        with self.connect() as db:
            db.begin_write()
            user = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                raise ValueError("User not found")
            for chat in data.chats:
                if db.execute("SELECT 1 FROM seeded_chats WHERE user_id=? AND seed_key=?", (user["id"], chat.key)).fetchone():
                    continue
                chat_id, timestamp = str(uuid4()), now()
                db.execute("INSERT INTO chats(id,owner,title,created_at,updated_at,pinned) VALUES(?,?,?,?,?,?)",
                           (chat_id, user["id"], chat.title, timestamp, timestamp, chat.pinned))
                for message in chat.messages:
                    db.execute("INSERT INTO messages(id,chat_id,role,content,sources,created_at,attachments) VALUES(?,?,?,?,?,?,?)",
                               (str(uuid4()), chat_id, message.role, message.content,
                                json.dumps([source.model_dump() for source in message.sources]), timestamp,
                                json.dumps([attachment.model_dump() for attachment in message.attachments])))
                db.execute("INSERT INTO chat_search VALUES(?,?,?,?)",
                           (chat_id, user["id"], chat.title, " ".join(message.content for message in chat.messages)))
                db.execute("INSERT INTO seeded_chats VALUES(?,?,?)", (user["id"], chat.key, chat_id))
                count += 1
        return count


@lru_cache(maxsize=4)
def _account_store(filename: str) -> AccountStore:
    return AccountStore(filename)


def get_accounts() -> AccountStore:
    return _account_store(database_target())


def magic_link(base_url: str, token: str) -> str:
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise ValueError("Base URL must be an HTTP(S) app URL without credentials, query or fragment")
    return base_url.rstrip("/") + "/#" + urlencode({"magic": token})


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Guuey users in CHAT_DATABASE_PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "password", "magic-link", "disable", "enable", "seed"):
        entry = sub.add_parser(command)
        entry.add_argument("username")
        if command in {"create", "password"}:
            passwords = entry.add_mutually_exclusive_group()
            passwords.add_argument("--password", action="store_true", help="Prompt for a password")
            passwords.add_argument("--password-env", metavar="NAME", help="Read password from this environment variable")
        if command in {"create", "magic-link"}:
            entry.add_argument("--base-url", default="http://localhost:18000")
        if command == "create":
            entry.add_argument("--no-magic-link", action="store_true")
        if command == "magic-link":
            entry.add_argument("--revoke", action="store_true")
        if command == "seed":
            entry.add_argument("file", help="JSON file, or - for stdin")
    sub.add_parser("list")
    args = parser.parse_args()
    try:
        if hasattr(args, "base_url"):
            magic_link(args.base_url, "validate")
        password = None
        if getattr(args, "password_env", None):
            password = os.environ.get(args.password_env)
            if not password:
                raise ValueError("Password environment variable is empty or unset")
        elif getattr(args, "password", False) or args.command == "password":
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Confirm password: "):
                raise ValueError("Passwords do not match")
        store = get_accounts()
        if args.command == "create":
            user, token = store.create_user(args.username, password, magic=not args.no_magic_link)
            print(f"Created {user['username']}")
            if token:
                print(magic_link(args.base_url, token))
        elif args.command == "magic-link":
            token = store.update_credentials(args.username, magic=not args.revoke)
            print(magic_link(args.base_url, token) if token else "Magic link revoked; existing sessions signed out")
        elif args.command == "password":
            store.update_credentials(args.username, password=password)
            print("Password updated; existing sessions signed out")
        elif args.command in {"disable", "enable"}:
            store.update_credentials(args.username, disabled=args.command == "disable")
            print(f"User {args.command}d")
        elif args.command == "seed":
            import sys
            content = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
            data = SeedFile.model_validate_json(content)
            print(f"Created {store.seed(args.username, data)} chats; existing seed keys skipped")
        elif args.command == "list":
            with store.connect() as db:
                for user in db.execute("SELECT username,disabled FROM users ORDER BY username"):
                    print(f"{user['username']}\t{'disabled' if user['disabled'] else 'active'}")
    except ValidationError:
        parser.exit(1, "Invalid seed file. See the documented seed format; no chats were imported.\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
