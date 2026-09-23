# Judge + Scorecard — Runbook

How the LLM-as-Judge scoring module and the regression scorecard work, what
changed in the September integration pass, and how to run an evaluation against
the live QA platform.

Scope of this doc: `judge/` and `scorecard/` as they exist in this repository.

`judge_module/` — the standalone, framework-agnostic packaging of the same judge
(§12.1) — is **not part of this tree**. The §13.1 repository layout does not
include it, so it lives separately on `feat/judge-module-integration`. Anything
below referring to it describes that branch, not this checkout.

---

## 1. What it does

For every Q&A pair the framework submits the natural-language question to the
platform (Pulse / Claris AI Service), captures the answer **and the generated
SQL**, then scores that response two independent ways:

| Scorer | Question it answers | Output |
|---|---|---|
| **exact-match** (`judge/exact_match.py`) | Are the numbers exactly right? (§HC-3, zero tolerance) | PASS / FAIL / NOT_APPLICABLE / ERROR |
| **LLM-as-judge** (`judge/openai_judge.py`) | Is the answer complete, correctly formatted, coherent, and is the SQL plausible? (§10) | 4 integer scores 1–5 + a rationale each |

The two are always reported **side by side, never blended** (§11.3). Results
aggregate into a **scorecard** (`scorecard/`): per domain × tier, compared to an
immutable baseline, with an automated `regression_flag` when a domain drops
≥ 5 percentage points.

`judge_module/` is the same LLM-as-judge machinery packaged as a standalone,
framework-agnostic library the client's evaluation orchestrator can import
(§12.1). It is not present in this repository — see the scope note at the top.

---

## 2. What changed this pass

| Commit | Change |
|---|---|
| `d322c7a` | **§10.1 compliance.** `reference_sql` removed from the judge-facing contract/prompt/cache key. Per-domain configs for all 5 domains. Fixed seed. Bounded backoff. `judge_run_id` on every audit record. Malformed output retried then failed loudly. Strict integer scores (no coercion). One rationale per dimension. |
| `7db6e00` | **§11 scorecard.** Moved to top-level `scorecard/`. `scorecard_summary.csv` (§11.1 full schema) + `question_results.csv` (§11.2, SQL logged for every question). Immutable write-once baseline per `platform_version`. Domain-level regression flag at −5 pp. PREVIEW vs RELEASE split. |
| `4021dda` | **Rubric PDF** (§14.1). `python -m judge.rubric` → `docs/judge_rubric.pdf`, rendered from the live templates, with a sign-off block. |
| `30cd7c4` `f19fc62` `73efa8c` | **Real Pulse client** (`--pulse live`, HC-4). Verified against QA org 4104: `POST /api-proxy/org/{id}/ai-svc/v2/tco/chat`, `Bearer` auth, answer from `response.analysis`, SQL from `analysis_request[id="primary"]`. JSON and SSE both supported. |
| `1b87731` | **Corp TLS proxy.** `truststore` injected at startup so the LLM gateway and Pulse both verify against the OS CA store. |
| `cec3f8b` | **Concurrent + fault-tolerant live run.** `--pulse-concurrency` (default 4). One failed/timed-out Pulse call becomes an `ERROR` row — recorded, excluded from the exact-match denominator, not scored — instead of aborting the batch. |
| `682d24b` | **temperature-0 fallback.** `JUDGE_REQUIRE_TEMPERATURE_ZERO=false` lets a GPT-5/o1-family deployment run on "model default + fixed seed"; the run records `judge_temperature_enforced=false`. |
| `3ed9804` | **OI-2 numeric normalization.** `--normalize-numerics` strips thousands separators + trailing decimal zeros before exact-match (`$438,632.65` matches `438632.65`). OFF by default; runs using it are marked in the scorecard. |
| `78b9841` | **`judge_module/`** — standalone rubric-driven judge package. Not in this tree; see the scope note. |
| `bd908af` | **black** on the judge-owned files + `main.py`. |

Full suite after all of this: **~170 pass, 1 pre-existing unrelated generator
failure** (`tests/generators/common/test_csv_export.py`).

---

## 3. Setup

