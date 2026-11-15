# API reference

[Documentation index](README.md)

The API uses JSON under `/api`. The default direct backend URL is `http://localhost:18001` in Docker or `http://localhost:8000` on the host. The frontend proxies the same paths. FastAPI publishes `/docs`, `/redoc`, and `/openapi.json` on the backend; consult the running schema for exact request models.

## Authentication

`GET /api/health` is public. Sign-in endpoints exchange credentials for a session. Application endpoints require a valid named-user session or the optional shared bearer token.

| Method and path | Body or behavior |
|---|---|
| `POST /api/auth/login` | `{"username":"alice","password":"your-password"}` |
| `POST /api/auth/magic-link` | `{"token":"your-magic-link-token"}` |
| `POST /api/auth/logout` | Revoke the current cookie session; returns 204 |
| `GET /api/session` | Returns `authenticated` and, for a named account, `user` |

Send `X-Guuey-Request: 1` for sign-in, sign-out, and every cookie-authenticated mutation. The session cookie is `guuey_session`, with HttpOnly, SameSite=Strict, and path `/api`. Sessions expire after 30 days. Set `AUTH_COOKIE_SECURE=true` behind HTTPS. Optional `X-Guuey-User` binds the request to the expected account ID and rejects stale account state.

For bearer authentication, use `Authorization: Bearer <AGENT_API_TOKEN>`. A bearer header takes precedence over a cookie and represents the shared token owner. Never put this token into frontend bundles. Username logins allow 10 attempts per username and 30 per client IP per five minutes; throttled responses return 429 and `Retry-After: 300`.

## Health and conversations

| Method and path | Behavior |
|---|---|
| `GET /api/health` | Configuration status: `status`, `mode`, `version`, `model`, `auth_configured`, `exa_configured`, `browser_enabled`, `saved_logins_enabled` |
| `GET /api/chats?query=...` | Search this owner's titles and message text; query up to 100 characters; up to 100 chats |
| `POST /api/chats` | Create an empty conversation; returns 201 |
| `GET /api/chats/{chat_id}` | Conversation metadata and ordered messages with sources and attachments |
| `PATCH /api/chats/{chat_id}` | Set pin state with `{"pinned":true}` |
| `POST /api/chats/{chat_id}/messages` | Synchronous compatibility endpoint; accepts `message`, optional `search`, and optional `attachments` |

Use the run endpoints for the worker-driven UI, streamed progress, browser requests, and persisted execution state. The synchronous endpoint returns `user`, `assistant`, and `debug` in the response and does not expose the queued browser workflow.

## Create and follow a run

| Method and path | Behavior |
|---|---|
| `POST /api/chats/{chat_id}/runs` | Queue a run; returns 202 |
| `GET /api/chats/{chat_id}/runs/latest` | Read the most recent run, or null when there is none |
| `GET /api/runs/{run_id}` | Read saved run state and browser state |
| `GET /api/runs/{run_id}/debug` | Read this run's diagnostics |
| `GET /api/runs/{run_id}/events` | Server-sent stream of state snapshots |
| `POST /api/runs/{run_id}/cancel` | Cancel the run and return its saved state |
| `POST /api/runs/{run_id}/continue` | Queue an eligible partial research run in Deep mode; returns 202 |

Example run body, with a new UUID for each logical submission:

```json
{
  "message": "Compare three laptops under €900 that ship to Finland.",
  "mode": "auto",
  "force_browser": false,
  "request_id": "e62237e0-50bf-425e-8af0-7adcd5b9a7ad",
  "timezone": "Europe/Helsinki",
  "attachments": [
    {"name": "preferences.md", "content": "Prefer a Finnish keyboard and a matte screen."}
  ]
}
```

| Field | Constraints |
|---|---|
| `message` | Required, 1 to 12,000 characters |
| `request_id` | Required UUID; reuse it only for a retry of the same payload |
| `mode` | `auto`, `fast`, or `deep`; default `auto` |
| `force_browser` | Boolean; default `false`; explicit browser opt-out in the message still takes precedence |
| `timezone` | Valid IANA timezone; default `UTC`, up to 100 characters |
| `attachments` | Default empty; up to three UTF-8 files, 50 KiB each and 100 KiB combined |

