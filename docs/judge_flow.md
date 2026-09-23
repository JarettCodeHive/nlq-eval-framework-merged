# Judge Flow Diagrams

Post-integration architecture for the [`judge/`](../judge/) module.
Each diagram uses Mermaid — GitHub renders these natively.

## 1. End-to-end flow (integrated CLI)

```mermaid
flowchart LR
  subgraph invoke [Invocation]
    A1[python main.py judge]
    A2[python -m judge.cli]
  end
  A1 --> B
  A2 --> B

  B[judge.cli.run_from_args] --> C{--judge?}
  C -- heuristic --> D[HeuristicJudge<br/>test double — CI/dev]
  C -- llm  --> E[OpenAIJudge<br/>Azure / OpenAI]
  E --> G[Calibration gate<br/>§10.2]
  G -- uncalibrated & no override --> X[Refuse to run]

  B --> H{--pulse}
  H -- live --> I[PulseClient<br/>real platform API — HC-4]
  H -- sql --> I2[SQLPulse<br/>reference_sql in DuckDB — §14.2]
  I --> R[pulse_raw/ + run_log.jsonl]
  I --> J[platform_answer + generated_sql]
  I2 --> J

  D --> K
  E --> K[JudgeVerdict<br/>4 dims + rationale]
  J --> L[Exact-match check<br/>§HC-3 zero-tolerance]

  K --> M[Per-row result]
  L --> M
  M --> N[Aggregate summary]

  N --> O1[results.json  §11.2]
  N --> O2[scorecard_summary.csv  §11.1 CI]
  N --> O5[question_results.csv  §11.2 drill-down]
  N --> O3[scorecard.md  GitHub]
  N --> O4[scorecard.pdf §11.3 Phase-4 exit]
  N --> O6[baseline compare + regression_flag<br/>RELEASE runs only, §11.3]
```

## 2. Judge internal flow (one row → one verdict)

```mermaid
flowchart TD
  A[JudgeRequest<br/>question, expected_answer,<br/>judge_reference, platform_answer,<br/>generated_sql, domain] --> B[Prompt renderer]
  B --> C{--mode?}
  C -- combined --> D[1 prompt for all 4 dims]
  C -- per_dimension --> E[4 scoped prompts,<br/>no halo effect]

  D --> F[JudgeCache lookup<br/>sha256 over prompt + model]
  E --> F
  F -- hit  --> H[JudgeVerdict cached=true]
  F -- miss --> G[LLM call  Azure / OpenAI]
  G --> I[Parse JSON<br/>schema-validate<br/>1–5 range check]
  I --> J[JudgeVerdict cached=false]
  J --> K[Cache write]
  K --> H

  H --> L[prompts_log.jsonl<br/>full prompt+response audit]

  style A fill:#eef,stroke:#88a
  style H fill:#efe,stroke:#8a8
```

`reference_sql` is **deliberately not** passed to the judge (per §10.1:
"handing the judge the correct query invalidates the assessment"). SQL
plausibility is scored on the platform-generated SQL alone — the judge sees
the question and the generated SQL, then rates syntactic validity and
logical coherence without a reference to grade against.

## 3. Calibration gate (§10.2)

```mermaid
flowchart LR
  A[judge/anchors/&lt;domain&gt;.json] --> B[Judge runs on anchors]
  B --> C[Compare judge scores vs human anchor scores]
  C --> D{Agreement<br/>≥ threshold?}
  D -- No --> E[No calibration marker.<br/>LLM judge refuses to run<br/>against this domain.]
  D -- Yes --> F[Write judge/.calibration/&lt;domain&gt;.passed.json]
  F --> G[LLM judge cleared for scorecard use<br/>on this domain]

  H[--allow-uncalibrated flag] -.-> I[Bypass — smoke test only.<br/>Scores labelled calibrated=false.<br/>MUST NOT feed a release scorecard.]

  style E fill:#fee,stroke:#a88
  style G fill:#efe,stroke:#8a8
  style I fill:#ffe,stroke:#aa8
```

