# Accounts and authentication

[Documentation index](README.md)

Accounts are provisioned from the backend CLI; there is no public registration. The CLI,
API and worker must use the same database target (`DATABASE_URL` or `CHAT_DATABASE_PATH`). Docker commands below use the
existing shared data volume. Locally, run `uv run python -m app.accounts ...` from
`backend/` with the API's database path in the environment.

## Create an account

Create an account with both a password and a permanent magic link:

```sh
docker compose exec backend /app/.venv/bin/python -m app.accounts create alice --password --base-url http://localhost:18000
```

The command prompts for the password twice and prints the magic link once. Passwords
must be 12-1024 characters. Usernames are case-insensitive and use letters, numbers,
dots, underscores or hyphens. The first character must be a letter or number.
Use the public frontend URL for `--base-url` when sharing links. For password-only
accounts, add `--no-magic-link`; for magic-link-only accounts, omit `--password`.
Automation can use `--password-env VARIABLE_NAME` to read a password from the backend
process environment instead of prompting or putting it in command arguments.

## Credentials and sessions

Passwords use Argon2 hashes; only hashes of magic-link and session tokens are stored.
Magic links grant access to the account to anyone holding the link. They are reusable
until rotated, revoked or the account is disabled. The token is in the URL fragment,
which is removed from the address bar before sign-in and is not sent in HTTP URLs.
Sessions use an HttpOnly, SameSite=Strict cookie and expire after 30 days. Sign out
revokes the current session. Optional bearer-token access stays in tab memory and
clears on reload or sign out.
Set `AUTH_COOKIE_SECURE=true` for HTTPS deployments, including TLS reverse proxies.

## Manage accounts

```sh
docker compose exec backend /app/.venv/bin/python -m app.accounts list
docker compose exec backend /app/.venv/bin/python -m app.accounts password alice
docker compose exec backend /app/.venv/bin/python -m app.accounts magic-link alice --base-url http://localhost:18000
docker compose exec backend /app/.venv/bin/python -m app.accounts magic-link alice --revoke
docker compose exec backend /app/.venv/bin/python -m app.accounts disable alice
docker compose exec backend /app/.venv/bin/python -m app.accounts enable alice
```

The `magic-link` command replaces the previous link. Credential changes and disabling
an account revoke its existing sessions without deleting chats. Set a password before
revoking a magic-link-only account's link, or disable the account to block all access.

## Seed conversations

To pre-seed a user's chat history, copy `backend/examples/chats.seed.json` to
`chats.json` and edit it, or create a JSON file with this structure:

```json
{
  "chats": [
    {
      "key": "welcome-v1",
      "title": "Getting started",
      "pinned": true,
      "messages": [
        { "role": "user", "content": "What can I do here?" },
        { "role": "assistant", "content": "Ask a question or continue this conversation." }
      ]
    }
  ]
}
```

Import it into an existing account from the host:

```sh
docker compose exec -T backend /app/.venv/bin/python -m app.accounts seed alice - < chats.json
```

Locally: `uv run python -m app.accounts seed alice chats.json`. The same file can seed
many accounts; every account gets separate chat and message IDs. Each `key` is unique
within an account. Repeating an import skips existing keys and preserves subsequent
messages and pin changes. Change the key to add a new seed conversation. Imports are
validated and committed atomically. Messages support `sources` (`title`, `url`) and
the existing text `attachments` format; they do not trigger model calls. An empty
`messages` list creates an empty named chat.

## Shared workspace and ownership

The existing shared workspace remains available through **API token** or, in
development, **Use development connection**. Its chats are not automatically assigned
to a named user. Chats, runs, debug traces and saved browser logins are scoped to each
account's stable ID, so password or magic-link changes do not change their owner.

## Authentication API

Authentication endpoints:

- `POST /api/auth/login`: `{"username":"alice","password":"..."}`.
- `POST /api/auth/magic-link`: `{"token":"..."}` exchanges the reusable link token for a session.
- `GET /api/session`: returns the current user for account sessions.
- `POST /api/auth/logout`: revokes and clears the current session.

Send `X-Guuey-Request: 1` on sign-in, sign-out and all cookie-authenticated mutations.
Login requests are limited to 10 per username and 30 per client IP per five minutes.
Behind a proxy, configure Uvicorn to trust forwarded client IPs only from that proxy;
otherwise clients sharing the proxy also share its IP limit. Optional `X-Guuey-User`
binds a request to the user ID displayed in the browser, preventing stale tabs from
operating on a different signed-in account.

For environment loading and database selection, see [getting started](getting-started.md).
