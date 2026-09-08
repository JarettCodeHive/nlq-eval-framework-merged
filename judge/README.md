# Judge

LLM-as-Judge scoring module. Sources platform responses through a shared client
interface (mock or Azure/OpenAI) and returns a `JudgeVerdict` with a rationale
and four rubric-anchored dimensional scores.

> Flow diagrams (mermaid) for this module live at
> [`docs/judge_flow.md`](../docs/judge_flow.md).

## What the judge scores

Per Execution Spec §10, each response is scored on four independent dimensions,
each an integer 1–5:

- **Factual correctness** — anchored on `expected_answer`
- **Completeness** — anchored on `judge_reference` (the ideal full response)
- **Format adherence** — anchored on `judge_reference` formatting
- **SQL plausibility** — assessed against the platform-generated SQL only

The rubric encodes the non-determinism boundary explicitly (§HC-3): wording,
length, tone, and dashboard styling never count as failures. Numeric
disagreement is always a failure. `reference_sql` is **never** passed to the
judge.

Deterministic exact-match (§HC-3) and judge scores are reported side by side,
**never combined** into a composite (§11.3 last line).

## Layout

```
judge/
  contracts.py            Pydantic contracts: JudgeRequest, JudgeVerdict, DIMENSIONS
  client.py               Abstract JudgeClient (judge_many, aclose)
  mock_judge.py           Heuristic stand-in judge — CI/dev fallback only, NOT semantic
  openai_judge.py         Live Azure/OpenAI backend (async, cached, prompt-logged)
  cache.py                Content-addressed judge cache (sha256 over prompt+model)
  calibration.py          §10.3 gate: uncalibrated LLM judges refuse to score
  exact_match.py          Deterministic answer comparison — zero-tolerance numeric
  prompts.py              Prompt rendering + prompt_version stamping
  parsing.py              JSON verdict extraction with schema validation
  config.py               LLM settings loader — reads .env, redacts secrets
  cli.py                  Evaluation runner: python -m judge.cli  (see docs/judge_runbook.md)
  input_contract.py       CSV input contract for question/pair batches
  mock_pulse.py           Mock/Echo/SQL Pulse stand-ins (CI, dev, offline)
  pulse_client.py         Real platform API client (HC-4) — `--pulse live`
  templates/judge/        Jinja prompt templates (combined + per-dimension)
  anchors/                Calibration anchor sets per domain
  tests/                  Judge module tests
  data/pulse_fixtures/    Mock Pulse responses (good/regressed/bad sets)
  .env.example            Copy to judge/.env — Azure/OpenAI + PULSE_* creds
```

(Per-domain judge configs live at `config/judge/*.json`, per §13.1.)

## Modes

- `--judge mock` — deterministic heuristic (CI/dev only, scores are NOT semantic).
- `--judge llm --mode combined` — 1 prompt for all 4 dimensions.
- `--judge llm --mode per_dimension` — 4 prompts, no halo effect between dims.

Platform (Pulse) source is chosen with `--pulse`:

- `good` / `regressed` / `bad` — canned fixtures (default; CI/dev)
- `echo` / `sql` — offline stand-ins over authored pairs (`--input-csv`)
- `live` — the real platform API (HC-4). POSTs to
  `{PULSE_BASE_URL}/api-proxy/org/{PULSE_ORG_ID}/ai-svc/v2/tco/chat` (QA:
  `https://api-qa.platform.claris.com`); needs `PULSE_AUTH_TOKEN` + `PULSE_ORG_ID`
  in `judge/.env`, and the `TCOApiV2Feature` flag enabled for the org. Verified
  against QA org 4104: the answer comes from `response.analysis` and the SQL from
  the `analysis_request` entry with `id="primary"`. `PULSE_STREAM=true` consumes
  the SSE stream instead. Only `live` yields a non-provisional release baseline.

## Section 10.1 guarantees

- The judge-facing request contains the question, platform answer,
  `expected_answer`, `judge_reference`, and platform-generated SQL. Ground-truth
  `reference_sql` remains in the verification pipeline and is never passed to
  the judge or included in its cache key.
- Every locked domain has a JSON config covering model, temperature, seed,
  token limit, retry policy, and concurrency. Temperature is fixed at zero;
  seed is sent where the provider supports it.
- Transient API errors use bounded exponential backoff. Malformed structured
  output is retried according to `malformed_output_retries`, then recorded as a
  loud per-item error.
- Every dimension has its own integer score and non-empty rationale. Numeric
  strings and floats are rejected rather than coerced.
- LLM audit JSONL records contain the stable `judge_run_id`, full prompt, full
  raw response, attempt number, parse error (if any), and cache-hit state.

## Quick start

```bash
python -m pip install -r requirements.txt
python -m judge.cli --judge mock --pulse good
```

For live LLM runs:

```bash
cp judge/.env.example judge/.env   # then fill in AZURE_OPENAI_* or OPENAI_API_KEY
python -m judge.cli --judge llm --pulse good --domain crm
```

## Rubric PDF (§14.1 deliverable)

```bash
python -m judge.rubric              # writes docs/judge_rubric.pdf
python main.py rubric               # same, via the framework dispatcher
```

Rendered from the live judge templates, so it cannot drift from the calibrated
prompt. The cover page stamps `prompt_version` — Platform Owner sign-off (for
the interpolated score levels 2 and 4) is against that exact revision.

## Scorecard output

The scorecard package lives at the repo top level (`scorecard/`, per §13.1).
Every run writes into `judge/runs/<UTC-timestamp>/`:

- `results.json` — question-level drill-down per §11.2
- `scorecard_summary.csv` — §11.1 one row per domain + per-tier breakout, with
  `baseline_exact_match_pct` / `delta_pct` / `regression_flag`
- `question_results.csv` — §11.2 one row per question, `platform_generated_sql`
  logged for every question
- `scorecard.md` — GitHub-renderable summary
- `scorecard.pdf` — stakeholder deliverable (Phase-4 exit-gate item; §11.3)
- `prompts_log.jsonl` — full prompt/response log (Phase-4 exit-gate item)

### PREVIEW vs RELEASE (§10.3, §11.3)

Every run is a **PREVIEW** unless `--release` is passed. A PREVIEW scorecard is
clearly labelled and never establishes or updates a baseline.

A **RELEASE** run requires `--judge llm`, a calibration marker for the domain,
`--platform-version` and `--dataset-version`. The first RELEASE run for a
`platform_version` establishes the immutable baseline
(`scorecard/baselines/<platform_version>.json`, written once then made
read-only). Later RELEASE runs compare against it and set `regression_flag`
where a domain drops ≥ 5 pp; the CLI then exits `4`. Runs against a Pulse
stand-in mark the baseline `provisional` until re-run against the live API.

## Calibration gate (§10.3)

An LLM judge run refuses to start against a domain that has no
`calibration/<domain>.passed.json` marker. Override with
`--allow-uncalibrated` for smoke tests only; scores are then labelled
`calibrated=false` in the summary and **must not** feed a scorecard release.
