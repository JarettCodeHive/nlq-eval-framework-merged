# Evaluation Orchestrator — Architecture (Design Only)

Status: **DEFERRED — do not build** (scope doc §12). This document and
`config/orchestrator/config.contract.yaml` are the design-time deliverables
§12.1 asks for while the client's internal-tool-vs-TechM-Orchestrator decision
is open (§23 OI-8). No orchestrator implementation code exists or should be
added here until that decision resolves AND the datasets/Q&A pairs are
accepted.

## 0. The one thing this document has to get right

§12.1 flags one deliverable as paying off "regardless of the decision": the
**interface into our scoring and scorecard modules**. Everything else here is
useful but replaceable if the client's tool wins. Section 4 (Interface
Definition) is therefore the load-bearing section — read it even if nothing
else.

## 1. What already exists vs. what §12 actually defers

This is easy to get wrong, so it is stated once, explicitly: **a driving loop
already exists and is already in production use** — `judge/cli.py` +
`scorecard/`. It submits every question to the platform, captures the
response and generated SQL, runs deterministic exact-match, runs the LLM
judge, and writes the §12.2 result artifacts. That is not a violation of the
DEFERRED status, because HC-4 ("all evaluation goes through the real
platform") requires *some* driving loop to exist for the judge/scorecard
tracks (C/D) to function at all — §12 defers something narrower.

What §12 actually defers is **genericizing** that loop: today it is
hardcoded to one platform's shape —

| Hardcoded today (`judge/pulse_client.py`) | §12.2 requirement |
|---|---|
| `DEFAULT_CHAT_PATH = "/api-proxy/org/{org_id}/ai-svc/v2/tco/chat"` | endpoint configurable |
| `PULSE_BASE_URL` / `PULSE_AUTH_TOKEN` / `PULSE_ORG_ID` env vars, `Authorization: Bearer <jwt>` | auth method configurable |
| Fixed request body: `{prompt, messages, chat_session_id, message_session_id, model_name, is_quick_prompt, stream, stream_thinking}` | request shape configurable |
| `_ANSWER_KEYS` / `_SQL_CONTAINER_KEYS` / `_SQL_SCALAR_KEYS` candidate-key lists for parsing the response | response field mapping configurable |
| One natural-language `prompt` string per question | question format configurable |
| `timeout_s=420`, `max_retries=3`, `backoff_base_s=0.5/backoff_max_s=8.0` | timeouts configurable (already are, just not via a shared contract) |
| `--pulse-concurrency` / `--concurrency` CLI flags | concurrency configurable (already is) |

So: two of seven are already parameter-driven (timeouts, concurrency); five
are Pulse-specific constants. **"Build the Orchestrator" would mean lifting
those five into `config/orchestrator/config.contract.yaml` and making
`judge/cli.py`'s platform leg read the contract instead of importing
`PulseClient` directly** — not writing a new driving loop from zero. That
scope estimate (lift five fields out of one file, add a thin adapter
interface) is the basis for the "twelve hours of design work" §23 OI-8
mentions being committed regardless of the decision.

## 2. Component diagram

```
                          ┌─────────────────────────────┐
                          │   config/orchestrator/       │
                          │   config.contract.yaml       │   <- §12.1 config contract
                          │   (endpoint, auth, request    │      (DRAFT, see sibling file)
                          │    shape, field mapping,       │
                          │    question format, timeouts,  │
                          │    concurrency)                │
                          └───────────────┬───────────────┘
                                          │ parameterises
                                          ▼
   qa_pairs/release/.../qa_pairs.csv            ┌─────────────────────────┐
   (question_id, natural_language_question, ───▶│   DRIVING LOOP           │
    expected_answer, judge_reference, tier,      │   (today: judge/cli.py   │
    reference_sql, rephrase_group_id, ...)       │   `_run()` + the Pulse   │
                                                  │   leg of `_collect_      │
                                                  │   and_score()`)         │
                                                  │                          │
                                                  │  per question:           │
                                                  │   1. submit  ───────────┼──▶  PLATFORM API
                                                  │   2. capture answer+SQL  │     (Pulse today;
                                                  │   3. exact-match score   │◀────  any platform
                                                  │      (judge/exact_match) │      under the
                                                  │   4. LLM-judge score     │      contract)
                                                  │      (judge/floodgate_   │
                                                  │       judge, 4 dims)     │
                                                  └────────────┬─────────────┘
                                                                │ JudgeRequest /
                                                                │ JudgeVerdict /
                                                                │ ExactMatchOutcome
                                                                │ (§4 — the stable
                                                                │  interface)
                                                                ▼
                                                  ┌─────────────────────────┐
                                                  │   SCORECARD              │
                                                  │   (scorecard/summary.py, │
                                                  │    baseline.py,          │
                                                  │    report.py)            │
                                                  │                          │
                                                  │  - results.json          │
                                                  │  - question_results.csv  │
                                                  │  - scorecard_summary.csv │
                                                  │  - scorecard.md / .pdf   │
                                                  │  - baseline + regression │
                                                  │    flag (§11.3, ≥5pp)    │
                                                  └─────────────────────────┘
```

## 3. Data flow (end to end, one run)

1. **Input.** A Q&A pair CSV (`judge/input_contract.py:load_input_csv`) —
   either resolved automatically from `--domain`/`--profile`
   (`judge/resolve.py:judge_input_csv`) or passed explicitly via
   `--input-csv`. Every row already carries `question_id`,
   `natural_language_question`, `expected_answer`, `judge_reference`,
   `tier`, `reference_sql`, `rephrase_group_id`.
2. **Submit.** Each pair's question text is sent to the platform leg. Today
   that's `PulseClient.query(question_id)`, built from `PulseSettings`
   (§5). Under the contract, this becomes: render the config's `request`
   template with the question text, POST to the config's `endpoint`, using
   the config's `auth` block.
3. **Capture.** The raw response is parsed into a `PulseResponse`
   (`answer_text`, `generated_sql`, `clarify`) using the candidate-key lists
   in §1. Under the contract, this becomes: apply the config's
   `response_field_mapping` instead of the hardcoded key lists. The raw
   payload is always archived to `pulse_raw/*.json` regardless — this does
   not change, because it's how a failure gets traced to a pipeline stage
   (§14.2 condition 33).
4. **Score — deterministic.** `judge/exact_match.py:evaluate_answer()` runs
   immediately, independent of the judge call (see §6 — this is the
   "where does deterministic scoring sit" answer). Zero numeric tolerance,
   §HC-3.
5. **Score — judge.** `judge/floodgate_judge.py` scores the same pair across
   4 rubric dimensions (`judge/contracts.py:DIMENSIONS`), UNLESS the
   platform returned a clarification request (§2e — fixed score, no
   provider call) or the §10.2 calibration gate refuses the run outright
   (before step 2 even starts).
6. **Write.** Both scores land in one row (`judge/cli.py:_row_dict`) — never
   blended (§11.3) — and the run writes `results.json`, then the scorecard
   module writes the four §11 artifacts plus baseline/regression comparison.

## 4. Interface Definition — the part that pays off regardless

This is what a replacement driving loop (ours, generalized, or the client's
internal tool) has to produce to use our scorer and scorecard unmodified.
These three contracts already exist and are stable:

**a. The request into the judge** — `judge/contracts.py:JudgeRequest`
   (`question`, `expected_answer`, `judge_reference`, `platform_answer`,
   `generated_sql`, `domain`, `question_id`). Any driving loop that can
   populate these six fields per question can call
   `judge/exact_match.py:evaluate_answer(expected_answer, platform_answer,
   policy)` and `judge/floodgate_judge.py` (or any `JudgeClient`) directly.

**b. The verdict out of the judge** — `judge/contracts.py:JudgeVerdict` /
   `ClarificationVerdict`, and `judge/exact_match.py:ExactMatchOutcome`.
   These three are what `scorecard/summary.py` consumes — not the raw
   platform response, not `JudgeRequest`.

**c. The result row the scorecard writes** —
   `scorecard/summary.py:QUESTION_FIELDNAMES` (`run_id`, `question_id`,
   `domain`, `tier`, `natural_language_question`, `expected_answer`,
   `actual_answer`, `exact_match_result`, `exact_match_detail`,
   `expected_answer_off_contract`, `null_handling`, `null_handling_detail`,
   `platform_generated_sql`, the judge dimension columns, `judge_overall`,
   `judge_rationale`, `judge_error`, `platform_error`, `rephrase_group_id`)
   and `SUMMARY_FIELDNAMES` for the per-domain roll-up. This CSV shape is a
   **superset** of §12.2's minimal list (`question, expected, actual,
   generated_sql, det_score, judge_score, judge_rationale`) — nothing here
   needs to shrink to satisfy the spec, only to stay stable.

**The actual pluggable seam**, if the client's tool is the one driving: it
needs to call `judge/cli.py`'s `_collect_and_score()` shape (fetch → build a
`JudgeRequest` → score) per question, OR, more simply, write its own
submissions into the same `InputRow`/pair-dict shape consumed by
`judge/input_contract.py` and let `judge/cli.py --pulse <their-adapter>`
drive scoring — exactly the same seam `--pulse sql` already uses for
offline DuckDB replay (`judge/sql_pulse.py:SQLPulse`). **`SQLPulse` is the
existing proof that this interface is already adapter-shaped**: it
implements the same two methods as `PulseClient` (`query(question_id)`,
`mode`, `available_ids()`) against a completely different backend. A
third adapter — generic HTTP driven by `config.contract.yaml` — is the
only new code `12.2` actually requires.

## 5. Config contract (draft)

See `config/orchestrator/config.contract.yaml` for the actual draft file.
It generalizes `judge/pulse_client.py:PulseSettings` (§1 table) into a
platform-agnostic shape: `endpoint` (templated path + base URL), `auth`
(method + header/token source), `request` (templated body), `response`
(field-mapping, replacing the hardcoded candidate-key lists),
`question_format`, `timeouts`, `concurrency`. Pulse's current values are
included as the worked example, since they're the only platform we've
actually integrated.

## 6. Where deterministic scoring sits relative to judge scoring

Side by side, never combined — this is §11.3, not a design choice up for
debate:

- **Deterministic exact-match** (`judge/exact_match.py`) runs on every
  non-error, non-clarification pair, synchronously, with zero numeric
  tolerance (§HC-3). It never calls the judge and never waits on it.
- **LLM judge** scoring runs independently, pipelined *concurrently* with
  the platform fetch (not after it) via separate semaphores in
  `_collect_and_score()` — scoring starts on the first answer while later
  questions are still in flight. This hides the ~12s/answer judge latency
  inside the platform wait rather than adding to it, which is why a
  full-domain run costs roughly `max(platform_time, judge_time)` instead of
  their sum.
- They are reported as two parallel fields on one row
  (`exact_match_result` vs `judge_scores`/`judge_overall`) and two parallel
  summaries (`exact_match.pass_pct` vs `judge.mean_overall`). A scorecard
  consumer that averages them together is misusing the output — nothing in
  the contract supports that, by design.
- The §10.2 calibration gate sits in front of the judge leg only: an
  uncalibrated judge fingerprint (model + prompt version + mode) refuses
  the run (`judge/cli.py:_run`, exit code 3) before either leg starts,
  unless `--allow-uncalibrated` explicitly demotes the run to a smoke test
  that can never feed a scorecard baseline.

## 7. Failure and retry model

| Failure | Where it's handled today | Retry behavior |
|---|---|---|
| Platform API error (timeout, 5xx, malformed response) for one question | `PulseClient` → caught per-pair in `_collect_and_score`, recorded as `PlatformError`, excluded from exact-match denominator | `max_retries=3` with exponential backoff (`backoff_base_s`/`backoff_max_s`) **inside `PulseClient`**, before it surfaces as a pair-level error. No run-level retry — a failed pair stays failed for that run. |
| Judge provider error for one question | Caught per-pair, returned as the exception object in the row tuple, counted in `judge_errors` | None at the orchestrator level — relies on the provider client's own retry (Floodgate transport). A nonzero `judge_errors` count fails the whole run's exit code (`return 1`), signalling "re-run," not "accept partial." |
| Auth token expiring mid-run | `check_token_headroom()` estimates run duration vs. token TTL *before* starting; refuses to start unless `--ignore-token-expiry` or `PULSE_REFRESH_*` is configured | Only platform with self-healing: `PULSE_REFRESH_*` configured → `PulseClient` re-mints its own token mid-run. No generic equivalent in the draft contract yet — flagged as an open design question for whichever platform-agnostic adapter gets built. |
| A run dying mid-way (process killed, machine lost) | `run_manifest.json` and `run_log.jsonl` are written to `out_dir` **before** any network call, so partial progress and the run's exact arguments survive even a hard kill | No resume-from-partial today — a killed run must be re-run from scratch. Worth deciding explicitly if/when this is built: resume would need per-question idempotency keyed on `question_id`, which the input contract already provides for free. |
| Uncalibrated judge / non-default comparison policy / missing `--platform-version` on a `--release` run | Refused **before** any question is submitted (`_resolve_scorecard_mode`, `_run` early returns) | N/A — these are gates, not transient failures; the fix is operator action, not a retry. |

The pattern worth carrying into any generalized orchestrator: **isolate
failures per-pair, never let one bad question abort the run**, and **persist
the manifest/log before the first network call** so a crash is diagnosable,
not silent.

## 8. Augment-vs-migrate comparison framework

To be filled in once the client demos their internal tool (§23 OI-8). The
criteria below are the ones this codebase's design actually depends on —
answering them is what lets the team respond within days rather than
starting the analysis cold:

| Criterion | Question to answer at demo time | Why it matters here |
|---|---|---|
| Scoring seam | Can their tool call out to an external scorer per question, or does it only report its own pass/fail? | If it can't call out, §4's interface is dead on arrival — we'd have to re-implement exact-match + judge scoring inside their tool, duplicating `judge/exact_match.py` and the calibration gate. |
| Config shape | Does it already support arbitrary REST endpoints with a configurable request/response mapping, or is it also hardcoded to one platform shape? | Directly comparable to §1/§5 — if it has the same gap we do, "augment" doesn't save the config-contract work either. |
| Determinism controls | Does it support (or forbid) temperature=0 / seed pinning for an LLM-judge leg, and does it report that honestly (cf. `judge_temperature_enforced`/`judge_seed_enforced`)? | §10.1/§10.2 compliance is non-negotiable; a tool that silently can't guarantee this blocks a release gate regardless of which org owns it. |
| Baseline & regression | Does it have an equivalent to `scorecard/baseline.py`'s established-baseline + ≥5pp regression flag (§11.3), or would ours need to run alongside it anyway? | Determines whether migrating means losing regression detection or keeping it as a bolt-on. |
| Audit trail | Does it persist a per-run manifest + full prompt/response log before any network call (§14.2 condition 33), the way `run_manifest.json`/`prompts_log.jsonl` do? | A tool that can't produce "generated SQL logged for every failed question" fails an accuracy-gate condition outright. |
| Cost / ownership | Who pays for and owns the judge LLM credentials routed through it (§23 OI-9)? | Changes the pass-through-vs-absorbed cost conversation independent of which tool wins. |
| Migration cost if we switch later | If we augment now and migrate later, what of §1-§7 is thrown away vs. reusable? | Per §1, the answer today is: almost nothing — `judge/exact_match.py`, `scorecard/`, and the `JudgeRequest`/`JudgeVerdict` contracts are platform-agnostic already; only the driving-loop adapter (five fields in §1's table) is platform-specific and would be rebuilt either way. |

## 9. What this document deliberately does not do

- No code under `orchestrator/` — per the DEFERRED status, only this
  document and the config contract draft exist.
- No decision on augment-vs-migrate — §8 is a framework to apply at demo
  time, not a recommendation.
- No change to `judge/cli.py` or `judge/pulse_client.py` — generalizing the
  Pulse adapter into one that reads `config.contract.yaml` is the
  twelve-hour implementation task §23 OI-8 refers to, and stays out of
  scope until the tooling decision resolves.
