"""Optional cross-process request budget for bounded live evaluations."""
import json
import os
import sqlite3
from datetime import UTC, datetime


def reserve(endpoint):
    path = os.getenv("EXA_USAGE_DATABASE")
    if not path:
        return None
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, endpoint TEXT, started TEXT, cost TEXT)")
        db.execute("BEGIN IMMEDIATE")
        configured_limit = os.getenv("EXA_REQUEST_LIMIT")
        limit = int(configured_limit) if configured_limit else None
        if limit is not None and db.execute("SELECT count(*) FROM requests").fetchone()[0] >= limit:
            from app.research import SearchUnavailable
            raise SearchUnavailable("The configured Exa request budget is exhausted.")
        identifier = db.execute("INSERT INTO requests(endpoint,started) VALUES(?,?)", (endpoint, datetime.now(UTC).isoformat())).lastrowid
    return path, identifier


def record(ticket, payload):
    if ticket:
        path, identifier = ticket
        with sqlite3.connect(path, timeout=10) as db:
            db.execute("UPDATE requests SET cost=? WHERE id=?", (json.dumps(payload.get("costDollars")), identifier))
