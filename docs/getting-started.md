# Getting started

[Documentation index](README.md)

## Requirements

Use Docker with Compose for the bundled services. For a host installation, use Python 3.12 or later, `uv`, and Node.js 22 with npm. The container images use Python 3.13 and Node.js 22.

Choose the credentials that match your intended features:

| Capability | Required configuration |
|---|---|
| Sign-in and persisted conversations | A named account created by the CLI, or the optional shared `AGENT_API_TOKEN` |
| Ordinary model replies | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| Research and recurring tasks | Gemini credentials and `EXA_API_KEY` |
| Remote browser tasks | Gemini credentials, `BROWSER_ENABLED=true`, `OXY_AB_USERNAME`, `OXY_AB_PASSWORD` |
| Website discovery inside remote browser tasks | `OXYLAB_KEY` or its alias `OXYLABS_WEB_API_KEY` |
| Private sign-in input and saved website logins | `BROWSER_SESSION_KEY` |
| Telegram notifications | Bot credentials, a running Telegram worker, a linked user, and per-task opt-in |
| Voice input | Gemini credentials, microphone permission, and HTTPS or localhost |

Exa research and Oxylabs browser discovery have separate credentials. A browser task can open a supplied website URL without the optional discovery key. Browser automation does not require Exa when it operates directly on a website.

## Docker setup

From the repository root:

```sh
cp .env.example .env
```

Fill in `.env`. The file is ignored by Git. For saved logins, generate the encryption key and paste the output into `BROWSER_SESSION_KEY`:

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Keep this key stable. The API and worker need the same value to read existing saved logins and private input responses.

Start the services:

```sh
docker compose up -d --build
docker compose ps
```

The `.env.example` setting `COMPOSE_FILE=compose.yaml:compose.dev.yaml` selects live reload. The base services are the API, task worker, and frontend; the optional Telegram worker uses a profile.

Create a named account:

```sh
docker compose exec backend /app/.venv/bin/python -m app.accounts create alice --password --base-url http://localhost:18000
```

Use a password of at least 12 characters. The command prints a reusable magic link once. See [accounts](accounts.md) for password-only accounts, link rotation, disabling users, and seeding conversations.

| Service | Default URL |
|---|---|
| Application | <http://localhost:18000> |
| API health | <http://localhost:18001/api/health> |
| Swagger UI | <http://localhost:18001/docs> |
| OpenAPI schema | <http://localhost:18001/openapi.json> |

`FRONTEND_PORT` and `BACKEND_PORT` change the host ports. Both published ports bind to `127.0.0.1` in the supplied Compose configuration.

## First task

Sign in, open a conversation, and enter a request with the constraints that matter: location, dates, budget, quantities, and preferences. Select Fast, Auto, or Deep for the research budget. Auto is the default.

For research, try a comparison request with a budget and delivery country. For a browser task, provide the website URL and ask to inspect stock or prepare a basket. The conversation shows progress, source links, and any input, approval, or takeover request. See [using Guuey](user-guide.md).

The Debug panel reports configuration and run diagnostics. A successful health request confirms that the API responds; it does not prove that provider credentials work or that the task worker is running.

## Enable Telegram delivery

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, and `APP_PUBLIC_URL` in `.env`, then run:

```sh
docker compose --profile telegram up -d --build
```

In **Recurring tasks > Telegram**, link and confirm the private Telegram account. Enable **Send matches to Telegram** on each desired task. See [Telegram](telegram.md) for the complete flow.

## Run directly on the host

The application reads process environment variables. The commands below explicitly load the root `.env` with `uv --env-file`; the API and worker do not load that file on their own. The inline database path overrides the container-specific `/data/chats.sqlite3` setting for local use.

Install backend dependencies from `backend/`:

```sh
cd backend
uv sync --frozen
```

Start the API in that directory:

```sh
CHAT_DATABASE_PATH=runtime/chats.sqlite3 uv run --env-file ../.env uvicorn app.main:app --reload
```

Start the worker in a second terminal, also from `backend/`:

```sh
CHAT_DATABASE_PATH=runtime/chats.sqlite3 uv run --env-file ../.env python -m app.worker
```

Create the account using the same environment and database:

```sh
CHAT_DATABASE_PATH=runtime/chats.sqlite3 uv run --env-file ../.env python -m app.accounts create alice --password --base-url http://localhost:5173
```

In another terminal, from the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to `http://127.0.0.1:8000`. Set `API_PROXY_TARGET` in `frontend/.env.local` if the API uses another address. `DEV_AUTH_TOKEN` can enable the explicitly selected shared development connection; see [configuration](configuration.md).

If you set `DATABASE_URL`, it takes precedence over `CHAT_DATABASE_PATH`. Keep the effective database target identical across the API, account CLI, worker, and Telegram worker.

For local Telegram delivery, run this from `backend/` in another terminal:

```sh
CHAT_DATABASE_PATH=runtime/chats.sqlite3 uv run --env-file ../.env python -m app.telegram_worker
```

## Optional local browser

Set `BROWSER_ENABLED=true` and `BROWSER_BACKEND=local`. From `backend/`, install the browser for the selected driver:

```sh
uv run playwright install chromium
```

For `BROWSER_DRIVER=patchright`, use `uv run patchright install chromium`. Local Chromium requires the host's browser system dependencies. `BROWSER_HEADLESS=false` opens a visible window when a graphical display is available. Local sessions do not provide the Oxylabs live-view iframe.

## Stop and restart

```sh
docker compose stop
docker compose up -d
```

The database remains in the named `chats-data` volume. Rebuild after dependency or Dockerfile changes; development source edits reload automatically. See [operations](operations.md) before replacing storage or changing encryption keys.
