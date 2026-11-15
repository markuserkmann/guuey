# Exa research and Markdown evidence

[Documentation index](README.md)

## Exa retrieval and Markdown parsing

Guuey calls Exa's `search` and `contents` endpoints from
[`research.py`](../backend/app/research.py). Requests ask for full text with full
link URLs. Search planning can supply domain filters, publication-date bounds,
location, and supported search categories. Page extraction can request relevant
subpages and a live crawl when freshness matters. Exa credentials stay on the server.

Exa supplies the retrieved page text. Guuey parses that text with `markdown-it-py`
in [`passages.py`](../backend/app/orchestration/passages.py), using CommonMark and
table support. The parser retains heading ancestry, list content, table headers,
row context, and raw text. It normalizes visible text for matching and assigns
source-specific passage IDs, so a quoted value can be checked against its source
without losing units, labels, or surrounding qualifications.

The answer pipeline checks exact quoted values and passage references, then uses
semantic verification to assess what those passages support. Citation links alone
do not establish that a claim is correct or current. Frontend Markdown rendering
uses `react-markdown` and `remark-gfm`; it is separate from this evidence parser.

## Task contracts and research modes

The worker pins a research contract for each task: the original request, objective,
hard requirements and preferences, location, dates/timezone, exact entities, exclusions,
missing information, and requested output format. Numeric budgets distinguish strict
`under` from inclusive `at most`; approximate budgets remain preferences. A destination
and its associated hotel use separate checks.

Research profiles are shared by the worker, supervisor, retrieval and prompt builder in
`backend/app/research_modes.py`:

| Mode | Rounds | Deadline | Prompt budget | Live-crawl pages per round | Example planner |
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

## Evidence selection and refinement

Search and extraction retain the complete text returned by Exa. Generation receives
relevant indexed passages within per-source and global budgets; headings and table
headers render once per section/table. Candidate focus pages remain complete and are
exempt from the character budget. Source text, passage IDs, verification context and
persisted evidence remain complete. Short, link-only navigation and off-target pages
receive less prompt space. Full-text search results are reused as cached pages; only
empty results need extraction for tasks without freshness requirements. Live crawls
prioritize relevant offers, with at most two contents calls per round and a 5-second
livecrawl timeout. Ordinary interactive research can retain cached evidence after a
failed live crawl; recurring checks require [live evidence](recurring-tasks.md).

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

## Citations and freshness

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

## Persistent cache

Public page caches use a separate SQLite file beside `CHAT_DATABASE_PATH` (override with `SOURCE_CACHE_PATH`). This remains a local file when the application database uses PostgreSQL.
Policies, legal and registry pages expire after seven days; offers after thirty minutes;
semantic verifier verdicts after thirty days. Cache hits preserve original observation times.
Cached prices, stock and opening hours remain reported rather than confirmed.
The [answer quality report](../backend/evaluations/gold/quality-2026-11-14.md) records the audited
baseline, offline results and live acceptance checks outstanding at the time of that audit.



## Continuing in the browser

Research can pass discovered URLs, supported facts, provisional choices, and
unresolved requirements into the browser workflow. Common triggers include live
appointment calendars, interactive inventory, JavaScript-only content, and stock
that appears only after selecting a configuration or reaching checkout. The
worker also evaluates browser recovery after a search-provider failure or stalled
refinement. It preserves the search answer and asks before opening a browser, explaining
that browser checks take longer. An accepted offer resumes the original request, resolved
dates and supporting sources. Store and pickup lookups use their available search rounds
before offering browser stock checks. Browser support must be enabled, and an explicit
opt-out takes precedence.

A staff biography does not verify a bookable appointment. A product description
does not prove current stock for a specific variant or delivery destination. If
the browser cannot verify the requested fact, the result retains the limitation.
See [live browser workflows](browser.md) for controls, approval, and follow-up behavior.

## Relevant source modules

| Module | Responsibility |
|---|---|
| [`interpretation.py`](../backend/app/orchestration/interpretation.py) | Fixed task requirements and preferences |
| [`planner.py`](../backend/app/orchestration/planner.py) | Initial and refinement search plans |
| [`orchestrator.py`](../backend/app/orchestration/orchestrator.py) | Retrieval and answer pipeline |
| [`grounding.py`](../backend/app/orchestration/grounding.py) | Evidence checks and rendering |
| [`verification.py`](../backend/app/orchestration/verification.py) | Semantic verification of supported claims |
| [`refinement.py`](../backend/app/orchestration/refinement.py) | Follow-up queries and candidate refinement |
| [`browser_handoff.py`](../backend/app/orchestration/browser_handoff.py) | Browser continuation decisions |

See [development and testing](development.md) for offline checks and live evaluations.
