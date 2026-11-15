"""Initialize a Runtime deployment before starting its API and workers."""

import base64
import os
import time
from pathlib import Path

import psycopg

from app.accounts import AccountStore
from app.auth import valid_api_token
from app.runs import RunStore


def initialize():
    if not os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        raise ValueError("Set DATABASE_URL using the Aiven PostgreSQL integration.")
    local = os.getenv("LOCAL_DEVELOPMENT", "").lower() == "true"
    if not local and os.getenv("DATABASE_SSLMODE") != "verify-full":
        raise ValueError("Runtime requires DATABASE_SSLMODE=verify-full.")
    certificate = os.getenv("AIVEN_CA_CERT_BASE64")
    if certificate:
        decoded = base64.b64decode(certificate, validate=True)
        if b"-----BEGIN CERTIFICATE-----" not in decoded:
            raise ValueError("AIVEN_CA_CERT_BASE64 must contain the project CA certificate.")
        path = Path("/tmp/guuey-postgres-ca.pem")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(decoded)
        os.environ["PGSSLROOTCERT"] = str(path)
    if not local and not os.getenv("PGSSLROOTCERT"):
        raise ValueError("Set AIVEN_CA_CERT_BASE64 to the Aiven project CA certificate.")

    for attempt in range(30):
        try:
            accounts = AccountStore()
            RunStore()
            break
        except psycopg.OperationalError:
            if attempt == 29:
                raise RuntimeError("PostgreSQL is unavailable; check the integration and TLS settings.") from None
            time.sleep(2)

    if not accounts.has_users() and not valid_api_token(os.getenv("AGENT_API_TOKEN", "")):
        username = os.getenv("BOOTSTRAP_USERNAME")
        password = os.getenv("BOOTSTRAP_PASSWORD")
        if not username or not password:
            raise ValueError("Set BOOTSTRAP_USERNAME and BOOTSTRAP_PASSWORD for the first deployment.")
        with accounts.connect() as db:
            db.begin_write()
            empty = db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
            if empty:
                from app.accounts import normalize_username, hash_password
                from app.store import now
                from uuid import uuid4
                db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                           (str(uuid4()), normalize_username(username), hash_password(password), now()))


def main():
    try:
        initialize()
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
    os.environ.pop("BOOTSTRAP_PASSWORD", None)
    os.environ.pop("BOOTSTRAP_USERNAME", None)
    os.environ.setdefault("TELEGRAM_ENABLED", "false")
    os.execvp("supervisord", ["supervisord", "--configuration", "/app/supervisord.conf"])


if __name__ == "__main__":
    main()
