# Configuration reference

[Documentation index](README.md)

Start from [`.env.example`](../.env.example). The tables describe defaults in the current code and Compose files. Where the example deliberately sets a different value, it is shown separately.

## Loading settings

Compose reads the root `.env` for interpolation, then passes the variables listed in each service's `environment` block into that container. Adding an arbitrary variable to `.env` alone does not pass it into a container. Add an environment entry or an override file for settings not already mapped.

For host processes, export the variables or use `uv run --env-file ../.env` from `backend/`. Use a writable local `CHAT_DATABASE_PATH`, and keep the API, worker, account CLI, and Telegram worker on the same database. See [getting started](getting-started.md).

After changing container environment settings, recreate affected services with `docker compose up -d`. A plain container restart keeps its old environment. Model, research, and browser settings generally need to match between the API and worker.

## Authentication and deployment

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_API_TOKEN` | Unset | Optional bearer access to a shared owner; at least 32 printable ASCII characters without spaces |
| `AUTH_COOKIE_SECURE` | `false` | Force Secure cookies when serving behind HTTPS |
| `COMPOSE_FILE` | Example: `compose.yaml:compose.dev.yaml` | Select base services and optional development override |
| `FRONTEND_PORT` | `18000` | Frontend port published on host loopback |
| `BACKEND_PORT` | `18001` | Backend port published on host loopback |
| `APP_PUBLIC_URL` | Unset; example: `http://localhost:18000` | Public frontend URL for Telegram links back to conversations |

Generate an optional shared API token with:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Named accounts do not require `AGENT_API_TOKEN`. Everyone using the same bearer token shares that owner's chats, tasks, and saved website logins. Rotating the token changes its derived owner ID; it does not migrate the old owner's data.

## Storage

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_DATABASE_PATH` | `runtime/chats.sqlite3` on the host | SQLite application database; Compose explicitly uses `/data/chats.sqlite3` |
| `DATABASE_URL` | Unset | PostgreSQL connection URL; takes precedence over `CHAT_DATABASE_PATH` |
| `DATABASE_SSLMODE` | Driver default | Optional PostgreSQL SSL mode |
| `PGSSLROOTCERT` | Driver default | Optional PostgreSQL root certificate path |
| `SOURCE_CACHE_PATH` | `research-cache.sqlite3` beside `CHAT_DATABASE_PATH` | Separate SQLite cache for public pages, seller policies, and verifier verdicts |
| `EXA_USAGE_DATABASE` | Unset | Optional SQLite ledger for Exa request accounting |
| `EXA_REQUEST_LIMIT` | No ceiling | Maximum ledger-accounted requests when `EXA_USAGE_DATABASE` is configured |

`DATABASE_URL`, `DATABASE_SSLMODE`, `PGSSLROOTCERT`, `SOURCE_CACHE_PATH`, and the Exa ledger settings are not passed by the base Compose configuration. Add explicit entries for the services that need them. PostgreSQL is supported by the application connection layer; the supplied Compose stack provisions SQLite and does not provision PostgreSQL or migrate existing SQLite data.

The source cache and Exa ledger remain SQLite files even when the application database uses PostgreSQL. Set their paths deliberately on a host or custom deployment. The application creates and extends its schema when stores initialize; there is no separate migration CLI.

## Gemini and Exa

| Variable | Code/base default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Unset | Model, browser-agent, and dictation credentials |
| `GOOGLE_API_KEY` | Unset | Alternative Gemini credential; `GEMINI_API_KEY` takes precedence |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Ordinary chat and fallback model for research stages |
| `GEMINI_DECISION_MODEL` | `gemini-decision-1` | Workflow routing through the Decisions API; mapped on the worker in Compose |
| `GEMINI_PLANNING_MODEL` | `GEMINI_MODEL` when unset or blank | Auto/Deep planning; example sets `gemini-3.5-flash` |
| `GEMINI_FAST_PLANNING_MODEL` | `gemini-3.5-flash-lite` | Fast-mode planner |
| `GEMINI_RESEARCH_MODEL` | `GEMINI_MODEL` when unset or blank | Evidence extraction and answer generation; example sets `gemini-3.5-flash` |
| `GEMINI_VERIFICATION_MODEL` | `GEMINI_MODEL` when unset or blank | Semantic claim verification; example sets `gemini-3.5-flash` |
| `INITIAL_RESPONSE_ENABLED` | `true` | Brief streamed progress response for research and browser tasks |
| `GEMINI_INITIAL_RESPONSE_MODEL` | `gemini-3.5-flash-lite` | Model for that progress response |
| `EXA_API_KEY` | Unset | Exa search and page contents |

Planning uses minimal thinking. Stage overrides do not replace the ordinary chat or routing model. The brief initial response has a separate 128-token output limit, no retries, a two-second first-event timeout, and a four-second request deadline. It cannot replace the checked answer or delay completion. Compose passes these initial-response settings to the worker.

Research budgets are fixed in [`research_modes.py`](../backend/app/research_modes.py):

| Mode | Rounds | Deadline | Prompt characters | Live-crawl pages per round |
|---|---|---|---|---|
| Fast | 1 | 45 seconds | 150,000 | Up to 4 |
| Auto | 2 | 120 seconds | 320,000 | Up to 8 |
| Deep | 3 | 240 seconds | 600,000 | Up to 16 |

There is no environment variable for these profiles. The worker currently admits at most two running tasks. See [research](research.md) for prompt budgeting, refinement, cache behavior, and freshness.

## Voice input

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_TRANSCRIPTION_MODEL` | `gemini-3.5-transcribe-live` | Gemini Live dictation model |
| `GEMINI_TRANSCRIPTION_LANGUAGE` | `en-US` | BCP-47 language hint such as `fi-FI`, or `auto` |

