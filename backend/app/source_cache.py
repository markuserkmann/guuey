"""Persistent caches for public source pages and evidence verdicts."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit


POLICY_TTL = 7 * 86400
OFFER_TTL = 30 * 60
VERDICT_TTL = 30 * 86400
POLICY_PATH = re.compile(r"(?:shipping|delivery|toimitus|terms|conditions|ehdot|policy|legal|laki)", re.I)


class PersistentCache:
    def __init__(self, namespace: str):
        self.namespace = namespace
        chat_path = Path(os.getenv("CHAT_DATABASE_PATH", "runtime/chats.sqlite3"))
        self.path = Path(os.getenv("SOURCE_CACHE_PATH", str(chat_path.with_name("research-cache.sqlite3"))))

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS research_cache (namespace TEXT, key TEXT, expires REAL, value TEXT, PRIMARY KEY (namespace, key))")
            yield db
            db.commit()
        finally:
            db.close()

    def get(self, key: str):
        try:
            with self.connect() as db:
                row = db.execute("SELECT value FROM research_cache WHERE namespace=? AND key=? AND expires>?",
                                 (self.namespace, key, time.time())).fetchone()
            return json.loads(row[0]) if row else None
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return None

    def put(self, key: str, value, ttl_seconds: float):
        try:
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO research_cache VALUES (?, ?, ?, ?)",
                           (self.namespace, key, time.time() + ttl_seconds, json.dumps(value)))
                db.execute("DELETE FROM research_cache WHERE expires<=?", (time.time(),))
        except (OSError, sqlite3.Error, ValueError, TypeError):
            pass


def page_ttl(url: str, *, policy_request: bool = False) -> int:
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    official = {"finlex.fi", "kkv.fi", "kuluttajariita.fi", "kela.fi", "ytj.fi", "prh.fi", "eur-lex.europa.eu"}
    if policy_request or POLICY_PATH.search(parts.path) or any(host == item or host.endswith("." + item) for item in official):
        return POLICY_TTL
    return OFFER_TTL


def cached_sources(sources: list[dict]) -> list[dict]:
    result = deepcopy(sources)
    for source in result:
        source["cached_retrieval"] = source.get("retrieval", "page")
        source["retrieval"] = "cache"
        source["cache_hit"] = True
    return result


def policy_sources(domain: str) -> list[dict]:
    sources = PersistentCache("seller_policies").get(domain)
    return cached_sources([source for source in sources if usable_policy(source)]) if isinstance(sources, list) else []


def usable_policy(source: dict) -> bool:
    if not POLICY_PATH.search(urlsplit(source['url']).path) or not source.get('text', '').strip():
        return False
    observed = source.get('crawled_at') or source.get('observed_at') or source.get('retrieved_at')
    try:
        timestamp = datetime.fromisoformat(observed)
        return timestamp.tzinfo is not None and 0 <= (datetime.now(UTC) - timestamp).total_seconds() < POLICY_TTL
    except (TypeError, ValueError):
        return False


def delivery_source_current(source: dict) -> bool:
    if POLICY_PATH.search(urlsplit(source['url']).path):
        return usable_policy(source)
    if source.get('retrieval') not in {'live', 'page', 'cache'}:
        return False
    observed = source.get('crawled_at') or source.get('observed_at') or source.get('retrieved_at')
    try:
        timestamp = datetime.fromisoformat(observed)
        return timestamp.tzinfo is not None and 0 <= (datetime.now(UTC) - timestamp).total_seconds() < OFFER_TTL
    except (TypeError, ValueError):
        return False


def cache_policy_sources(sources: list[dict]) -> None:
    groups = {}
    for source in sources:
        if usable_policy(source):
            domain = (urlsplit(source["url"]).hostname or "").removeprefix("www.")
            groups.setdefault(domain, []).append(source)
    cache = PersistentCache("seller_policies")
    for domain, pages in groups.items():
        previous = [page for page in cache.get(domain) or [] if usable_policy(page)]
        merged = {page["url"]: page for page in [*previous, *pages]}
        cache.put(domain, list(merged.values()), POLICY_TTL)
