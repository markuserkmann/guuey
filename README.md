# Guuey

A standalone React chat UI backed by FastAPI, Pydantic, and Pydantic AI. Chats are stored in SQLite, searchable by title and message, and private to each user account. Sign in with a username and password or a reusable magic link. Chat uses Gemini when configured and a deterministic local reply otherwise.

## Configuration

Copy `.env.example` to `.env`, create user accounts as described below, set `GEMINI_API_KEY`, and set `EXA_API_KEY` to enable web search. Optional `AGENT_API_TOKEN` access retains the original shared workspace; use a unique token with at least 32 characters. Both provider keys stay on the server. `GOOGLE_API_KEY` is also accepted for Gemini. `GEMINI_MODEL` selects the model and defaults to `gemini-3.5-flash-lite`.

Research stages can use separate models: `GEMINI_PLANNING_MODEL` interprets the request and plans searches, `GEMINI_RESEARCH_MODEL` extracts evidence and writes the research answer, and `GEMINI_VERIFICATION_MODEL` checks claims against source passages. Each falls back to `GEMINI_MODEL` when unset or blank. These overrides leave ordinary chat and the existing routing decision model unchanged. Docker passes them to both the backend and worker; recreate those containers after changing the environment.

The example environment explicitly selects `gemini-3.5-flash` for all three research
stages, while ordinary chat uses `gemini-3.5-flash-lite`. Full-text parsing and additional
research rounds trade higher provider usage for more complete evidence coverage.

User sessions survive reloads in an HttpOnly cookie and expire after 30 days; magic links do not expire and can open a new session each time. Sign out revokes the current session. Optional API-token access stays in tab memory and clears on reload or sign out. Chat history persists in a Docker volume; search filters saved titles and messages. Auto routes each request to chat or research. Fast allows one research round; Auto allows two; Deep allows up to three rounds. Runs and debug traces persist in SQLite.

Debug opens beside the conversation on desktop and below it on mobile, so chat remains usable. The composer accepts text, Markdown, CSV, JSON, and code files through the attachment button or drag-and-drop. Attach up to three UTF-8 files per message, 50 KB each and 100 KB combined. File contents are sent with the message to the model and saved with the chat for follow-up questions. Attached files can be expanded in the conversation. Images, PDFs, and office documents are not supported yet.

## Recurring research tasks

The authenticated task API stores recurring research in the same SQLite database as
chats. The existing worker checks for due tasks every five seconds, queues them under
its normal concurrency limit, and runs fresh web research followed by a structured
evaluation of the notification condition. Both Gemini and Exa must be configured on
the worker. In-app updates use the existing worker; optional Telegram delivery runs in
a separate process.

Create an hourly CVE watch with `POST /api/tasks`:

```json
{
  "title": "Critical CVE watch",
  "prompt": "Check newly published CVEs using CVE.org, NVD, CISA, and vendor security advisories. Include CVE IDs, affected versions, severity and score source, confirmed exploitation, patches or mitigations, and primary-source links.",
  "notification_condition": "Notify me only about a newly published CVE rated Critical by its vendor or with a verified CVSS v3.x/v4.0 score of at least 9.0. Do not repeat previously reported events.",
  "interval_minutes": 60,
  "timezone": "Europe/Tallinn",
  "enabled": true
}
```