## 4. Where data comes from (current vs target)

```mermaid
flowchart TB
  subgraph now [Current — POC / dev]
    N1[CSV dataset<br/>--pulse-data] --> N2[SQLPulse]
    N3[judge/data/sample_pairs.json] --> N2
  end

  subgraph target [Target — post DEP-03 and qa_pairs]
    T1[generators/ → release/&lt;domain&gt;/&lt;version&gt;/*.csv] --> T2[qa_pairs verify SQL<br/>against exported CSVs]
    T2 --> T3[Q&amp;A pair set with<br/>question_id, expected_answer,<br/>judge_reference, reference_sql]
    T3 --> T4[PulseClient<br/>real Pulse API]
    T4 --> T5[platform_answer + generated_sql]
  end

  N2 -. superseded by .-> T4
  T3 -. feeds .-> J[judge/input_contract.py<br/>columns lock]

  style now fill:#f6f6f6,stroke:#999
  style target fill:#eef,stroke:#88a
```

**Current status of the two dependencies:**

- **Real Pulse API** ([`judge/pulse_client.py`](../judge/pulse_client.py) exists):
  blocked on DEP-03, which is blocked on Mac machine network access.
- **`qa_pairs/` module:** stub README in Jarett's repo; the target CSV
  columns (`question_id, domain, tier, nlq, expected_answer,
  judge_reference, reference_sql`) are defined in
  [`judge/input_contract.py`](../judge/input_contract.py).

## 5. Scorecard side-by-side reporting (§11.3)

```mermaid
flowchart LR
  A[Per-row results] --> B[aggregate<br/>group by domain, tier breakout]
  B --> C[exact_match pass %<br/>deterministic side]
  B --> D[per-dimension means<br/>judge side, 1–5]

  C --> E
  D --> E[scorecard rows<br/>domain roll-up + per tier]
  BL[baseline for platform_version<br/>immutable, write-once] --> E

  E --> F1[scorecard_summary.csv<br/>§11.1 CI-consumable]
  E --> F4[question_results.csv<br/>§11.2 drill-down]
  E --> F2[scorecard.md<br/>PR-renderable]
  E --> F3[scorecard.pdf<br/>stakeholder deliverable]
  E --> F5[regression_flag<br/>domain drop ≥ 5 pp — RELEASE only]

  note[§11.3 last line:<br/>exact-match and judge scores<br/>are reported side-by-side,<br/>NEVER combined into a composite.]
  note -.-> E

  style note fill:#ffe,stroke:#aa8
```

## Reading these diagrams alongside the code

| Diagram box | Source file |
|---|---|
| `run_from_args` | [`judge/cli.py`](../judge/cli.py) |
| `HeuristicJudge` / `OpenAIJudge` | [`judge/heuristic_judge.py`](../judge/heuristic_judge.py), [`judge/openai_judge.py`](../judge/openai_judge.py) |
| `SQLPulse` / `PulseClient` | [`judge/sql_pulse.py`](../judge/sql_pulse.py), [`judge/pulse_client.py`](../judge/pulse_client.py) |
| Calibration gate | [`judge/calibration.py`](../judge/calibration.py) |
| Prompt renderer | [`judge/prompts.py`](../judge/prompts.py), templates under [`judge/templates/judge/`](../judge/templates/judge/) |
| Parser + validation | [`judge/parsing.py`](../judge/parsing.py) |
| JudgeCache | [`judge/cache.py`](../judge/cache.py) |
| Scorecard writers | [`scorecard/summary.py`](../scorecard/summary.py), [`scorecard/report.py`](../scorecard/report.py) |
| Baseline + regression flag | [`scorecard/baseline.py`](../scorecard/baseline.py) |
| Exact-match | [`judge/exact_match.py`](../judge/exact_match.py) |
| Input contract | [`judge/input_contract.py`](../judge/input_contract.py) |