An existing request ID with different content returns 409. Only one queued/running run is allowed per conversation. Status is one of `queued`, `running`, `completed`, `partial`, `failed`, or `cancelled`. `phase` describes the current operation or stop reason. `result` and `live_output` can be null; checked results can appear before the run ends.

Deep continuation requires a partial, non-Deep research run with no newer turn. Browser and recurring-task runs cannot use this endpoint. Retrying a submission, continuing research, sending a new follow-up, and rerunning a scheduled task are distinct operations.

For a bearer client, set `GUUEY_API_TOKEN` in your shell without placing its value in source, then create a conversation:

```sh
curl --fail-with-body -X POST http://localhost:18001/api/chats \
  -H "Authorization: Bearer ${GUUEY_API_TOKEN}"
```

Save the JSON run body above as `run.json`, use the returned chat ID, and submit it:

```sh
curl --fail-with-body -X POST "http://localhost:18001/api/chats/${CHAT_ID}/runs" \
  -H "Authorization: Bearer ${GUUEY_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  --data-binary @run.json
```

## Event stream

```sh
curl --no-buffer "http://localhost:18001/api/runs/${RUN_ID}/events" \
  -H "Authorization: Bearer ${GUUEY_API_TOKEN}"
```

Events use `event: run`, an `id` containing `updated_at`, and a JSON `data` snapshot. Snapshots include `id`, `status`, `phase`, `updated_at`, `live_output`, `result`, and `browser`. The stream checks saved state every 0.2 seconds, emits changed state, sends a keepalive comment after 15 seconds without changes, and closes after a terminal status.

This is a current-state stream, not a replayable event log. Reconnect or fetch the run to recover its latest saved state; the server does not replay historical events from `Last-Event-ID`. Browser state includes pending requests, action status, steps, and an owner-only live-view URL while the task is active. SSE result sources contain title and URL; full diagnostics use the debug endpoint.

## Browser requests and saved logins

| Method and path | Behavior |
|---|---|
| `POST /api/runs/{run_id}/browser/requests/{request_id}` | Answer a pending approval, input, or takeover request |
| `GET /api/runs/{run_id}/browser/requests/{request_id}/screenshot` | Approval screenshot as JPEG with `Cache-Control: no-store` |
| `GET /api/browser/sessions` | Saved-login metadata, status, revisions, and cookie counts; no cookie values |
| `POST /api/browser/sessions/import` | Import site cookies and return metadata plus accepted/rejected counts |
| `DELETE /api/browser/sessions/{session_id}` | Remove a login and revoke its active lease; returns 204 |
| `GET /api/browser/actions` | List unresolved submissions for this owner |
| `POST /api/browser/actions/{action_id}/resolve` | Record a checked outcome for an uncertain action; returns 204 |

Answer only with the body matching the request's `kind`:

| Request kind | Body |
|---|---|
| Approval | `{"decision":"approve"}` or `{"decision":"decline"}` |
| Input | `{"values":{"field_id":"value"}}`, using the requested field IDs |
| Takeover | `{"done":true}` |

Private input requires a configured encryption key. Requests expire, and stale or mismatched responses return 409. Approval applies to the recorded page and target, not to all future actions in the session.

Cookie import example; replace the placeholder with an actual export:

```json
{"site_url":"https://example.com","cookies":"session=REPLACE_WITH_COOKIE_VALUE"}
```

Import supports helper JSON, cookie-editor JSON, Netscape cookie files, and Cookie headers. The limit is 500 cookies and 1 MiB. Imports begin `unverified`, reject invalid or out-of-scope cookies, and return 409 while the saved login is leased. See [browser sessions](browser.md) for domain restrictions and verification.

After checking the website account, resolve an uncertain action with one of:

```json
{"decision":"confirmed","evidence":"The order appears in my website account history."}
```

```json
{"decision":"not_completed","evidence":"The website account shows no completed order."}
```

