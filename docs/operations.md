# Deployment and operations

[Documentation index](README.md)

## Service layout

The base Compose stack runs `frontend`, `backend`, and `worker`. The optional `telegram` profile adds Telegram polling and delivery. The API and worker share the `chats-data` volume at `/data`; the frontend reaches the API through its proxy.

Use the production container layout explicitly:

```sh
docker compose -f compose.yaml up -d --build
```

With Telegram enabled:

```sh
docker compose -f compose.yaml --profile telegram up -d --build
```

The root example selects the development override by default. Explicit `-f compose.yaml` omits live reload and the shared development connection. Host ports bind to loopback. Place an HTTPS reverse proxy in front of the frontend when serving remote users, set `AUTH_COOKIE_SECURE=true`, and set `APP_PUBLIC_URL` to the externally reachable frontend URL. For the combined PostgreSQL-backed image, see [Aiven Runtime deployment](aiven-runtime.md).

Keep `/api` on the same public origin as the app. Preserve streaming responses and disable proxy buffering for SSE. The bundled Nginx configuration already disables buffering and allows a 360-second proxy read timeout. Configure forwarded-client-IP trust in Uvicorn only for your proxy so sign-in throttling sees the intended clients.

## Storage and backups

Application records use `/data/chats.sqlite3` in the supplied deployment. The adjacent research cache stores public source material and verdicts. An evaluation override may add another SQLite request ledger. Keep the application database and `BROWSER_SESSION_KEY` available across restarts.

Use SQLite's backup API or a consistent snapshot rather than copying only a live `.sqlite3` file and omitting its WAL. This example backs up the default database inside the running backend, then copies it out:

```sh
docker compose exec -T backend /app/.venv/bin/python -c 'import sqlite3; source=sqlite3.connect("/data/chats.sqlite3"); target=sqlite3.connect("/tmp/guuey-backup.sqlite3"); source.backup(target); target.close(); source.close()'
docker compose cp backend:/tmp/guuey-backup.sqlite3 ./guuey-backup.sqlite3
```

Choose a distinct destination for each retained backup. Store configuration secrets, especially `BROWSER_SESSION_KEY`, separately with access restricted to operators. Without the original key, restored encrypted website logins and pending private inputs cannot be decrypted. Conversations and research records are not encrypted by that feature.

For restoration, stop all writers (`backend`, `worker`, and `telegram` if active), preserve the existing volume, and replace the database using a consistent restore procedure that handles SQLite sidecar files. Restore matching configuration and permissions before starting the services. Test restoration on a separate deployment first. `docker compose down` normally retains named volumes; adding `-v` removes them and deletes the stored application data.

For PostgreSQL deployments, back up the database using PostgreSQL tooling and retain any separate local research cache or Exa usage ledger as required. See [configuration](configuration.md) for the connection variables and Compose mapping requirements. Switching `DATABASE_URL` does not migrate existing SQLite data.

## Upgrade and configuration changes

Back up persistent data before updating code that changes schemas. Rebuild after dependency or Dockerfile changes:

```sh
docker compose -f compose.yaml up -d --build
```

Include `--profile telegram` if the Telegram worker is in use. The Telegram service does not receive the development source mounts, so code changes require its rebuild/restart even during development.

Run `up -d` to recreate services after environment changes; `restart` alone keeps the existing environment. Keep provider and browser settings aligned between the API and task worker, and keep the database target aligned across every process and the account CLI.

The worker startup recovery marks previously running tasks as interrupted and clears warm browser reservations. Allow important work to finish before restarting. Saved results and task schedules persist, but a live page cannot survive worker termination. Inspect uncertain browser actions before attempting another purchase or booking.

Run one task supervisor against a SQLite database. PostgreSQL deployments coordinate supervisors through an advisory lock, so a replacement waits before it recovers and schedules jobs. Telegram polling has a separate database lease for electing a single poller.

## Health and logs

```sh
docker compose ps
curl --fail http://127.0.0.1:18001/api/health
docker compose logs --tail=100 backend worker
```