`GET /api/task-templates` supplies a reusable CVE example; it does not activate a task.
The critical threshold follows [FIRST's CVSS severity scale](https://www.first.org/cvss/v4.0/specification-document#Qualitative-Severity-Rating-Scale).
Open **Recurring tasks** in the left sidebar, then **New task** or **Use hourly CVE example**
to fill the form. Review its instructions and alert condition, then select **Create task**.
The Tasks panel supports editing, pause/resume, Check now, history, and deletion.
The **Updates** view lists matching results with sources; the sidebar heading shows the unread
count. Open chat reads the full conversation, and
updates can be marked read individually or together. The panel refreshes every 10 seconds.
No recurring tasks are activated just by installing this feature or selecting a template.

Recurring task chats have their own section in the left sidebar, separate from Pinned
and Recents. Select a task to read its chat; select the Recurring tasks heading to manage
schedules and read updates. Queued and running checks show a rotating arrow, including
when another chat is open. After each refresh, the sidebar waits 3 seconds before polling
again if any check is queued or running, or 10 seconds otherwise. It also refreshes when
the window regains focus. Reduced-motion preferences keep the arrow still. An open task
chat picks up newly scheduled runs automatically.

### Telegram notifications

Each Guuey user can link one private Telegram account to a shared bot. In **Recurring
tasks → Telegram**, select **Link Telegram**, open the bot and press **Start**, then return
to Guuey and confirm the displayed account. Edit each desired task and enable **Send
matches to Telegram**. Notifications default to off; linking alone sends no research
results. Existing inbox updates are not backfilled.

For the server administrator:

1. Create a dedicated bot with [@BotFather](https://t.me/BotFather).
2. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` in the server `.env`. Set
   `APP_PUBLIC_URL` to the Guuey URL reachable by your users, for links back to task chats.
   Keep the token out of frontend configuration and source control.
3. Run `docker compose --profile telegram up -d --build` to recreate the API and start
   the optional Telegram service. For local development, run `uv run python -m
   app.telegram_worker` from `backend/`, with the same bot configuration and
   `CHAT_DATABASE_PATH` as the API. Rebuild/restart the Telegram service after code changes.

The worker uses outbound [long polling](https://core.telegram.org/bots/api#getupdates),
so it can run on a remote machine with the shared database and outbound HTTPS access;
no public webhook or inbound Telegram port is needed. Use one dedicated bot, with no
other polling consumer or webhook. The worker verifies its bot identity and refuses an
existing webhook instead of removing it. Multiple Guuey Telegram workers sharing the
database elect one poller through a lease.

The [link invitation](https://core.telegram.org/bots/features#deep-linking) expires in
ten minutes, is stored only as a hash, and can be used once. Only private chats are
accepted. Confirmation requires the same signed-in Guuey owner that created it; a
Telegram account cannot be linked to multiple owners. Normal users need their own
Guuey accounts: the shared development bearer token represents a single owner.

Each new matching inbox entry queues one delivery in the same transaction. The worker
sends a bounded plain-text excerpt, up to three source links and an Open in Guuey link
when `APP_PUBLIC_URL` is set. Failed or incomplete research does not create an alert.
Delivery uses [sendMessage](https://core.telegram.org/bots/api#sendmessage), with up to
five attempts for known transient failures and Telegram's `retry_after` for rate limits.
An ambiguous send timeout or interrupted send is marked unconfirmed and is not retried
blindly. The Telegram panel shows queued updates and the latest failed or unconfirmed
delivery; the full result remains in Guuey.

**Disconnect Telegram** or `/stop` in the bot chat removes the link, cancels unsent
updates and disables Telegram for all that user's tasks. A message already in flight
may still arrive. Disabling Telegram on a task also cancels its unsent updates. Task
pause only stops future checks; an active check can still finish and notify. Reconnecting
requires enabling desired tasks again. Disabled Guuey accounts cannot receive deliveries.
Changing to a different bot requires users to link again; rotating the same bot's token
preserves links. Existing in-app research and notifications continue without Telegram.

| Telegram endpoint | Behavior |
|---|---|
| `GET /api/telegram` | Read this owner's link, pending confirmation and delivery status |
| `POST /api/telegram/link` | Create a private, expiring bot link; replace any pending invitation |
| `POST /api/telegram/confirm` | Confirm the candidate using `{"attempt_id":"..."}` |
| `DELETE /api/telegram` | Disconnect or cancel a pending link; turn off task Telegram notifications |

`POST /api/tasks` and `PUT /api/tasks/{id}` accept `telegram_enabled` (default `false`).
Enabling it requires a confirmed link. These endpoints use the same owner authentication
and cookie request protection as the rest of the task API.

| Endpoint | Behavior |
|---|---|
| `GET /api/tasks` | List the current owner's tasks and latest status |
| `POST /api/tasks` | Create a task and its alert conversation |
| `PUT /api/tasks/{id}` | Replace the title, instructions, condition and schedule |
| `PATCH /api/tasks/{id}` | Pause or resume with `{"enabled": false}` or `true` |
| `POST /api/tasks/{id}/run` | Queue one immediate check, including for a paused task |
| `GET /api/tasks/{id}/runs` | Read the most recent 50 checks and their outcomes |
| `DELETE /api/tasks/{id}` | Delete the task, history and inbox entries; retain its chat |
| `GET /api/task-notifications` | Read the latest 100 updates and total unread count |
| `POST /api/task-notifications/{id}/read` | Mark one update as read |
| `POST /api/task-notifications/read-all` | Mark all of the current owner's updates as read |

Intervals are elapsed minutes, from 1 to 10,080 (one week), with up to 50 tasks per
owner. The first automatic run is one interval after creation. Resume and interval
changes restart that countdown; Check now also resets the next scheduled time.
The timezone supplies research context; these are intervals, not local clock-time
or cron schedules. Pausing prevents future scheduling and lets an already queued
or running check finish. Editing and deletion wait until the conversation is idle.

Schedules and alert deduplication survive restarts. Downtime is coalesced into one
catch-up check, and a task never overlaps an active run in its conversation. A worker
interruption or provider failure is recorded as a failed check, not as a successful
check with no matches. Resume processing requires the API and worker to use the same
database. Checks continue while the browser is closed as long as the worker is running.

Recurring checks bypass the local page and seller-policy caches and request a live crawl
for fetched pages (`maxAgeHours: 0`). Cached provider fallbacks and discovery-only text
are excluded from the evidence used to evaluate alerts. If no live pages can be retrieved,
the check fails rather than reporting a successful check from cached evidence. Existing
retrieval limits still apply; live access and complete coverage are not guaranteed.
Previous results and notified-event keys are retained for duplicate suppression.
Ordinary interactive research keeps its existing cache behavior.

Only supported, matching, previously unreported events create inbox entries and
assistant messages in the task's chat. Each alert's evidence quotes are checked against
retrieved source text, and stable event keys suppress duplicate notifications. Quiet
checks appear only in run history. Research uses a 24-hour overlap before the previous
successful check (or the preceding interval on the first run) to catch indexing delays.
This is model-evaluated web monitoring, not a complete CVE feed: search coverage,
indexing delays, source access and model classification can cause missed events or
incorrect judgments. Review the cited advisories. Delivery is persisted in the app;
email, browser push and operating-system notifications are not implemented.

## Voice input

Select the microphone beside Send to dictate into the message draft. Text updates
as you speak; select Stop or press Escape to finish, then edit or send the draft.
Stop releases the microphone and unlocks the draft immediately. Any final words
arrive in the background; Send and the microphone button wait for transcription
to finish. Editing the draft cancels that wait, and late transcripts cannot
overwrite your edits. The microphone button floats while
listening, with a stationary raised state when reduced motion is preferred.
Existing text is preserved. Sending and editing are paused while recording, and
switching chats or signing out closes the microphone. Sessions stop after five
minutes or when the draft reaches 12,000 characters.

Voice input uses the Gemini Live dictation flow ported from Junction-Valio-Aimo.
`GEMINI_API_KEY` (or `GOOGLE_API_KEY`) stays on the backend. The authenticated
`POST /api/voice/token` endpoint issues a single-use token that can open a session
within one minute and expires after five minutes. Its model and transcription
settings are fixed on the server. The browser streams 16 kHz PCM directly to
Gemini using that token; the app does not save recordings. Audio captured while
connecting stays in memory until the session opens. Start and Stop define one
turn, so pauses do not cut off later words. Final fragments are concatenated and
interim guesses replaced, following the source application's transcript handling.
The transcript becomes an ordinary message only when you send it.

`GEMINI_TRANSCRIPTION_MODEL` defaults to `gemini-3.5-transcribe-live`.
`GEMINI_TRANSCRIPTION_LANGUAGE` defaults to `en-US`, matching the source app's
English default. Set it to `fi-FI` for Finnish, another BCP-47 language code, or
`auto` for automatic detection. The token endpoint also accepts an optional
`language` of `en`, `fi`, or `auto`. `GET /api/voice` reports availability.
See Google's [ephemeral-token documentation](https://ai.google.dev/gemini-api/docs/ephemeral-tokens).

Microphone access requires HTTPS or localhost and browser permission. The browser
must be able to reach Gemini directly. Restart the backend and reload the frontend
after upgrading from the previous WebSocket relay implementation.

## Run with Docker

```sh
docker compose up --build
```

Open http://localhost:18000. The backend API and docs are at http://localhost:18001. The Debug panel reports provider, model, backend health, and whether API key auth is configured.

The example environment sets `COMPOSE_FILE=compose.yaml:compose.dev.yaml`, so this command starts development with an optional shared development connection and live reload.

Set `BACKEND_PORT` and `FRONTEND_PORT` to override the host ports.

## User accounts and seeded chats

Accounts are provisioned from the backend CLI; there is no public registration. The CLI,
API and worker must use the same `CHAT_DATABASE_PATH`. Docker commands below use the
existing shared data volume. Locally, run `uv run python -m app.accounts ...` from
`backend/` with the API's database path in the environment.

Create an account with both a password and a permanent magic link:

```sh
docker compose exec backend /app/.venv/bin/python -m app.accounts create alice --password --base-url http://localhost:18000
```

The command prompts for the password twice and prints the magic link once. Passwords
must be 12–1024 characters. Usernames are case-insensitive and use letters, numbers,
dots, underscores or hyphens. The first character must be a letter or number.
Use the public frontend URL for `--base-url` when sharing links. For password-only
accounts, add `--no-magic-link`; for magic-link-only accounts, omit `--password`.
Automation can use `--password-env VARIABLE_NAME` to read a password from the backend
process environment instead of prompting or putting it in command arguments.

Passwords use Argon2 hashes; only hashes of magic-link and session tokens are stored.
Magic links grant access to the account to anyone holding the link. They are reusable
until rotated, revoked or the account is disabled. The token is in the URL fragment,
which is removed from the address bar before sign-in and is not sent in HTTP URLs.
Set `AUTH_COOKIE_SECURE=true` for HTTPS deployments, including TLS reverse proxies.

Manage accounts:

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

The existing shared workspace remains available through **API token** or, in
development, **Use development connection**. Its chats are not automatically assigned
to a named user. Chats, runs, debug traces and saved browser logins are scoped to each
account's stable ID, so password or magic-link changes do not change their owner.

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

## Docker development with live reload

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

Open http://localhost:18000. React and CSS edits update through Vite hot reload. Backend Python edits restart Uvicorn automatically; worker Python edits restart its supervisor. Source mounts are read-only inside the containers, and the existing chat data volume is retained.

Development offers **Use development connection** when `AGENT_API_TOKEN` is configured.
The Vite proxy adds that token only for explicitly selected development requests; it
never replaces a user-account session or an explicitly supplied API token. The token
is never bundled into browser code or saved in browser storage. This is enabled only
in the development Compose override. User sign-in works in both development and
production. For local Vite outside Docker, set `DEV_AUTH_TOKEN` to the same value as the
backend token in the server environment.

The chat uses assistant-ui thread and message primitives. Sent messages appear immediately with delivery feedback; failed submissions can be retried with the same request ID. Run progress and checkpointed answers update while the worker runs. Pinned chats are saved in the backend and remain searchable.

After routing a research or browser request, a separate Flash-Lite call streams a brief,
task-specific progress response while the main work starts. It describes intended work,
without claiming results before evidence arrives, and appears in the existing live progress
area. Ordinary chat streams its answer directly. `GEMINI_INITIAL_RESPONSE_MODEL` overrides
this model independently of the main model; `INITIAL_RESPONSE_ENABLED=false` disables it.
The optional call uses minimal thinking, at most 128 output tokens, no retries, a two-second
first-event timeout and a four-second request deadline. A failed or late response cannot
replace actual answer/progress output or delay task completion. These limits do not
guarantee a particular response time from the provider.

An edit during a research run can interrupt it; the saved run and any checkpointed result remain available. Let a run finish before editing when you want to preserve its execution.

Rebuild with the same command when dependencies or Dockerfiles change. Source edits do not require image rebuilds. To return to the normal containers:

```sh
docker compose -f compose.yaml up -d --build
```

## Local development

Start the API:

```sh
cd backend
uv sync --frozen
uv run uvicorn app.main:app --reload
```

Run `uv run python -m app.worker` from `backend/` in a second terminal, using the same environment and database path as the API.

Start the UI in another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend on port 8000.

## Browser integration (in progress)

Set `BROWSER_ENABLED=true` to make the Gemini browser agent available. The composer's
**Force browser** checkbox is a debugging override and starts unchecked.
The run API accepts `force_browser` (default `false`); retries and Deep continuations
retain that run's choice. Unchecked leaves automatic routing and browser handoffs
enabled. Checked bypasses model routing and starts the browser directly. An explicit
browser choice in the message, including a trailing `browser`, also bypasses routing.
Explicit requests not to use a browser still take precedence over the debug override.
Live staff availability and booking-calendar checks are browser tasks.
Requests to order, buy, book, submit forms, send messages, or change website accounts
are browser tasks even when finding or comparing options comes first. Clear action
requests bypass model classification and go directly to the browser. If browser
support is disabled, these requests report that it is unavailable rather than running
as research. Information requests and unsent drafts still use normal routing.

When workflow confidence is below 0.8, a local rules check resolves the choice without
another model or network call. Clear form, login, basket and booking interactions or
live appointment checks select the browser; other uncertain requests start with agentic
search. Drafts, explanations and negated interactions also stay on search. This check
uses the current message, not instructions in attachments or earlier turns. Low
confidence about research depth alone does not trigger workflow reclassification.
The debug override and explicit browser opt-outs still take precedence.

If research leaves requested live availability or stock unverified, the same run can
continue in the browser with its discovered site links. A failed search provider can
also hand off to the browser with the checkbox unchecked. Browser opt-outs, disabled browser support and draft-only
results prevent the availability handoff. A staff profile alone does not complete an
appointment-availability check. Across sites, the agent advances visible forms using
explicit requirements, known preferences, then plausible reversible defaults. It records
provisional service, duration or other choices and discloses them with the result;
availability applies only to the checked configuration. Follow-ups retain these choices
until the user changes them. After two unproductive routes, or when a choice requires
private facts or a commitment, it asks for the missing information. Checking slots does
not authorize making a booking.

Unverified provider links and incomplete suitability checks trigger targeted follow-up
research within the selected mode's round and time limits. If those searches stall or
run out of useful queries, the worker checks whether to continue in the browser before
returning a partial answer. Interactive pages, JavaScript-only content and requested
stock hidden at checkout can hand off immediately. Browser work carries the unresolved
requirements and supported facts forward; it requires enabled server support and no
explicit browser opt-out. If browser verification is disabled or unavailable, the partial
answer states that limitation. Debug records the recovery decision and its reason.

The default backend is Oxylabs: one remote browser connection stays open for the run,
including while waiting for private input or approval. Playwright connects over CDP
with a URL-safe Basic authorization header; browser credentials stay out of the URL.
Connections use `page_handler=false` and optional country targeting. Missing credentials
or a remote connection failure do not start a local browser.

After a successful browser reply, the chat worker keeps that page and connection
open for 60 seconds (`BROWSER_IDLE_TIMEOUT_S`, configurable from 1 to 300 seconds).
The reply is saved immediately, so the user can send another message during this
window. A browser follow-up in the same chat reuses the page, including its basket
and form state, with fresh task limits and approval requests. Each follow-up still
goes through the normal router; a non-browser task ends the warm session after it
finishes. Idle browsers do not occupy active task slots. Expiry, cancellation,
connection loss, or a changed/deleted saved login closes the connection. Idle page
network requests are blocked until a new browser task starts.

When information or manual interaction is needed during a task, the agent uses
the existing input or takeover card and keeps the live view available while
waiting. This uses `BROWSER_REQUEST_TIMEOUT_S`, not the 60-second idle window.
The live-view link is exposed only during an active task. A normal `needs_user`
reply also retains the page briefly; errors and uncertain submissions close it.

Images, media and fonts are blocked before download by default. The browser agent
can use `open(url, load_images=true)` when images are needed for visual review or
manual interaction. That choice applies before navigation and persists through
clicks and form submissions until the next `open` call. Text-only work and fresh
login-verification sessions keep images blocked. Stylesheets and JavaScript remain
enabled so visibility checks, dynamic forms and the VNC layout continue to work.
The VNC preview reflects this setting: text-only tasks may show missing images.
Enabling images requires a subsequent navigation; the app does not automatically
reload forms, resubmit actions or reuse one-time sign-in links to restore images.

The agent can navigate, fill forms, restore and save encrypted logins, and request
approval before final actions. It checks that the page and target still match the
approval, records each submission, and requires new page text to confirm the result.
An uncertain submission blocks further submissions on that site until the owner
checks the account and resolves it through the API. Passwords, one-time codes and
sign-in links use encrypted private input requests and are removed from stored responses
when consumed. Sign-in links are restricted to the login site and opened at most once.
Card fields require manual takeover; saved payment methods can be used.

Run responses and SSE events include browser steps, pending requests, action status,
and the owner-only live-view link. Debug includes browser model calls and timing data.
Cancellation and expiry clear stored private responses, and browser runs cannot
continue as Deep research. The final-action label and URL checks are heuristics;
approval remains necessary for final submissions.

Browser activity appears in the conversation with expandable steps and a read-only
VNC iframe preview for active Oxylabs sessions. The preview scales to fit the chat;
Open live view opens the interactive viewer in a new tab, also available if the
embed cannot connect. Local Chromium sessions do not provide a VNC preview.
Approval cards show the proposed action, website button, screenshot and expiry.
Private-input forms mask passwords, one-time codes and sign-in links, and clear fields
when sent or expired. Takeover cards let you open the live view and confirm when the
manual step is finished. Browser runs do not offer automatic retries or Deep research.

Open **Saved logins** in the header to import or delete a login. Deletion asks for
confirmation because it also stops tasks using that login. The same panel lists
uncertain submissions: check the website account, select the outcome and record what
you found before starting another task on that site. The controls use the authenticated
API; a request's `kind` determines its response body:

- `POST /api/runs/{id}/browser/requests/{request_id}` accepts `{"decision":"approve"}`
  or `{"decision":"decline"}`, `{"values":{"field_id":"value"}}`, or `{"done":true}`.
- `GET /api/runs/{id}/browser/requests/{request_id}/screenshot` returns the approval image.
- `GET /api/browser/actions` lists uncertain submissions for the current owner.
- `POST /api/browser/actions/{id}/resolve` accepts
  `{"decision":"confirmed","evidence":"Order 123 appears in my account"}` or
  `{"decision":"not_completed","evidence":"No order in my account history"}`.
  Only uncertain actions from finished runs can be resolved.

Oxylabs live-view interaction and live-site flows still need manual verification.
The frontend production build and offline tests cover the API contracts, rendered
controls and extension behavior. Interactive layout checks still need an environment
that permits Chromium and a local preview server.

The saved-logins panel includes **Download cookie helper**. Unzip the download, open
`chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select
the extracted folder. On the website tab, open the helper and export cookies, then
paste them into Guuey. The helper requests access only to that HTTPS site, removes
the permission after the operation, and makes no network requests. Cookie values
remain on the clipboard until you replace them. The helper does not export local
storage; some sites still require signing in through the browser task.

`npm run dev` and `npm run build` generate `frontend/public/cookie-helper.zip` from
`frontend/cookie-helper/`. Validate the frontend with
`cd frontend && npm test && npm run build && npm run format`.

Set `BROWSER_SESSION_KEY` to a persistent 32-byte key encoded as 64 hex characters:

```sh
python -c "import secrets; print(secrets.token_hex(32))"
```

The API and worker must use the same key. Without a valid key, importing saved logins
and collecting passwords, one-time codes or sign-in links return unavailable. Changing the key makes
existing encrypted sessions unreadable; delete them and import them again. Sessions
belong to the signed-in user. People using the same legacy API token share its saved logins.

Authenticated session endpoints:

- `GET /api/browser/sessions` lists site names, status, revisions and cookie counts.
- `POST /api/browser/sessions/import` accepts `{"site_url":"https://example.com","cookies":"session=..."}`.
- `DELETE /api/browser/sessions/{id}` removes a saved login and revokes its active lease.

Saved logins have exclusive leases tied to the owner, site and run. A run may use
one authenticated HTTPS site, including its subdomains. The worker renews the lease
while waiting for the model or user; deletion, cancellation and expiry stop further
browser actions and close the connection. Imports return 409 while a login is leased.
Revision checks reject stale saves, including saves after deletion and replacement.
Cancelled and interrupted runs release leases and preserve uncertain submissions.

Saving a login checks account evidence in separate fresh anonymous and authenticated
browser connections. The evidence must be absent anonymously and present with the
stored state, without a visible password or OTP form. Verification uses an account
URL without query parameters or fragments. Inconclusive checks leave the state
`unverified`. Cookies and local storage are restricted to the site; expired cookies
are discarded, and refreshed state is saved under the run's lease. Cross-site SSO
redirects are not supported within an authenticated run; import the destination
site's login cookies instead.

Import accepts helper JSON, cookie-editor JSON, Netscape cookie files, or a Cookie
header. Imports start as `unverified`; values are never returned by the API. Imports
are limited to 500 cookies and 1 MiB, and reject expired, partitioned, unrelated-domain
and public-suffix cookies. An import with no valid cookies leaves the saved login intact.

Browser settings are listed in `.env.example` and passed to both services by Compose.
`BROWSER_ENABLED` defaults to false, `BROWSER_BACKEND` to `oxylabs`, and
`BROWSER_MODEL` to `gemini-3.5-flash-lite`. Oxylabs configuration uses `OXY_AB_USERNAME`,
`OXY_AB_PASSWORD`, and optional `BROWSER_COUNTRY`. Website discovery uses the separate
`OXYLAB_KEY` (also accepted as `OXYLABS_WEB_API_KEY`) with the Oxylabs MCP `search` tool.
Other remote MCP tools are unavailable. Without the discovery key, the agent can open
supplied website URLs and asks for a URL when needed. Browser discovery does not fall
back to the research search provider when Oxylabs is selected.

`BROWSER_DRIVER=patchright` selects the patched Playwright-compatible driver for either
backend. `BROWSER_BACKEND=oxylabs` still connects to the same remote provider and uses its
default context and live view; `BROWSER_BACKEND=local` launches a local browser. The default
driver remains `playwright`; switching drivers requires a worker restart. Run diagnostics
include the selected driver. Patchright changes automation behavior but does not guarantee
CAPTCHA-free access or change the remote browser's launch flags, exit IP or provider settings.
Oxylabs challenge handling is currently disabled (`page_handler=false`) independently of
the driver. Use the live view to inspect a blocked page; provider recordings can also be
enabled separately for diagnosis. Patchright does not deliver browser console events.

Local development settings include
`BROWSER_HEADLESS`; deadline, tool-call and user-response limits use
`BROWSER_DEADLINE_S`, `BROWSER_MAX_STEPS`, and `BROWSER_REQUEST_TIMEOUT_S`.
Browser runs default to 30 minutes, 120 tool calls and 15 minutes for user responses.
`BROWSER_THINKING_LEVEL=minimal` keeps routine browser decisions fast. Page controls
come directly from Playwright's accessibility snapshot without a separate model call.
`BROWSER_MODEL_MAX_TOKENS=8192` and `BROWSER_MODEL_TIMEOUT_S=180` retain room for longer responses.
`BROWSER_TOOL_RETRIES=5` allows corrections to invalid tool arguments; it never
authorizes repeating a final submission. Validation diagnostics record the tool,
field paths and error types without input values. Model-request counts remain bounded,
and the run's hard deadline remains 59 minutes including user waits.
Oxylabs connections use six timed attempts with exponential backoff and jitter for
transient connection errors. Authentication, invalid parameters, profile conflicts
and restricted targets stop without retrying the same connection request. Browser
cleanup still runs on every exit; an established task is never replayed automatically.

Recognized cookie dialogs are closed automatically, or accepted when no usable close
or necessary-only control remains, including English, Finnish and Swedish controls. Detection is scoped
to cookie containers and dialogs; unrelated acceptance buttons are left alone. Each
consent control is attempted once per frame URL. Pending final actions disable this
handling until their result is checked. Repeated actions on unchanged pages and
repeated navigation cycles are blocked after two attempts; continued repetition ends
the run with a request for help instead of consuming the entire browser budget.
Page observations prioritize foreground dialogs and address suggestions over long
catalogs, including product popups rendered in portal regions without a dialog role.
Popup inspection preserves page references. Control checks read DOM attributes without
replacing the accessibility reference map, including while the page is updating.
Repeated picker open/select cycles count
as repetition even when each click succeeds. Restored-login status is included in the
agent context; current account controls must still establish whether it is signed in.
Browser tools accept both plain (`e45`) and frame-prefixed (`f21e45`) element references,
including popup detection and repeat tracking. Authenticated interactions in frames
remain restricted to the saved login's site.
Text inspection supports `ref` to read a whole table or date group and `refresh=false`
to search or paginate the last observation without another browser round trip. Inspection
refreshes by default; the agent must verify live availability with a fresh observation.
Text waits check the main page and embedded frames, including frames attached while waiting,
under one shared timeout. Navigation observations allow up to two seconds for empty or
spinner-only embedded forms to show readable content before handing the page to the model.
Product cards omit duplicated descendant text only when their accessible names already
contain those details; prices, sellers and availability stay together. Query excerpts include
the containing result and parent references. Pagination preserves its query and subtree.
Recent inspection evidence survives history trimming with obsolete references removed.
The first inspection after an action refreshes pending results before cached pagination resumes.
Repeated identical reads receive guidance, then stop instead of consuming the whole step budget.
Native selects and custom listbox pickers support exact-label selection. Quantity changes count
as progress. Search fields without forms support Enter while ordinary inputs and checkout
submissions retain their guards. Ordinary checkout buttons outside forms are not submissions.
Once browsing a site, direct navigation uses user-provided or observed URLs; invented paths
and sorting parameters are rejected. Links opening a new tab become the active page, and Back
can return to the original results with their filters intact. Warm follow-ups retain that tab.
Foreground controls remain visible during text searches, and covered background
targets are refused before clicking. Product previews lead through their merchant's
ordering controls; the agent checks basket contents before claiming an item was added.
An address field may be an editable combobox: the agent selects a matching
suggestion before continuing. Overlay errors retain their actual obstruction details
in debug history. Repeated overlay failures or exhausted progress offer one manual
takeover when live view is available, then stop if recovery remains unsuccessful.
The evaluation Compose configuration keeps browser automation disabled.

`BROWSER_BACKEND=local` is available for development and fixture tests. Install its
Chromium version with `cd backend && uv run playwright install chromium`, or
`cd backend && uv run patchright install chromium` for Patchright. The remote backend
does not require browser binaries in the Docker image. Local flow tests cover both
drivers and skip a driver when its Chromium is missing or the environment denies startup.
For offline backend validation without launching browsers, run
`cd backend && uv run --frozen pytest -q --ignore=tests/test_browser_flows.py -k 'not real_chromium'`.

The opt-in live browser checks cover Wolt delivery search and basket, IKEA product selection
and basket, and Booking.com dates, guests, filters and room availability. They use an isolated
database and anonymous browser session; input, takeover and final-action requests stop the test.
They consume the configured model/browser providers. From the repository root with Compose running:

```sh
docker compose exec -T -e PYTHONPATH=/app backend /app/.venv/bin/python - wolt < backend/evaluations/browser_flows.py
```

Replace `wolt` with `ikea` or `booking`. With provider environment variables configured locally,
use `cd backend && uv run --frozen python -m evaluations.browser_flows wolt`.
The runner prints its observation directory, timings and outcome. Check the saved basket or
availability observations as well as the model report; `done` alone is not proof of correctness.
See [the recorded live results](backend/evaluations/browser-flows-2026-11-15.md).

## Live research evaluation

The opt-in suite exercises 15 real Exa/Gemini research requests: Finnish laptop offers,
Helsinki sauna prices and bookings, and holiday/transport comparisons. It reads provider
keys from the environment or the root `.env`, incurs normal provider usage, and writes
answers, retrieved evidence, stage timings and diagnostics without creating chat records.

```sh
cd backend
uv run python live_research_eval.py --output evaluations/my-run --concurrency 3
uv run python live_research_eval.py --output evaluations/my-followup --followup-from evaluations/my-run --concurrency 3
```

Use `--cases sauna_loyly laptop_linux` to select individual cases. Each invocation runs
one research round; the second command exercises a seeded follow-up. Coverage counts
are diagnostics, not a factual accuracy score: review the saved answers against their
evidence, including price units, exact configurations, destination and date restrictions.

## Task interpretation and bounded refinement

The worker pins a research contract for each task: the original request, objective,
hard requirements and preferences, location, dates/timezone, exact entities, exclusions,
missing information, and requested output format. Numeric budgets distinguish strict
`under` from inclusive `at most`; approximate budgets remain preferences. A destination
and its associated hotel use separate checks.

Research profiles are shared by the worker, supervisor, retrieval and prompt builder in
`backend/app/research_modes.py`:

| Mode | Rounds | Deadline | Prompt budget | Livecrawl pages per round | Planner |
|---|---|---|---|---|---|
| Fast | 1 | 45 s | 150K characters | Up to 4 | Flash Lite |
| Auto | 2 | 120 s | 320K characters | Up to 8 | Flash |
| Deep | 3 | 240 s | 600K characters | Up to 16 | Flash |

`GEMINI_FAST_PLANNING_MODEL` configures the Fast planner; `GEMINI_PLANNING_MODEL`
configures Auto and Deep. Planning uses minimal thinking. Each round plans at most
four searches. Refinement plans return queries without regenerating the fixed contract.
Rounds stop after one round without evidence progress unless a replacement search is still untried, or before starting another round
that is unlikely to fit the deadline. Discovering more URLs alone is not progress.
Provider requests also respect the remaining run deadline. The first checked answer is
saved at `answer_ready`; later rounds update that saved answer.

Search and extraction retain the complete text returned by Exa. Generation receives
relevant indexed passages within per-source and global budgets; headings and table
headers render once per section/table. Candidate focus pages remain complete and are
exempt from the character budget. Source text, passage IDs, verification context and
persisted evidence remain complete. Short, link-only navigation and off-target pages
receive less prompt space. Full-text search results are reused as cached pages; only
empty results need extraction for tasks without freshness requirements. Live crawls
prioritize relevant offers, with at most two contents calls per round and a 5-second
livecrawl timeout. Ordinary interactive research can retain cached evidence after a
failed live crawl; recurring checks require live evidence as described above.

Generation records missing checks as unknown. It never resends the complete prompt
for evidence-validation failures. A small set of rejected shopping findings can receive
a separate bounded repair using only their cited sources. Later rounds fill missing
checks per candidate, in parallel with three workers, and preserve checked facts. New
candidate discovery runs only for a count shortfall or a confirmed hard failure; empty
or failed gap filling falls back to full generation. Known price/stock URLs, seller
policies and service booking/price subpages can be retrieved before planning or search.

Shopping refinement follows product links actually published on discovery pages and
retrieves seller policy subpages for missing purchase-country evidence. Budget preferences
are researched for each candidate; a known preference failure remains a labelled tradeoff.

Sources retain URL-derived IDs and separate request, observation, publication and reported
crawl timestamps. A live crawl remains usable for the run when its crawl time is close to observation;
old crawl dates and timestamps without timezones cannot establish freshness.
Receiving a cached page does not make its facts current. Supported cached facts render
as **Reported** with their observation/crawl date and confirmation advice. Conflicts
retain both evidence items; missing facts show a concrete next step. Reported facts
pass semantic verification and never increase verified counts. Reported positive facts remain
unknown for requirement satisfaction; a reported contradiction can exclude a candidate.
Fast and Auto may stop with reported volatile stock, availability or delivery checks
when all other hard requirements are confirmed; Deep keeps refining. Tables and
prepared drafts are rendered from checked fields; preparation does not send or book anything.

Sourced answers use short extracted values and passage IDs. The application adds requirement
and display metadata, checks number/name alignment, excludes SEO and off-target pages, and
labels secondary facts. Knowledge answers verify each cited point separately, retaining
supported points when another point fails. Missing preferences are omitted; hard gaps appear
once per candidate under **To confirm**. Shopping answers label condition and limit repeated
sellers, with additional searches aimed at other retailers. Domains whose retrieved pages
expose no stock text do not consume repeated stock searches.

Public page caches persist next to the chat database (override with `SOURCE_CACHE_PATH`).
Policies, legal and registry pages expire after seven days; offers after thirty minutes;
semantic verifier verdicts after thirty days. Cache hits preserve original observation times.
Cached prices, stock and opening hours remain reported rather than confirmed.
The [answer quality report](backend/evaluations/gold/quality-2026-11-14.md) records the audited
baseline, current offline results and the live acceptance checks still outstanding.

The app-level evaluator exercises authenticated input, the real worker, chat persistence,
and refreshed citations. It creates evaluation chats and uses the app's configured provider
limits. It does not reset an Exa request ledger:

```sh
cd backend
uv run python evaluate_pipeline.py --output evaluations/pipeline/live --cases estonia_hotels loyly_price finland_laptops
uv run python evaluate_pipeline.py --output evaluations/pipeline/replay --replay-from evaluations/pipeline/before --cases estonia_hotels
```

Replay uses captured evidence and real Gemini in an isolated database, with Exa network
calls blocked. It preserves capture timestamps and cannot validate fresh prices, stock,
new retrieval, or a live end-to-end speedup. Diagnostics and the review are under
`backend/evaluations/pipeline/`; configured keys are redacted from evaluation artifacts.
Each evaluator case includes per-round planning, retrieval, answering and verification
timings, generation tokens/requests, contents calls, duplicate crawls and coverage.
Run summaries include first-answer time, wall time, stop reason and final phase.
The optimization comparison and its measured limitations are recorded in
`backend/evaluations/pipeline/optimization-review.json`.

The existing raw HTTP Exa integration follows the official
[search](https://exa.ai/docs/reference/search),
[contents](https://exa.ai/docs/reference/get-contents), and
[quickstart](https://exa.ai/docs/search/quickstart) documentation.