Evidence must contain 1 to 2,000 characters. Only uncertain actions from finished runs can be resolved. Resolving an action records the user's finding; it does not itself purchase, cancel, or refund anything.

## Recurring tasks and inbox

| Method and path | Behavior |
|---|---|
| `GET /api/task-templates` | Read templates without activating them |
| `GET /api/tasks` | List this owner's tasks and latest state |
| `POST /api/tasks` | Create a task and associated conversation; returns 201 |
| `PUT /api/tasks/{task_id}` | Replace the task configuration |
| `PATCH /api/tasks/{task_id}` | Pause/resume with `{"enabled":false}` or `{"enabled":true}` |
| `DELETE /api/tasks/{task_id}` | Remove task, history, and notifications; retain chat; returns 204 |
| `POST /api/tasks/{task_id}/run` | Queue Check now, including on a paused task; returns 202 |
| `GET /api/tasks/{task_id}/runs` | Most recent 50 checks |
| `GET /api/task-notifications` | Latest 100 updates and unread count |
| `POST /api/task-notifications/{notification_id}/read` | Mark one update read; returns 204 |
| `POST /api/task-notifications/read-all` | Mark all this owner's updates read; returns 204 |

Create/update body:

```json
{
  "title": "Laptop price watch",
  "prompt": "Check the selected laptop's current price and stock from its retailer.",
  "notification_condition": "Notify me when a new verified offer is under €900 and in stock.",
  "interval_minutes": 60,
  "timezone": "Europe/Helsinki",
  "enabled": true,
  "telegram_enabled": false
}
```

Title is 1 to 120 characters, prompt 1 to 8,000, and condition 1 to 2,000. Interval must be an integer from 1 to 10,080 minutes. Defaults are 60 minutes, `UTC`, enabled, and Telegram disabled. Unknown fields are rejected. `telegram_enabled=true` requires a confirmed link. There can be at most 50 tasks per owner; editing/deleting an active task conversation returns a conflict. See [recurring tasks](recurring-tasks.md) for scheduling semantics.

## Telegram

| Method and path | Behavior |
|---|---|
| `GET /api/telegram` | Link status, pending confirmation, and delivery status |
| `POST /api/telegram/link` | Create a new expiring bot invitation, replacing a pending invitation |
| `POST /api/telegram/confirm` | Confirm with `{"attempt_id":"UUID_FROM_LINK_STATUS"}` |
| `DELETE /api/telegram` | Disconnect/cancel linking, cancel unsent updates, and disable Telegram on tasks; returns 204 |

The confirmation ID must be a UUID. Linking requires the user to open the bot and press Start before confirming the displayed account in Guuey. See [Telegram](telegram.md).

## Voice

| Method and path | Behavior |
|---|---|
| `GET /api/voice` | Read `available` and the configured model |
| `POST /api/voice/token` | Request a constrained, single-use Gemini Live token; returns 201 |

Token bodies can be `{}` or `{"language":"fi"}`; accepted overrides are `en`, `fi`, and `auto`. The response's `session` includes `token`, `model`, `apiVersion`, `languageCode`, `responseModalities`, `connectBefore`, and `expiresAt`. The browser then connects directly to Gemini. There is no audio-upload or transcription-storage endpoint.

## Errors

| Status | Typical meaning |
|---|---|
| 401 | Missing/invalid credentials, expired session, or changed account |
| 403 | Missing `X-Guuey-Request: 1` on a protected cookie request |
| 404 | Resource missing or not owned by this account |
| 409 | Active-run conflict, reused request ID, leased login, expired browser request, or incompatible task state |
| 422 | Invalid request fields, timezone, attachments, or cookie import |
| 429 | Sign-in attempt limit reached |
| 502 | Upstream model/voice request failed |
| 503 | Required server credential or capability unavailable |

FastAPI generally returns a `detail` field. Authentication and browser-request validation use sanitized errors so passwords, pasted cookies, and private input values are not echoed. Background execution failures are also recorded in run status and diagnostics rather than necessarily failing the original 202 request.