```bash
# Python 3.11
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create **`judge/.env`** (gitignored). Two blocks — see `judge/.env.example` for
the annotated full surface.

The **judge model is not set here**: it comes from `config/judge/<domain>.json`,
which outranks the environment, so a run's provenance is reproducible from
committed config rather than from whoever's machine it ran on. `judge/.env`
carries credentials only.

```dotenv
# --- Judge provider: Anthropic via Floodgate (the default) ---
LLM_PROVIDER=floodgate
FLOODGATE_USER_AGENT=nlq-judge/1.0     # mandatory; how spend is attributed
# Auth on a local Mac is the AppleConnect CLI — nothing to set. For CI use
# FLOODGATE_NARRATIVE_CERT + FLOODGATE_NARRATIVE_KEY instead.
# FLOODGATE_PROJECT_TOKEN=             # spend against a project, not your quota

# --- Live platform (Claris / Pulse) — QA only (HC-6) ---
PULSE_BASE_URL=https://api-qa.platform.claris.com
PULSE_AUTH_TOKEN=<JWT from Chrome DevTools; "Bearer " is added automatically>
PULSE_ORG_ID=<integer org id, e.g. 4104>
# PULSE_MODEL_NAME=claude-sonnet-5      # default; must be enabled for the org
# PULSE_FEATURES=TCOApiV2Feature        # default; sent as X-Claris-Features
# PULSE_STREAM=false                    # true → consume the SSE stream
# PULSE_CA_BUNDLE=/path/to/corp-ca.pem  # see §3a if TLS fails
```

Onboard once per model family at `https://genai.apple.com/profile/onboarding`;
a system account does not inherit a human's onboarding.

**Getting `PULSE_AUTH_TOKEN` + `PULSE_ORG_ID`:** open Pulse in Chrome → F12 →
Network → trigger a request to `api-qa.platform.claris.com` → Request Headers →
copy the `Authorization` value. The `org_id` is the number in the request URL
path (`/org/NNNN/…`).

⚠️ **The token is a short-lived JWT — it expires within the hour.** A run that
starts failing with `401 Token expired` just needs a fresh copy from DevTools.
A long run should either be split, or a longer-lived service token requested
from the platform team (open question with the client).

---

## 3a. Corporate network: TLS and proxies

**Every machine running this is company-managed. On such a machine there is
nothing to configure — no certificate to export, no CA path to set.** This
section explains why, and what to check on the rare occasion it does not work.

### TLS: nothing to set, it reads the OS trust store

The inspection proxy re-signs traffic with a private root CA. MDM installs that
CA into the **OS trust store**, but Python does not look there — it ships its own
bundle via `certifi`, which has never heard of your corp CA.

`truststore` bridges that gap. It is pinned in `requirements.txt`, and
`trust_os_ca_store()` runs before any HTTP client is built, for the platform
client, the token refresh chain and the judge gateway alike. Because MDM already
put the CA where the OS keeps them, **the certificate is picked up
automatically** — that is the whole mechanism.

So the setup on a new managed PC is just:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py check-auth --domain crm
```

If `check-auth` passes, TLS is working on both paths. Nothing else to do.

### If TLS does fail, check one thing first

```
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
self-signed certificate in certificate chain
```

On a managed machine this almost always means **`truststore` is not installed** —
a broken or partial `pip install`, not a configuration problem. The code now says
so itself rather than failing later as an opaque TLS error:

```
[judge] WARNING: `truststore` is not installed, so TLS will be verified
against certifi and will FAIL behind a corporate inspection proxy.
```

Fix it with `pip install -r requirements.txt`. Confirm with
`python -c "import truststore"`.

### Escape hatches — for unmanaged or odd machines only

Neither should be needed on this fleet. Documented because a future CI box or
contractor laptop may not be MDM-managed:

```dotenv
PULSE_CA_BUNDLE=/path/to/corp-ca.pem       # explicit CA, wins over the OS store
FLOODGATE_CA_BUNDLE=/path/to/corp-ca.pem   # set BOTH — they are independent
PULSE_VERIFY_TLS=false                     # diagnosis only, never leave set
FLOODGATE_VERIFY_TLS=false
```

Setting only one `CA_BUNDLE` produces the confusing half-broken state where
scoring works and the platform call fails, or the reverse.

### Proxies: inherited, deliberately not configured here

There is **no proxy setting in `judge/.env`**. Every client is built with httpx's
default `trust_env=True` and nothing overrides it, so all of them — including the
Anthropic SDK, which is handed our httpx client — pick up the machine's standard
proxy environment variables. Configure the proxy the way everything else on the
machine does, in the shell profile, not in this project.

Telling a proxy problem from a TLS one is easy: **speed.** A proxy refusal
arrives in about a millisecond because no network round-trip happened; a TLS
failure takes a full handshake.

```bash
curl -s -o /dev/null -w "http=%{http_code} time=%{time_total}s\n" \
  https://api-qa.platform.claris.com/
