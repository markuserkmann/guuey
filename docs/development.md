# Development and testing

[Documentation index](README.md)

## Development environments

Follow [getting started](getting-started.md) for host processes or run the development containers explicitly:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

Vite updates React/CSS edits, Uvicorn reloads API code, and the worker uses a file-watching supervisor. Source mounts are read-only inside the containers. Dependency and Dockerfile changes require a rebuild. Worker reloads interrupt active runs; checkpointed results persist, but live browser connections do not.

To use the built frontend and base containers:

```sh
docker compose -f compose.yaml up -d --build
```

Development offers **Use development connection** only when the shared token is configured. Vite injects the token only for explicitly selected development requests and does not replace a named-user session or explicitly supplied bearer token. The token is not bundled or saved in browser storage. See [configuration](configuration.md).

## Backend checks

From `backend/`:

```sh
uv sync --frozen
uv run --frozen pytest -q --ignore=tests/test_browser_flows.py -k 'not real_chromium'
```

This exercises the offline suite without browser launch tests. Tests cover authentication, conversations, runs, attachments, routing, research budgets, citations, grounding, freshness, recovery, browser guards, sessions, and voice contracts. Test fixtures isolate the source cache. Keep tests isolated from a live database and do not load production environment files for offline checks.

For local browser fixture coverage, install the selected Chromium runtime and run the browser tests:

```sh
uv run playwright install chromium
uv run --frozen pytest -q tests/test_browser_flows.py
```

Install `uv run patchright install chromium` for Patchright coverage. Driver-specific fixtures can skip when that browser is missing or startup is denied. These local fixtures do not prove that current remote websites or the provider's live viewer work.

## Frontend checks

From `frontend/`:

```sh
npm ci
npm test
npm run build
npm run format
```

`npm test` runs Node's test runner against the client contracts and component behavior. `npm run build` checks TypeScript and builds with Vite. `npm run format` checks formatting without rewriting files.

Both `npm run dev` and `npm run build` generate `public/cookie-helper.zip` from [`cookie-helper/`](../frontend/cookie-helper/). To package it directly:

```sh
npm run package:cookie-helper
```

For changes to live browser interaction or voice capture, also exercise the relevant user controls in a browser. Test the pending/expired request states, cancellation, reconnects, reduced motion, and narrow screens when those paths change. Record what was actually exercised.

## Live research evaluation

[`live_research_eval.py`](../backend/live_research_eval.py) runs 15 Exa/Gemini cases covering laptop offers, sauna prices/bookings, and holiday/transport comparisons. It reads provider credentials from the environment or root `.env`, incurs provider usage, and writes answers, evidence, timings, and diagnostics without creating chat records.

From `backend/`:

```sh
uv run python live_research_eval.py --output evaluations/my-run --concurrency 3
uv run python live_research_eval.py --output evaluations/my-followup --followup-from evaluations/my-run --concurrency 3
```

Use `--cases sauna_loyly laptop_linux` to select cases. Each invocation runs one research round; the follow-up command seeds context from the previous output. Coverage counts are diagnostics, not independent factual accuracy scores. Review exact configurations, price units, date restrictions, delivery countries, and supporting passages.

## Application-level evaluation

[`evaluate_pipeline.py`](../backend/evaluate_pipeline.py) exercises authenticated input, the real worker, persistence, and refreshed citations. Live mode creates evaluation chats and uses the running app's provider settings. The default API URL is `http://127.0.0.1:18001`; override it with `--base-url`.

```sh
uv run python evaluate_pipeline.py --output evaluations/pipeline/live --cases estonia_hotels loyly_price finland_laptops
```

Useful options include `--mode auto|fast|deep`, `--timezone`, `--timeout`, `--concurrency 1|2`, `--repetitions 1|2`, `--followup`, and `--cache-checks`. Read `--help` for the current case list. The evaluator does not reset Exa accounting.

Replay uses previously captured source evidence, an isolated application database, and live Gemini, with Exa calls blocked:

```sh
uv run python evaluate_pipeline.py --output evaluations/pipeline/replay --replay-from evaluations/pipeline/live --cases estonia_hotels
```

Replay preserves capture timestamps. It cannot establish fresh prices, stock, retrieval coverage, or a current end-to-end speed improvement. Artifacts include per-round planning, retrieval, generation and verification timing, provider usage, duplicate crawls, coverage, first-answer time, wall time, and stop reason. Configured credentials are redacted, but task/source content can still be private.

## Quality scoring and historical evidence

The versioned [gold evaluation guide](../backend/evaluations/gold/README.md) explains the partial factual gold set, historical capture hashes, independent audits, and acceptance criteria. The [2026-11-14 quality report](../backend/evaluations/gold/quality-2026-11-14.md) records results from that audit, including outstanding live checks.

Given locally generated captures:

```sh
uv run python evaluations/score_quality.py evaluations/pipeline/live \
  --audits evaluations/gold/captured-audits.json \
  --output evaluations/quality/scores.json
```

`--reground` applies current local grounding/rendering to captured answers without new retrieval or generation. `--live-judge` makes additional Gemini calls for semantic audits. Changed claims invalidate old claim-specific audit hashes; a passing internal verifier is not an independent correctness audit.

Most generated files under `backend/evaluations/` are ignored by Git. A fresh checkout contains the scoring script and gold cases, but may not contain historical capture directories or local browser-evaluation scripts mentioned in older reports. Generate captures before running commands that consume them.

## Bounded evaluation deployment

For the supplied request-accounting override:

```sh
docker compose -f compose.yaml -f compose.eval.yaml up -d --build
```

This disables browser automation and uses a persistent Exa ledger with a 280-request ceiling. It consumes configured provider services; it is separate from the offline test suite. Return to the intended base/development configuration after evaluation and preserve the ledger when reviewing usage.
