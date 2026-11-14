# Answer quality evaluation

Each case file contains audited facts from a specific historical capture, including
the expected value, acceptable source classes, source passage and capture hash.
The seven cases without captures deliberately have empty fact lists and an
`awaiting live capture and semantic audit` status. Their recall is unknown.

The three populated cases are a **partial factual gold set**. They do not measure
all requested candidate × requirement slots. In particular, missing laptop stock
and delivery evidence and missing valid hotels still fail the task acceptance
gates, even when every fact in the partial gold set is recalled.

`captured-audits.json` records human judgments of the original displayed claims.
Judgments are keyed by a hash of the exact candidate, field, statement and quoted
evidence. Changing a statement invalidates its old audit. Offline precision stays
unknown until the changed statement is audited; passing the application's own
verifier does not count as an independent accuracy audit.

From `backend/`, score the saved baseline without provider requests:

```sh
.venv/bin/python evaluations/score_quality.py evaluations/pipeline/opt-final-fixed \
  --audits evaluations/gold/captured-audits.json \
  --output evaluations/quality-2026-11-14/baseline/scores.json
```

Exercise current local grounding and rendering on the same saved answers:

```sh
.venv/bin/python evaluations/score_quality.py evaluations/pipeline/opt-final-fixed \
  --reground --audits evaluations/gold/captured-audits.json \
  --baseline evaluations/quality-2026-11-14/baseline/scores.json \
  --output evaluations/quality-2026-11-14/offline-reground/scores.json
```

This second command does **not** regenerate the answer, run the semantic verifier,
retrieve replacement candidates or measure live latency. It isolates its disk
cache and preserves the original observation timestamps.

Run the complete live protocol against a running API and worker:

```sh
.venv/bin/python evaluate_pipeline.py --output evaluations/quality-live \
  --timezone Europe/Helsinki --concurrency 2 --repetitions 2 --followup --cache-checks
```

The full protocol submits 23 runs, below its 26-run ceiling. It stops scheduling
new runs after a provider quota or billing error. At most one already running
partner in the same two-case batch can finish after that error. Follow-ups reuse
the corresponding laptop chat and record the predecessor run ID. Worker and
observed queue times are reported separately.

Run one captured-evidence pass with live Gemini and no Exa requests:

```sh
.venv/bin/python evaluate_pipeline.py --output evaluations/quality-replay \
  --replay-from evaluations/pipeline/opt-final-fixed \
  --cases finland_laptops estonia_hotels loyly_price --timezone Europe/Helsinki
```

Then score the results. Add `--live-judge` to spend Gemini requests on independent
point-by-point semantic audits; credentials are loaded through the evaluator's
existing redacted configuration path. Source text remains untrusted data.

```sh
.venv/bin/python evaluations/score_quality.py evaluations/quality-replay \
  --live-judge --baseline evaluations/quality-2026-11-14/baseline/scores.json \
  --output evaluations/quality-replay/scores.json
```

Audit source liveness and candidate relevance separately. The scorer reports
these as unmeasured, rather than inferring them from a live-looking URL or a
verifier status. Exact values and historical prices in the gold files are not
current recommendations.
