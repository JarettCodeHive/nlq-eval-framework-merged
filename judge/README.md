# Judge

LLM-as-Judge scoring module. Sources platform responses through a shared client
interface and returns a `JudgeVerdict` with a rationale
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
  heuristic_judge.py      Deterministic test double — CI/dev only, NOT semantic scoring
  llm_judge.py            Provider-independent judge scaffolding (cache, audit, modes)
  openai_judge.py         Azure/OpenAI backend (async, cached, prompt-logged)
  floodgate_judge.py      Anthropic-via-Floodgate backend (Apple's model proxy)
  cache.py                Content-addressed judge cache (sha256 over prompt+model)
  calibration.py          §10.2 gate: uncalibrated LLM judges refuse to score
  exact_match.py          Deterministic answer comparison — zero-tolerance numeric
  prompts.py              Prompt rendering + prompt_version stamping
  parsing.py              JSON verdict extraction with schema validation
  config.py               LLM settings loader — reads .env, redacts secrets
  cli.py                  Evaluation runner: python -m judge.cli  (see docs/judge_runbook.md)
  run_log.py              Structured per-run event log + credential redaction
  input_contract.py       CSV input contract for question/pair batches
  sql_pulse.py            DuckDB reference_sql harness (§14.2 pair verification)
  pulse_client.py         Real platform API client (HC-4) — `--pulse live`
  templates/judge/        Jinja prompt templates (combined + per-dimension)
  anchors/                Calibration anchor sets per domain
  tests/                  Judge module tests
  .env.example            Copy to judge/.env — Azure/OpenAI/Floodgate + PULSE_* creds
```

(Per-domain judge configs live at `config/judge/*.json`, per §13.1.)

## Modes

- `--judge heuristic` — deterministic test double (CI/dev only, string overlap,
  NOT semantic scoring; never release-eligible).
- `--judge llm --mode combined` — 1 prompt for all 4 dimensions.
- `--judge llm --mode per_dimension` — 4 prompts, no halo effect between dims.

Platform (Pulse) source is chosen with `--pulse`:

- `live` — the real platform API (HC-4). **The default, and the only source a
  release run accepts.** POSTs to
  `{PULSE_BASE_URL}/api-proxy/org/{PULSE_ORG_ID}/ai-svc/v2/tco/chat` (QA:
  `https://api-qa.platform.claris.com`); needs `PULSE_AUTH_TOKEN` + `PULSE_ORG_ID`
  in `judge/.env`, and the `TCOApiV2Feature` flag enabled for the org. Verified
  against QA org 4104: the answer comes from `response.analysis`, and the SQL from
  **every** `analysis_request` entry — not only the one labelled `primary`, which
  is often a supporting breakdown rather than the statement that answers the
  question. `PULSE_STREAM=true` consumes the SSE stream instead. Each question is
  submitted in its own `chat_session_id` so answers stay independent, and the
  ids actually sent are what `pulse_raw/` records.
- `sql` — executes each pair's `reference_sql` in DuckDB against a CSV dataset
  (`--input-csv` + `--pulse-data`). This verifies that pairs and dataset agree
  (§14.2); it never scores the platform, and its runs are PREVIEW-only.

## Section 10.1 guarantees

- The judge-facing request contains the question, platform answer,
  `expected_answer`, `judge_reference`, and platform-generated SQL. Ground-truth
  `reference_sql` remains in the verification pipeline and is never passed to
  the judge or included in its cache key.
- Every locked domain has a JSON config covering model, temperature, seed,
  token limit, retry policy, and concurrency. Temperature is fixed at zero;
  seed is sent where the provider supports it. A control that does not reach the
  provider is never claimed: a run records `judge_temperature_enforced` and
  `judge_seed_enforced`, and clears `seed` from the provenance block when the
  configured seed was dropped (Anthropic has none at all).
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
python -m judge.cli --judge heuristic --pulse sql \
  --input-csv pairs.csv --pulse-data data/crm/
```

For live LLM runs:

```bash
cp judge/.env.example judge/.env   # then fill in AZURE_OPENAI_* or OPENAI_API_KEY
python -m judge.cli --judge llm --pulse live --domain crm --input-csv pairs.csv
```

## Judge provider

`--judge llm` picks its backend from the environment (`judge/.env`); nothing
downstream changes. Caching, audit records, scoring modes and the calibration
gate are identical across providers.

| `LLM_PROVIDER` | Backend | Credential |
|---|---|---|
| `openai` (default) | OpenAI or any OpenAI-compatible gateway | `OPENAI_API_KEY` |
| `azure` | Azure OpenAI | `AZURE_OPENAI_*` |
| `floodgate` | Anthropic through Apple's Floodgate proxy | AppleConnect token or Narrative cert |

### Anthropic through Floodgate

Floodgate is Apple's mandatory proxy for externally-hosted models — traffic
goes to `floodgate.g.apple.com`, never `api.anthropic.com`, and there is no
API key. Onboard once per model family at
<https://genai.apple.com/profile/onboarding>, then:

```bash
# local Mac — authenticates as you, via the AppleConnect CLI
LLM_PROVIDER=floodgate FLOODGATE_MODEL=anthropic.claude-sonnet-4-6 \
  python -m judge.cli --judge llm --pulse live --domain crm --input-csv pairs.csv

# CI / servers — authenticates as a system account, via a Narrative certificate
export FLOODGATE_NARRATIVE_CERT=/tls/tls.crt FLOODGATE_NARRATIVE_KEY=/tls/tls.key
```

Four things differ from the OpenAI path, and all four are visible in the run
artefacts rather than hidden:

- **`model_version` is `floodgate/<model>`**, so verdicts, cache keys and the
  manifest distinguish a run through the proxy from the same model elsewhere.
- **No `seed`** — see §10.1 above.
- **Model choice is a determinism decision, not a quality one.** Claude Sonnet 5
  and Opus 5 use adaptive thinking and **reject `temperature` outright** through
  Floodgate. With no `seed` to fall back on, that leaves a judge with no
  determinism control at all, so `JUDGE_REQUIRE_TEMPERATURE_ZERO=true` (the
  default) refuses the run rather than degrading quietly. Use
  `anthropic.claude-sonnet-4-6`, or `anthropic.claude-haiku-4-5-20251001-v1:0`
  for cheap high volume — both accept `temperature=0`. Verified 2026-09-09.
- **No JSON mode, and prefill is not guaranteed.** Strict JSON is forced by
  prefilling the assistant turn with `{` — but Floodgate routes between AWS
  Bedrock and GCP Vertex on its own, and Vertex-served Claude rejects prefill.
  The judge negotiates it away and falls back on the prompt plus
  `malformed_output_retries`. `judge/parsing.py` stays strict either way.
- **Quota is per-person by default** (resets 5 AM local). A full run is exactly
  the high-volume non-interactive workload a Floodgate *project* is for — set
  `FLOODGATE_PROJECT_TOKEN` to bill it there instead.

Policy, because it constrains what this judge may be pointed at: Anthropic
models through Floodgate are **internal-use only**, must not receive InfoSec
Tier 0/1 data, and since 2026-05-14 may not be used for **distillation or
synthetic data generation**. Scoring answers is evaluation and is fine;
generating the eval dataset with the same models is not.

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
A run writes to two roots, sharing one `run_id`. The §11 deliverables go to
`release/<domain>/scorecards/<UTC-timestamp>/` (`report_output_root` in
`config/scorecard/`):

- `scorecard_summary.csv` — §11.1 one row per domain + per-tier breakout, with
  `baseline_exact_match_pct` / `delta_pct` / `regression_flag`
- `question_results.csv` — §11.2 one row per question, `platform_generated_sql`
  logged for every question
- `scorecard.md` — GitHub-renderable summary
- `scorecard.pdf` — stakeholder deliverable (Phase-4 exit-gate item; §11.3)

The run's own artifacts go to `release/<domain>/eval-runs/<UTC-timestamp>/`
(`run_output_root` in `config/judge/`):

- `results.json` — question-level drill-down per §11.2
- `prompts_log.jsonl` — full prompt/response log (Phase-4 exit-gate item)
- `pulse_raw/<question_id>.json` — the untouched platform payload (live runs)
- `run_log.jsonl` — timestamped event stream for the run
- `run_manifest.json` — what produced this run: git commit, input hash, argv

The run directory is created **before** the first network call, so a run that
dies keeps everything it already fetched. A run that hung for 85 minutes once
left an empty directory behind, because the directory was only created at
write time.

### Raw platform responses (`pulse_raw/`)

A live Pulse question costs 50–170s, so a run that keeps only the extracted
answer and SQL cannot be re-scored offline — it has to be re-queried. Each
response is therefore persisted whole, wrapped in an envelope carrying fetch
time, latency, org, model and chat-session id.

What the payload holds beyond the two extracted fields: `reasoning` (the
platform's own account of how it read the question — the fastest way to
diagnose a wrong answer), `timing` (per-stage server-side ms), `record_counts`
(which tables it touched and how many rows — free confirmation the right
dataset is loaded), `extraction_request` (the tables it chose), and **all** of
`analysis_request`, not just the entry tagged `primary`.

Budget ~72KB per question (~8MB for a 111-pair run). These contain org data;
`release/` is gitignored.

Failures are captured too: a failed query still writes a `pulse_raw` file
holding the error, and logs a `pulse.error` event.

### Run event log (`run_log.jsonl`)

One JSON object per line, flushed and fsynced as it happens:

```json
{"ts": "...", "elapsed_s": 54.1, "event": "pulse.response",
 "question_id": "CRM-T2-03-16", "latency_s": 54.065,
 "server_timing": {"total_ms": 50759}, "answer_chars": 963, "sql_present": true}
```

Events: `run.start`, `pulse.settings`, `pulse.request`, `pulse.response`,
`pulse.error`, `pulse.token_warning`, `pulse.phase_complete`,
`judge.phase_start`, `judge.phase_complete`, `run.complete`.

**No credential ever reaches disk.** `run_log.redact()` masks any key matching
token/key/secret/password/authorization/bearer at any nesting depth, and a test
walks every file a run produces asserting the token appears in none of them.

### Token pre-flight

A Pulse token is a ~1-hour Cognito JWT; a question costs 50–170s. Before a
`--pulse live` run starts it estimates its own duration
(`pairs ÷ pulse-concurrency × 140s`) and refuses to start when the token would
expire mid-run, rather than turning the remaining questions into 401s.
`--ignore-token-expiry` overrides (partial results are still logged).

### PREVIEW vs RELEASE (§10.2, §11.3)

Every run is a **PREVIEW** unless `--release` is passed. A PREVIEW scorecard is
clearly labelled and never establishes or updates a baseline.

A **RELEASE** run requires `--judge llm`, a calibration marker for the domain,
`--platform-version` and `--dataset-version`. The first RELEASE run for a
`platform_version` establishes the immutable baseline
(`scorecard/baselines/<platform_version>.json`, written once then made
read-only). Later RELEASE runs compare against it and set `regression_flag`
where a domain drops ≥ 5 pp; the CLI then exits `4`. Runs against a Pulse
stand-in mark the baseline `provisional` until re-run against the live API.

## The platform under evaluation is an agent, not a model

This shapes the client, and getting it wrong caused real scoring bugs. For one
question Pulse self-reports nine pipeline stages — `domain_check`,
`schema_retrieval`, `guidelines_retrieval`, `extraction_builder`,
`analysis_builder`, `extraction`, `analysis`, `summarization`,
`data_display_preparation` — chooses its own tables, and issues **several SQL
statements**.

Consequences the client now handles explicitly:

**SQL is a set, not a string.** `analysis_request` holds every statement, and
the one tagged `primary` is *not* necessarily the one that answers the
question. On `CRM-T5-01-SEED-01` ("which three campaign types drove the most
engagement?") Pulse issued five statements; `primary` was a bare
`SELECT COUNT(*)` with no GROUP BY — the headline metric — while the answering
query (`GROUP BY campaign_type ORDER BY … LIMIT 3`) was a different entry.
Keeping only `primary` made the judge score `sql_plausibility` 2 and reason
that the SQL "does not group by campaign type, order the groups, or limit the
output to three" — all three of which the discarded statement did. We now pass
every statement, `primary` first and labelled, and the rubric asks whether the
set *taken together* supports the answer.

**Answers are narratives, not scalars.** A reply is typically 1–2KB of prose
with 14–15 numbers in it — a headline figure plus tier/region/status
breakdowns. `exact_match` is numeric-multiset containment, so it tolerates the
prose; but see the caveat under "Exact match" below.

**Questions must be independent.** Each question gets a fresh
`chat_session_id`. Sharing one across a run makes every question a turn in a
growing conversation, where a later answer can be coloured by an earlier
question — a confound invisible in the scorecard.

**Latency is a pipeline property.** 50–170s per question, dominated by
extraction (110s of 164s in one measured case), not by model inference.

## Exact match — what is compared, and the two open items we settled

`expected_answer` is machine-generated from `reference_sql` (§9.3) in a
structured shape, and the scorer parses it rather than pattern-matching the
string:

```
28731                                     bare scalar
26677920.26                               bare scalar, decimal
Standard | 6229                           one labelled value
Awareness | 35726; Retention | 34685      ordered labelled list
Priya Raghavan — 4,182,650.00             Appendix A's em-dash form
```

`;` separates records, `|` (or an em/en dash) separates fields. Each field is
classified and checked on its own terms — see `judge/exact_match.py`.

**Entity labels are required, and each must own its value.** Comparing only
numerics passes an answer that attaches every right number to the wrong entity
(`Bob Smith closed 4,182,650.00` against an expected `Priya Raghavan —
4,182,650.00`), and passes a ranked list whose values are permuted across
categories. That is the shape of the Appendix A worked example and of most T4/T5
pairs, so a numerics-only comparison inflates the §14.2 accuracy gate. A label
must therefore appear *and* be the nearest claimant of its own value; prose that
keeps them together still passes, because §HC-3 forbids deducting for phrasing.

### OI-2 — what "identical" compares

Open, owned by the Platform Owner: *"Zero tolerance is meaningless without a
definition of what 'identical' compares. 1000 vs 1,000 vs 1000.00 must be
settled."*

**Our decision: numerics compare by VALUE.** §HC-3 protects "answer phrasing,
response length and dashboard styling" and fails only "numeric variance"; a
thousands separator, a currency prefix and a trailing `.00` change the rendering,
not the value. So `28,731` equals `28731`, while `4,182,000` still fails
`4,182,650.00` because that is a real loss of value.

Correction C2 says "remove the tolerance parameter from the scorer entirely; do
not make it configurable". We have: there is no tolerance parameter. Two numbers
either are the same number or are not. What is configurable is the *reading* of
the rule, not its strictness — `--numeric-form string` restores byte-identical
comparison in one flag if the Platform Owner rules that way.

This matters practically: the platform always emits thousands separators, so
under byte-identical comparison a correct answer fails. In the 2026-09-09 QA run
the platform answered `28,731` — the right number for the data loaded in org
4104 — and scored FAIL.

### OI-3 — date tolerance

Open. Material change C10 withdrew the ±1 day tolerance carried in earlier
drafts: "Date values returned as answers are facts and compare exactly."

**Our decision: dates compare by day, no tolerance.** ISO on the ground-truth
side; ISO or a month-name rendering (`April 1, 2026`, `1 April 2026`) on the
platform side, because the platform writes dates in prose and a month name
cannot be mistaken for anything else.

This also fixed a plain bug: the old tokeniser read `2026-04-01` as
`['2026', '-04', '-01']` — the hyphens became minus signs — so no date answer
could ever be compared correctly under any ruling.

### Not done deliberately

Spelled-out numerals are **not** recognised by the deterministic scorer. The
judge scores "Five contacts" against an expected `5` as a 5 and should, but
teaching the scorer number words makes an expected `1` match any answer
containing the word "one" — a common false pass traded for a rare false fail.
The two scores diverging there is intended and both are always reported (§11.3).

### Residual risk

For a bare scalar with no label to anchor it, presence is still presence: a long
answer carrying a category breakdown can contain the expected number
incidentally and pass. Nothing in the response payload binds a bare number to
its role, so this is a floor on the method rather than a bug. Labelled pairs are
not exposed to it.

## Relationship to `judge_module/`

`judge/` is the NLQ-specific implementation the Execution Scope contracts for
(§13.1). `judge_module/` is a framework-agnostic LLM-as-judge package with a
rubric-parameterised prompt — not a spec deliverable, and `judge/` does not import
it today.

Two hardening changes are deliberately kept at parity across both, because each
one is about a score meaning what it claims:

| | `judge/` | `judge_module/` |
|---|---|---|
| `seed_enforced` on the verdict | ✅ | ✅ |
| Anchor set refused when a constant score would pass | `calibration.anchor_strength` + `AnchorSetTooWeak` | same names, generic over the rubric's score range |
| Pass bound to model + prompt + mode | marker file carries `judge_fingerprint` | `CalibrationReport.judge_fingerprint` (the package writes no marker; the caller persists it) |

The deterministic scorer (`exact_match.py`) is **not** ported and should not be:
the `label \| value; label \| value` shape it parses is this project's Q&A pair
contract, not a general one.

## Checks the spec asks for beyond the two scores

Both are diagnostics on the drill-down. Neither changes an `exact_match_result`
or a judge dimension — §11.3 forbids composites and §22 puts per-stage pipeline
evaluation out of scope.

- **T3 NULL handling (§9.2).** The spec names "an explicit NULL-handling check"
  without defining it. What T3 tests is whether the platform preserves the rows
  a LEFT OUTER JOIN preserves; the classic failure is answering an outer-join
  question with an inner join, silently dropping unmatched rows and returning a
  plausible smaller number. So: applicable when the pair is T3 or its reference
  SQL uses an outer join; PASS when the platform SQL contains an outer join or an
  equivalent null-aware construct (`NOT EXISTS`, `COALESCE`, `IS NULL`); FAIL
  when every join is inner; UNKNOWN when no SQL came back — absent evidence is
  not a pass.
- **Rephrase groups (§9.5).** Variants sharing a `rephrase_group_id` must
  resolve the same ground-truth values. Disagreement is reported as a *platform*
  finding. Variants declaring different `expected_answer`s are reported
  separately as a *dataset* defect, since a group asks one underlying question.

## Calibration gate (§10.2)

An LLM judge run refuses to start against a domain that has no
`judge/.calibration/<domain>.passed.json` marker. Override with
`--allow-uncalibrated` for smoke tests only; scores are then labelled
`calibrated=false` in the summary and **must not** feed a scorecard release.

A marker vouches for **one judge**, not for the domain in the abstract. It
records a `judge_fingerprint` — `model_version`, `prompt_version`, `mode` — and a
run differing on any of the three is treated as uncalibrated. Editing a prompt
template changes `prompt_version`, so every marker correctly goes stale.

The anchor set is itself checked before any provider budget is spent. §10.2 asks
for anchors "spanning the score range — not 10 easy passes", enforced as: ≥3
distinct human scores per dimension, and no constant score may satisfy the
acceptance test. The shipped CRM set fails that and is quarantined as
`anchors/crm.provisional.json` — see `judge/anchors/README.md`.
