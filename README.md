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
