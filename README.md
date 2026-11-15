# Guuey

A standalone React chat UI backed by FastAPI, Pydantic, and Pydantic AI. Chats are stored in SQLite, searchable by title and message, and scoped to the configured bearer token. Chat uses Gemini when configured and a deterministic local reply otherwise.

## Configuration

Copy `.env.example` to `.env`, set a unique `AGENT_API_TOKEN` with at least 32 characters, set `GEMINI_API_KEY`, and set `EXA_API_KEY` to enable web search. Both provider keys stay on the server. `GOOGLE_API_KEY` is also accepted for Gemini. `GEMINI_MODEL` selects the model and defaults to `gemini-3.5-flash-lite`.

Research stages can use separate models: `GEMINI_PLANNING_MODEL` interprets the request and plans searches, `GEMINI_RESEARCH_MODEL` extracts evidence and writes the research answer, and `GEMINI_VERIFICATION_MODEL` checks claims against source passages. Each falls back to `GEMINI_MODEL` when unset or blank. These overrides leave ordinary chat and the existing routing decision model unchanged. Docker passes them to both the backend and worker; recreate those containers after changing the environment.

The example environment explicitly selects `gemini-3.5-flash` for all three research
stages, while ordinary chat uses `gemini-3.5-flash-lite`. Full-text parsing and additional
research rounds trade higher provider usage for more complete evidence coverage.

Local development connects automatically through the frontend proxy. Production asks for the backend token and validates it before opening chats; the token stays in tab memory and clears on reload or disconnect. Chat history persists in a Docker volume; search filters saved titles and messages. Auto routes each request to chat or research. Fast allows one research round; Auto allows two; Deep allows up to three rounds. Runs and debug traces persist in SQLite.

Debug opens beside the conversation on desktop and below it on mobile, so chat remains usable. The composer accepts text, Markdown, CSV, JSON, and code files through the attachment button or drag-and-drop. Attach up to three UTF-8 files per message, 50 KB each and 100 KB combined. File contents are sent with the message to the model and saved with the chat for follow-up questions. Attached files can be expanded in the conversation. Images, PDFs, and office documents are not supported yet.

## Run with Docker

```sh
docker compose up --build
```

Open http://localhost:18000. The backend API and docs are at http://localhost:18001. The Debug panel reports provider, model, backend health, and whether API key auth is configured.

The example environment sets `COMPOSE_FILE=compose.yaml:compose.dev.yaml`, so this command starts development with automatic authentication and live reload.

Set `BACKEND_PORT` and `FRONTEND_PORT` to override the host ports.

## Docker development with live reload

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

Open http://localhost:18000. React and CSS edits update through Vite hot reload. Backend Python edits restart Uvicorn automatically; worker Python edits restart its supervisor. Source mounts are read-only inside the containers, and the existing chat data volume is retained.

Development connects automatically using the configured `AGENT_API_TOKEN`. The Vite proxy adds the token on the server; it is never bundled into browser code or saved in browser storage. This is enabled only in the development Compose override. The normal production containers still require the backend token. For local Vite outside Docker, set `DEV_AUTH_TOKEN` to the same value as the backend token in the server environment.

The chat uses assistant-ui thread and message primitives. Sent messages appear immediately with delivery feedback; failed submissions can be retried with the same request ID. Run progress and checkpointed answers update while the worker runs. Pinned chats are saved in the backend and remain searchable.

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

Set `BROWSER_ENABLED=true` to route website tasks to the Gemini browser agent. The
current message's explicit browser choice, including a trailing `browser`, bypasses
model routing. Live staff availability and booking-calendar checks are browser tasks.
If research leaves requested live availability or stock unverified, the same run can
continue in the browser with its discovered site links. A failed search provider can
also hand off to the browser. Browser opt-outs, disabled browser support and draft-only
results prevent the availability handoff. A staff profile alone does not complete an
appointment-availability check. Across sites, the agent advances visible forms using
explicit requirements, known preferences, then plausible reversible defaults. It records
provisional service, duration or other choices and discloses them with the result;
availability applies only to the checked configuration. Follow-ups retain these choices
until the user changes them. After two unproductive routes, or when a choice requires
private facts or a commitment, it asks for the missing information. Checking slots does
not authorize making a booking.

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
belong to the API token's owner, so people sharing one token also share saved logins.

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
Chromium version with `cd backend && uv run playwright install chromium`. The remote
backend does not require browser binaries in the Docker image. Local flow tests skip
when Chromium is missing or the execution environment denies browser startup.
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
livecrawl timeout. A failed live crawl retains cached evidence.

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