```

`http=000` at `time≈0.001s` means a local proxy refused the host. Three hosts
must all be reachable, and they are often governed by different rules:

| Host | Used by |
|---|---|
| `api-qa.platform.claris.com` | the platform client and token refresh |
| `cognito-idp.us-west-2.amazonaws.com` | token refresh, step 1 |
| `floodgate.g.apple.com` | the LLM judge |

A blocked host is an IT allowlist request, not a code change.

---

## 4. Run an evaluation against the live platform

### Smoke test (3 questions)

`judge` takes `--domain` and `--profile`, like `build-dataset` and `qa-build`.
The pair CSV, the `--dataset-version` tag and the `--pulse sql` table directory
all follow from those two and are echoed at the top of the run; pass the
matching flag to override any of them. The §9.3 contract file and the companion
are joined into the judge input automatically the first time a profile is
scored.

```bash
python main.py judge --domain crm --profile full \
  --judge llm \
  --pulse live \
  --limit 3 \
  --pulse-concurrency 3 \
  --allow-uncalibrated
```

### Full domain (PREVIEW)

```bash
python main.py judge --domain crm --profile full \
  --judge llm --pulse live \
  --pulse-concurrency 8 \
  --allow-uncalibrated
```

At ~27s per question measured against org 4104, 160 pairs at concurrency 4 takes
about 18 minutes — inside a one-hour token. The run refuses to start if the token
cannot cover the estimate; see §7.

### Official RELEASE run (once calibration + baseline are possible)

```bash
python main.py judge --domain crm --profile full \
  --judge llm --pulse live \
  --pulse-concurrency 8 \
  --release \
  --platform-version pulse-2026.09