The token endpoint can override the language with `en`, `fi`, or `auto`. Voice sessions use a single-use token, a one-minute connection window, and a five-minute expiry. See [voice input](voice-input.md).

## Browser provider and credentials

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_ENABLED` | `false` | Enable website tasks |
| `BROWSER_BACKEND` | `oxylabs` | `oxylabs` remote CDP browser or `local` Chromium |
| `BROWSER_DRIVER` | `playwright` | `playwright` or `patchright`; applies to either backend |
| `OXY_AB_USERNAME` | Unset | Oxylabs Agent Browser username |
| `OXY_AB_PASSWORD` | Unset | Oxylabs Agent Browser password |
| `OXYLAB_KEY` | Unset | Optional Oxylabs MCP website-discovery key |
| `OXYLABS_WEB_API_KEY` | Unset | Alias used when `OXYLAB_KEY` is absent |
| `BROWSER_COUNTRY` | Unset | Two-letter exit country code; blank uses provider routing |
| `BROWSER_MODEL` | `gemini-3.5-flash-lite` | Browser decision model |
| `BROWSER_SESSION_KEY` | Unset | 32-byte encryption key encoded as 64 hexadecimal characters |
| `BROWSER_HEADLESS` | `true` | Local Chromium only; `false` requires a graphical display |

Generate `BROWSER_SESSION_KEY` with:

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

A valid key enables importing/saving logins and collecting private sign-in inputs. Replacing it makes existing encrypted state unreadable. Keep the API and worker key identical. Saved browser state uses AES-GCM; ordinary conversations and research records are not encrypted by this feature.

## Browser limits and action checks

| Variable | Default | Accepted values or range |
|---|---|---|
| `BROWSER_DEADLINE_S` | `1800` | 1 to 3540 seconds per initial run |
| `BROWSER_IDLE_TIMEOUT_S` | `60` | 1 to 300 seconds to retain a page for same-chat follow-ups |
| `BROWSER_MAX_STEPS` | `120` | 1 to 1000 tool calls |
| `BROWSER_REQUEST_TIMEOUT_S` | `900` | 1 to 3540 seconds for input, approval, or takeover responses |
| `BROWSER_TOOL_RETRIES` | `5` | 1 to 20 corrections of invalid tool arguments |
| `BROWSER_THINKING_LEVEL` | `minimal` | `minimal`, `low`, `medium`, or `high` |
| `BROWSER_MODEL_MAX_TOKENS` | `8192` | 1 to 65536 tokens per response, including reasoning |
| `BROWSER_MODEL_TIMEOUT_S` | `180` | 1 to 600 seconds per model request |
| `BROWSER_FINAL_ACTION_PATTERNS` | Empty | Additional regular expression for final-action controls |

User waits can extend the initial deadline, subject to the run's 59-minute hard limit. Tool retries do not authorize repeating a final submission. Custom action patterns supplement the built-in checks. Invalid enum values, booleans, ranges, country codes, and regular expressions fail configuration validation.

## Telegram

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Unset | Dedicated bot credential kept on the server |
| `TELEGRAM_BOT_USERNAME` | Unset | Bot username, with or without a leading `@` |
| `APP_PUBLIC_URL` | Unset | Base URL for Open in Guuey links |

The API needs the bot settings for linking and task opt-in. The `telegram` service needs the same bot identity and application database. The worker uses outbound polling; no webhook is configured. See [Telegram](telegram.md).

## Combined Runtime deployment

[`Dockerfile.aiven`](../Dockerfile.aiven) builds the frontend, API, and workers into
one image for the Runtime entrypoint. These settings apply to that image, not to
the standard three-service Compose setup:

| Variable | Default | Purpose |
|---|---|---|
| `AIVEN_CA_CERT_BASE64` | Unset | Base64-encoded project CA certificate, written to a private temporary file for PostgreSQL TLS |
| `BOOTSTRAP_USERNAME` | Unset | First account username on a fresh database when no valid shared token is configured |
| `BOOTSTRAP_PASSWORD` | Unset | First account password; subject to the normal password rules |
| `TELEGRAM_ENABLED` | `false` | Start the supervised Telegram process when set to `true` |
| `STATIC_DIRECTORY` | Image: `/app/static` | Built frontend directory mounted by `app.web` |
| `LOCAL_DEVELOPMENT` | Unset | `true` skips the Runtime entrypoint's mandatory TLS-mode/CA checks for local testing |

The image sets `DATABASE_SSLMODE=verify-full`, `AUTH_COOKIE_SECURE=true`, and
`SOURCE_CACHE_PATH=/tmp/research-cache.sqlite3`. The entrypoint requires PostgreSQL,
initializes storage before starting services, and removes bootstrap credentials
from the environment passed to Supervisor. See [Aiven Runtime deployment](aiven-runtime.md)
and the [Runtime entrypoint](architecture.md#runtime-entrypoint).

## Frontend development

| Variable | Default | Purpose |
|---|---|---|
| `API_PROXY_TARGET` | `http://127.0.0.1:8000` | Vite's server-side `/api` proxy target |
| `DEV_AUTH_TOKEN` | Unset | Optional development-only shared bearer injection when explicitly selected |

Put local Vite settings in `frontend/.env.local`. Docker development sets the proxy to `http://backend:8000` and the development token from `AGENT_API_TOKEN`. Do not use a `VITE_` prefix for credentials: Vite exposes such variables to browser code. Production uses the Nginx proxy in [`frontend/nginx.conf`](../frontend/nginx.conf).

## Evaluation override

[`compose.eval.yaml`](../compose.eval.yaml) disables browser tasks, selects the research-stage models, and enables persistent Exa accounting with a 280-request ceiling. That ceiling belongs to the supplied evaluation configuration. It is not a default application quota or a monetary spending cap. Existing ledger usage survives restarts.