For Telegram:

```sh
docker compose --profile telegram logs --tail=100 telegram
```

Health reports configuration flags, not successful provider calls or worker liveness. Verify a small task through the app after changing provider settings. The Debug panel and `/api/runs/{run_id}/debug` expose routing, retrieval, evidence, model calls, and stop reasons.

Logs, screenshots, chat content, and diagnostics can contain user or website data. Restrict access to the volume and any exported artifacts. Configured-secret redaction is not a general-purpose personal-data scrubber.

## Troubleshooting

| Symptom | Check or action |
|---|---|
| Cannot sign in after a fresh install | Create a named account using the same database as the API, or configure a valid shared bearer token |
| Ordinary chat returns mock text | Configure Gemini credentials on the process executing the request |
| Health works but runs stay queued | Start the worker; verify its database target and inspect its logs |
| API sees chats but worker/account CLI does not | Compare effective `DATABASE_URL` and `CHAT_DATABASE_PATH`; relative paths depend on the working directory |
| Local startup fails opening `/data/chats.sqlite3` | Override the container path with a writable host path as shown in getting started |
| Research cannot retrieve sources | Verify `EXA_API_KEY` on the worker, provider access, and any Exa request-ledger ceiling |
| Research returns Reported or To confirm fields | Inspect freshness, missing requirements, and sources; try a suitable deeper run or browser check |
| Browser unavailable | Enable it on API and worker and configure Gemini plus the selected backend credentials |
| Browser asks for a URL | Supply the target URL or configure the separate Oxylabs discovery key |
| Remote browser connection fails | Check Agent Browser credentials and provider access; the remote backend does not silently launch local Chromium |
| Page is blocked or needs human input | Use the live-view link and takeover flow; inspect current page state before resuming |
| Preview contains missing images | Text-only browser tasks block images, media, and fonts; image loading requires a subsequent navigation |
| Saved login import/private input unavailable | Check the 64-hex-character `BROWSER_SESSION_KEY` and ensure API/worker use the same key |
| Saved login cannot decrypt | Restore the original key, or delete and re-import the login if intentionally replacing it |
| Cookie import returns 409 | Wait for the task using that site's login to finish; the login is exclusively leased |
| New submission blocked after a previous attempt | Inspect the website account and resolve the uncertain action in Saved logins |
| Follow-up opens a new page | The warm-session window expired, the worker restarted, or the connection/login changed |
| Recurring task is quiet | Inspect run history: a successful no-match check differs from a failed/incomplete check; only new supported matches notify |
| Recurring schedule differs from a wall-clock time | Schedules use elapsed-minute intervals; timezone supplies research context, not cron timing |
| Telegram link never appears for confirmation | Run the Telegram worker, verify bot identity, open the invitation, and press Start within ten minutes |
| Telegram worker refuses to start polling | Use a dedicated bot without an existing webhook or another polling consumer |
| Linked Telegram receives no alerts | Enable Telegram on that task; old inbox entries are not backfilled; inspect delivery status |
| Telegram delivery is unconfirmed | Inspect Telegram before retrying; ambiguous sends are not retried automatically |
| Microphone unavailable | Use HTTPS or localhost and allow microphone access |
| Voice token/session fails | Check backend Gemini credentials, configured model access, and direct browser connectivity to Gemini |
| New settings have no effect | Check service environment mappings, recreate the affected containers, and reload the frontend if needed |

## Operating limits

Research and browser work are bounded by time, requests, and source access. Search results can miss events, pages can be stale or inaccessible, and interactive sites can change their controls. Final-action recognition uses heuristics. Preserve evidence and review unknown outcomes rather than assuming a completed tool call proves a completed order.

Recurring monitoring uses Exa research and model evaluation, with duplicate suppression and fresh-page requirements. It is not an exhaustive feed. Telegram sends bounded excerpts and source links; the complete result stays in Guuey. Email, browser push, and operating-system notifications are not implemented.
