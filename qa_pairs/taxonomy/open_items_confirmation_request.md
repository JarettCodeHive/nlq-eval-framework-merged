# CRM Open Items — status

Source: `NLQ_Evaluation_Framework_Execution_Scope_updated.pdf`, Section 23.
Dataset in scope: `dataset-v1.0.0` — `accounts`, `contacts`, `campaigns`,
`contact_campaigns`, `interactions`, `support_cases`.
`reference_today` = `2026-08-01`.

## 1. Resolved (Platform Owner team, Slack, 2026-09-08)

| Item | Ruling | Who |
|---|---|---|
| **OI-1** T1 single-table tier | T1 stays a single-table control tier at the 20% / 32-pair quota; Section 8.2's minimum-join-depth rule is scoped to T2–T5. | Evan Uyehara |
| **OI-2** numeric answer format | Golden answers are stored as **plain numbers** — no `$`, no thousands separator, no unit noun. The evaluator's **Deterministic** mode extracts the first number from the platform response and compares it numerically (configurable tolerance). Multi-value answers are handled by the **LLM Judge (Binary)** (Gemini 2.5 Flash). | Aichi Lin |
| **OI-3** date-valued answers | Exact match, no tolerance. | Evan Uyehara |
| **OI-4** empty CSV field on ingestion | Ingested as **SQL NULL** in the platform DB — the T3 LEFT-JOIN "missing relationship" assumption is correct. | Aichi Lin |

### Evidence — replies as received

> **Evan Uyehara:** "OI-1 — include the single-table tier as written, 20%.
> The join-depth rule in 8.2 is for T2 through T5. OI-3 — dates are an exact
> string match, no tolerance window."

> **Aichi Lin:** "OI-4 — an empty field in the CSV is stored as null in the
> platform database, so your LEFT JOIN / IS NULL answers are right. OI-2 —
> don't format the golden answer. Store `3663`, not `3,663 devices`. Store
> `1772515.87`, not `$1,772,515.87`. The deterministic evaluator grabs the
> first number in the model's response and does a numeric compare. For
> anything that isn't a single number, the LLM judge does a semantic
> pass/fail against your reference answer."

> **Aichi Lin (evaluator types, follow-up):** "Two evaluators. Deterministic
> = first-number extraction + numeric compare, tolerance configurable — fast
> but lower accuracy because it depends on where the number lands in the
> response. LLM Judge (Binary) = Gemini 2.5 Flash, semantic, returns
> pass/fail. Use the judge for lists and multi-part answers."

*(Slack thread: Platform Owner channel, 2026-09-08. Export/permalink to be
attached to the milestone sign-off packet.)*

## 2. Still open

| # | Question | Owner | Blocks | Interim posture |
|---|---|---|---|---|
| a | Confirm numeric tolerance is set to **0** for our evaluation runs (spec change C2 mandates zero). | Platform Owner DRI | strict exact-match claim | `utils/scoring.py` compares at `Decimal("0")`; `config.json` `scoring.numeric_tolerance` is `"0"`. A non-zero platform tolerance would only *loosen* pass criteria. |
| b | Confirm the list/composite serialization the **LLM Judge** expects for the **24 `judge_plus_exact` (T5)** answers. | Aichi Lin | T5 judge scoring | POC serializes rows as `a \| b; c \| d`; every numeric component is enforced exactly by `utils/scoring.py`; the judge only scores the prose. `scorer_status=draft` until confirmed. |

Note: the T4 and T2-02/05/06 answers are now `table_exact` (deterministic
row/cell match), **not** judge-scored — they no longer depend on item b.

Neither blocks authoring — the pairs are generated and verified; only the
downstream scoring configuration is pending.

## 3. Interim handling in the POC

- All 160 pairs are generated to full quota and written to the configured
  profile output (`tmp/generated/crm/dev/qa_pairs` for dev and the versioned
  `release/crm/qa-pairs-v<qa_version>` package for full). The earlier held-file split for
  T1/T3 was removed once OI-1 and OI-4 were resolved.
- `generator/config.json` `resolved_open_items` records each ruling with
  attribution and date.
- The generated `crm_qa_pairs_companion.csv` carries `scoring_mode`
  (`scalar_exact` | `table_exact` | `judge_plus_exact`, one per tier per
  Section 9.2) plus `answer_schema`, `numeric_components`, and
  `scorer_status`, so the downstream evaluator scores each pair without
  re-deriving anything. `utils/scoring.py` is the reference implementation.
- Every T3 family is tagged in `crm_edge_case_coverage_map.csv` so the
  LEFT-JOIN/NULL set stays isolable if OI-4 is ever revisited.
