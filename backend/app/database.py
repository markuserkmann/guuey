"""Connections shared by the chat, account, and worker stores."""

import os
import re
import sqlite3
import time
from threading import BoundedSemaphore
from contextlib import contextmanager
from pathlib import Path

import psycopg
from sqlparams import SQLParams


INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)
PARAMETERS = SQLParams("qmark", "format", expand_tuples=False)
WRITE_LOCK = 1829860713
POSTGRES_CONNECTION_SLOTS = BoundedSemaphore(3)


def database_target(filename=None):
    if filename is not None:
        return str(filename)
    configured = os.getenv("DATABASE_URL")
    if configured:
        if not configured.startswith(("postgres://", "postgresql://")):
            raise ValueError("DATABASE_URL must use PostgreSQL")
        return configured
    return os.getenv("CHAT_DATABASE_PATH", "runtime/chats.sqlite3")


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in cursor.description or ()]
    return lambda values: Row(zip(names, values))


class Connection:
    def __init__(self, raw, postgres):
        self.raw = raw
        self.postgres = postgres
        self.order_column = "position" if postgres else "rowid"
        self.text_aggregate = "string_agg" if postgres else "group_concat"
        self.greatest = "GREATEST" if postgres else "MAX"
        self.write_locked = False

    def execute(self, query, parameters=None):
        if self.postgres and parameters is not None:
            query, parameters = PARAMETERS.format(query, parameters)
            parameters = [int(value) if isinstance(value, bool) else value for value in parameters]
        return self.raw.execute(query, parameters) if parameters is not None else self.raw.execute(query)

    def executemany(self, query, parameters):
        if not self.postgres:
            return self.raw.executemany(query, parameters)
        query, parameters = PARAMETERS.formatmany(query, parameters)
        parameters = [[int(value) if isinstance(value, bool) else value for value in row] for row in parameters]
        cursor = self.raw.cursor()
        cursor.executemany(query, parameters)
        return cursor

    def executescript(self, script):
        if not self.postgres:
            return self.raw.executescript(script)
        self.begin_write()
        return self.raw.execute(script, prepare=False)

    def begin_write(self):
        if self.write_locked:
            return
        # Preserve SQLite's serialized read-modify-write transactions across workers.
        if self.postgres:
            self.execute("SELECT pg_advisory_xact_lock(?)", (WRITE_LOCK,))
        else:
            self.raw.execute("BEGIN IMMEDIATE")
        self.write_locked = True

    def columns(self, table):
        if not re.fullmatch(r"[a-z_]+", table):
            raise ValueError("Invalid table name")
        if self.postgres:
            rows = self.execute("SELECT column_name AS name FROM information_schema.columns "
                                "WHERE table_schema=current_schema() AND table_name=?", (table,))
        else:
            rows = self.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in rows}

    def add_order_column(self, table):
        if table not in {"messages", "runs", "task_seen_items"}:
            raise ValueError("Invalid ordered table")
        if self.postgres and "position" not in self.columns(table):
            self.execute(f"ALTER TABLE {table} ADD COLUMN position BIGINT GENERATED ALWAYS AS IDENTITY")
            self.execute(f"CREATE UNIQUE INDEX {table}_position ON {table}(position)")


@contextmanager
def connect(target):
    if target.startswith(("postgres://", "postgresql://")):
        with POSTGRES_CONNECTION_SLOTS:
            with _connect(target) as db:
                yield db
    else:
        with _connect(target) as db:
            yield db


@contextmanager
def _connect(target):
    postgres = target.startswith(("postgres://", "postgresql://"))
    if postgres:
        options = {"connect_timeout": 10, "application_name": "guuey"}
        if os.getenv("DATABASE_SSLMODE"):
            options["sslmode"] = os.environ["DATABASE_SSLMODE"]
        if os.getenv("PGSSLROOTCERT"):
            options["sslrootcert"] = os.environ["PGSSLROOTCERT"]
        raw = psycopg.connect(target, row_factory=row_factory, **options)
    else:
        if "://" in target:
            raise ValueError("DATABASE_URL must use PostgreSQL")
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(path, timeout=10)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("PRAGMA journal_mode = WAL")
        raw.execute("PRAGMA secure_delete = ON")
    try:
        yield Connection(raw, postgres)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


@contextmanager
def coordinator_lock(target):
    if not target.startswith(("postgres://", "postgresql://")):
        yield lambda: None
        return
    with connect(target) as db:
        db.raw.autocommit = True
        # A replacement container can serve HTTP while its worker waits for the old one to stop.
        while not db.execute("SELECT pg_try_advisory_lock(?)", (WRITE_LOCK + 1,)).fetchone()[0]:
            time.sleep(2)
        yield lambda: db.execute("SELECT 1").fetchone()