```

`--dataset-version` is derived as `<dataset-version>+<qa-package>`, e.g.
`dataset-v1.0.0+qa-pairs-v0.3.0` — both move independently, so neither alone
identifies what was scored. `--platform-version` is the one tag the repository
cannot know; set `PLATFORM_VERSION` in `judge/.env` or `platform_version` in
`config/judge/<domain>.json` to stop passing it by hand.

`--release` needs: `--judge llm`, a calibration marker for the domain,
`--platform-version`, `--dataset-version`. First release for a
`platform_version` establishes the immutable baseline; later ones compare and
exit `4` if any domain trips the regression flag.

### Flags that matter

| Flag | When | Effect |
|---|---|---|
| `--pulse live` | real run (default) | HC-4 — submits through the platform API. The only source a `--release` run accepts. |
| `--pulse sql` | pair verification | Executes each pair's `reference_sql` in DuckDB (§14.2). Never scores the platform; PREVIEW-only. |
| `--allow-uncalibrated` | **required today** | the §10.2 gate blocks the LLM judge without a calibration marker; this overrides it. Scores are labelled `calibrated=false` and MUST NOT feed an official scorecard |
| `--numeric-form value` | default | OI-2, decided: numerics compare by **value**, so `$438,632.65` == `438632.65` while `4,182,000` still fails `4,182,650.00`. `--numeric-form string` restores byte-identical comparison if the Platform Owner rules that way |
| `--ignore-entity-labels` | diagnostic only | Compares numerics but not the entity/category labels beside them. Re-admits the false pass where every right number is bound to the wrong entity, so `--release` refuses it |
| `--pulse-concurrency N` | tuning | in-flight platform calls. Pulse answers take ~30 s each; N=8 turns a ~7 h serial run into <1 h. Stay within the API rate limit |
| `--concurrency N` | tuning | in-flight judge (LLM) calls |
| `--mode per_dimension` | optional | 4 judge prompts per item instead of 1 — removes the halo effect, 4× the cost |
| `--limit N` | dev | score only the first N pairs |

---

## 5. Reading the output

Each run writes two directories under `release/<domain>/`, sharing one
`run_id`. The §11 deliverables go to `scorecards/<UTC-timestamp>/` and the
provenance to `eval-runs/<UTC-timestamp>/` — see the repository README for the
tree and the `config/` keys that control both roots.

| File | What |
|---|---|
| `scorecard_summary.csv` | §11.1 — one row per domain + per-tier breakout. `exact_match_pct`, the 4 judge means + `judge_overall`, `judge_errors`, `platform_errors`, `baseline_exact_match_pct`, `delta_pct`, `regression_flag` |
| `question_results.csv` | §11.2 — one row per question. `expected_answer` / `actual_answer` / `exact_match_result` + **`exact_match_detail`** (which requirement went unmet), **`platform_generated_sql` for every question**, `null_handling` (§9.2 T3), per-dimension scores + rationale, `platform_error`, `rephrase_group_id` |
| `scorecard.md` / `scorecard.pdf` | human summary; PREVIEW / normalization / regression flags called out in the header |
| `results.json` | the full run — summary + every row, machine-readable |
| `prompts_log.jsonl` | one line per LLM call: `judge_run_id`, full prompt, full raw response, attempt, parse error, cache flag |
| `pulse_raw/<question_id>.json` | live runs — the untouched platform payload: `reasoning`, `timing`, `record_counts`, `extraction_request`, and **all** of `analysis_request`. ~72KB/question. Lets a run be re-scored offline instead of re-queried (a live question costs 50–170s) |
| `run_log.jsonl` | timestamped events: per-question request/response with latency and server-side timing, phase transitions, errors. Written as they happen, so a killed run still explains itself |
| `run_manifest.json` | git commit/branch/dirty, input CSV sha256, argv, python/platform — what produced this run |

The run directory is created **before** the first network call, so a run that
dies keeps everything it already fetched.

Two things worth reading first when a run looks wrong:

- `run_log.jsonl` — `pulse.response` carries `latency_s` and the platform's own
  `server_timing`; `record_counts` confirms which dataset it actually read
- the `reasoning` field inside a `pulse_raw` payload — the platform's account of
  how it interpreted the question, usually the fastest route to *why* an answer
  is wrong

Summary JSON keys to check:

- `exact_match.pass_pct` — the §14.2 accuracy number (over eligible questions)
- `exact_match.platform_error` — questions the platform never answered (infra, not accuracy). If this is more than a stray flake, **re-run**
- `judge.mean_per_dimension` — never averaged with exact-match
- `calibrated` — `false` until a calibration marker exists
- `exact_match.not_applicable` — pairs with no deterministic core. They sit **outside** `pass_pct`, so a rise here is not a rise in accuracy (§14.2 condition 4)
- `exact_match.expected_answer_off_contract` — pairs whose `expected_answer` is prose rather than the machine-generated shape (§9.3). Our defect, not the platform's
- `judge_temperature_enforced` / `judge_seed_enforced` — `false` if the model rejected temp 0, or if a configured seed never reached the provider
- `comparison_policy` — how exact-match compared answers this run; `comparison_is_default: false` means the run cannot establish a baseline
- `null_handling` — §9.2 T3 diagnostic: outer-join questions answered with inner joins only
- `rephrase_groups.platform_findings` — §9.5: variants of one question that returned different values
- `calibration.reason` — why the gate is open or closed, including a marker that was earned by a different judge

---

## 6. Known issues / open items

| Item | Status |
|---|---|
| **Calibration** | The CRM anchors are quarantined as `judge/anchors/crm.provisional.json` — they were graded against the rubric they are meant to validate, 2 of 10 SQLs don't run, and a judge that always answers `4` would pass 2 of 4 dimensions. Needs re-authoring against the confirmed dataset plus the §10.2 Platform Owner session (DEP-11). Until then every run needs `--allow-uncalibrated`. See `judge/anchors/README.md`. |
| **Dataset profile mismatch** | org 4104 is loaded with the **full** profile (144,000 support_cases / 24,000 accounts / 48,480 contacts), so `qa_pairs/dev/` expected answers are wrong against it by construction — the dev profile is 1% scale. Always run `qa_pairs/full/` against org 4104. Confirm with `record_counts` in any `pulse_raw` payload. |
| **OI-2** | **Decided by us, pending confirmation:** numerics compare by value, not rendering. `--numeric-form string` reverses it in one flag. The Platform Owner still needs to confirm the canonical numeric form. |
| **OI-3** | **Decided by us, pending confirmation:** dates compare by day with no tolerance, per material change C10. ISO or a month-name rendering is accepted on the platform side. |
| **OI-4** | Unaffected by the above and still open: how the platform reads an empty CSV field on ingestion. If empty is read as `''` rather than NULL, every T3 expected answer is wrong — no scorer change can compensate. |
| **temperature 0** | `gpt-5.6-sol` won't accept it. Running on seed-only determinism (`JUDGE_REQUIRE_TEMPERATURE_ZERO=false`). A temp-0-capable judge model would restore the full §10.1 guarantee. |
| **5-domain baseline** (§14.2) | Blocked on calibration + the other 4 domains' Q&A pairs. |
| **CI** | No `.github/workflows/` yet. |
| **`judge/` ↔ `judge_module/`** | Two implementations coexist; `judge/` will become a thin adapter over the package. Kept at parity on the three hardening changes that affect whether a score means what it claims — `seed_enforced`, the anchor-strength refusal, and binding a calibration pass to model + prompt + mode. The deterministic scorer stays in `judge/` only: the `label \| value` shape it parses is this project's pair contract, not a general one. |

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `SSL: CERTIFICATE_VERIFY_FAILED ... self-signed certificate in certificate chain` | Corp TLS-inspection proxy. `truststore` should handle it automatically; if not, set `PULSE_CA_BUNDLE=/path/to/corp-ca.pem`, or `PULSE_VERIFY_TLS=false` as a last resort |
| `Pulse 401: Unauthenticated: failed to parse token: EOF` | The token was sent without a scheme. The client adds `Bearer ` automatically now — if you still see this, the token in `.env` is empty or truncated |
| `Pulse 401: Unauthenticated: Token expired` | The browser JWT expired (< 1 h). Grab a fresh `Authorization` value from DevTools and update `judge/.env` |
| `REFUSING TO RUN: no calibration passed for domain='crm'` | The §10.2 gate. Add `--allow-uncalibrated` for a diagnostic run, or run calibration first |
| `model '...' rejected temperature=0; this run cannot certify deterministic judging` | Set `JUDGE_REQUIRE_TEMPERATURE_ZERO=false` in `judge/.env`, or point `OPENAI_MODEL` at a model that accepts temperature 0 |
| `REFUSING RELEASE RUN` | `--release` needs `--judge llm` + a calibration marker + `--platform-version` + `--dataset-version`. Drop `--release` for a PREVIEW run |
| Every question `exact_match: FAIL` but the judge scores are high | Usually OI-2 (comma formatting) or a dataset mismatch. Check `actual_answer` vs `expected_answer` in `question_results.csv` |
| Run takes hours | Raise `--pulse-concurrency` (each live answer is ~30 s). Watch the API rate limit |
| `⚠ N/M questions got no platform answer` | Transient Pulse failures. They're recorded as `ERROR` rows and excluded from accuracy. If N is more than a couple, re-run |

---

## 8. Verify the Pulse connection only

```bash
python - <<'EOF'
from judge.pulse_client import load_pulse_settings, PulseClient
s = load_pulse_settings()
print(s.redacted, s.chat_path_for())
c = PulseClient(s, [{"question_id": "v1",
                     "natural_language_question": "How many active accounts are there?",
                     "expected_answer": "", "reference_sql": ""}])
r = c.query("v1")
print("answer:", r.answer_text[:200])
print("sql   :", r.generated_sql)
c.close()
EOF
```

If the response fields don't match, `pulse_client.py` raises an error listing
the actual keys — send that and the `_extract_answer` / `_extract_sql` helpers
get pinned.
