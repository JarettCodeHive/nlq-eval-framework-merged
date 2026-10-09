# Pulse / Claris Integration — 5-minute brief

7 Oct 2026 · judge + scorecard · detail in [pulse_api_reference.md](pulse_api_reference.md)

Measured from the two full live runs of 28 Sep: CRM (179 questions) and Sales (104).

---

## At a glance

| | CRM | Sales |
|---|---|---|
| Questions | 179 | 104 |
| Wall clock | **2.9 h** | **24 min** |
| Platform time per question (median) | 161s | 46s |
| T1 → T5 slowdown | 4.9x | 1.7x |
| Rows extracted per question (median) | 257,480 | 20,400 |
| Needed a timeout retry | **29 (16%)** | 1 (1%) |

**Time is driven by how much data the platform extracts, not by how hard the
question is.** CRM is 3.5x slower per question because it extracts 12.6x more rows.

---

## 1. Request, auth and retries

One POST per question: `/api-proxy/org/4104/ai-svc/v2/tco/chat`, `stream=false`,
so one JSON body carries both the answer (`response.analysis`) and the SQL the
platform generated (`analysis_request`).

**The token Pulse accepts is not the Cognito token.** Cognito authenticates the
user; Claris mints its own `claris.com` token from it. So refresh is two steps —
Cognito `REFRESH_TOKEN_AUTH`, then `POST /auth/token` — and that second token is
the Bearer. It lives **one hour**, so any run over an hour needs the chain.

```mermaid
flowchart LR
    A["POST /tco/chat"] --> B{"outcome?"}
    B -->|"401 stale token"| C["re-mint, retry at once<br/>no backoff, free<br/>ONCE only"]
    C --> A
    B -->|"429 / 5xx / timeout"| D{"retries left?<br/>max 3"}
    D -->|"yes"| E["backoff 0.5s x 2^n<br/>capped 8s"]
    E --> A
    D -->|"no"| F["PlatformError<br/>isolated, run continues"]
    B -->|"ok"| G["answer + SQL"]
    style F fill:#7a2020,color:#fff
    style G fill:#1b4965,color:#fff
```

Three points worth making:

- **A 401 gets a free immediate re-mint** — waiting does not make a token
  fresher, and it must not spend the retry budget or a token expiring on the last
  attempt would lose an answer a refresh would have saved.
- **A 403 is deliberately not a 401.** Stale credential vs not allowed; refreshing
  a 403 just spends a second token on the same denial.
- **We refresh at 5 minutes remaining, not on expiry**, because a question takes
  161s and a token with 60s left dies mid-question. The 2.9-hour CRM run re-minted
  8 times with **zero 401s across 283 questions**.

---

## 2. Judge workflow

Two phases: a concurrent one that talks to the network, then a sequential one
that scores once every answer is in hand.

```mermaid
flowchart TD
    A["179 pairs"] --> B["PHASE 1 — concurrent, 4 in flight<br/>platform call + retries → LLM judge"]
    B --> C["PHASE 2 — sequential, every row<br/>exact_match → NULL check §9.2"]
    C --> D["rephrase-group agreement §9.5<br/>across all rows"]
    D --> E["results.json → scorecard"]
    style B fill:#5a2a5e,color:#fff
    style C fill:#1b4965,color:#fff
    style E fill:#2d3e50,color:#fff
```

**Two independent scorers, never blended into a composite (§11.3).**
`exact_match` asks "is the number right" — deterministic, zero numeric tolerance.
The LLM judge scores four dimensions 1–5 (factual correctness, completeness,
format adherence, SQL plausibility). They run at different times from different
inputs, so neither can see the other's verdict, and a disagreement is information.

How the prompt is built, in one line each:

- Templates on disk, never in code; `prompt_version()` is a **content hash** over
  them, so every score carries the exact prompt revision that produced it.
- **`reference_sql` is withheld.** `JudgeRequest` forbids it and a test asserts
  that — otherwise the judge could score SQL by diffing against the right query,
  and the answer leaks.
- The **anchoring map is explicit**: factual correctness against
  `expected_answer`, completeness and format against `judge_reference`, SQL
  plausibility against the platform's statements taken together. §10.1 does not
  say which anchors which, and a judge left to choose drifts between calls.
- The prompt carries **eight worked examples** of the HC-3 boundary, because
  stating "phrasing is not a deduction" as a rule is not enough — without
  examples judges over-penalise wording and corrupt every regression comparison.
- Invalid output is **never repaired**. A score of 7, `"5"` or `5.0` raises and
  the question fails. Clamping would invent a measurement.

---

## 3. Time per domain and tier

Platform self-reported, so our retries and network are excluded.

| Tier | CRM | Sales |
|---|---|---|
| T1 | 41s | 35s |
| T2 | 166s | 46s |
| T3 | 149s | 44s |
| T4 | 183s | 51s |
| T5 | **199s** | **60s** |
| all | **161s** | **46s** |

**Not monotonic** — T2 exceeds T3 in both domains, so "higher tier = slower" is
the wrong model. Tier correlates with time only because higher tiers touch more
tables. The platform's own breakdown says extraction is 68% of CRM's time (109s)
but only 23% of Sales' (11s), and within CRM, above-median row counts take 219s
against 123s.

```
estimate = (questions × median platform seconds) / concurrency 4

CRM    179 × 161 / 4 = 120 min ideal   vs 177 actual   ← the gap is retries
Sales  104 ×  46 / 4 =  20 min ideal   vs  24 actual
```

The judge adds no measurable wall clock — it is interleaved, and the platform is
the bottleneck. Finance, Logistics and PM have never been run, so their time is
not yet knowable; CRM and Sales differ 3.5x on identical tier structure.

---

## 4. Where the platform fails

### a. The same value resolves in most questions and fails in a few

The clearest finding, and it needs no reference to our data — it is the platform
contradicting itself inside one run (`20260928T093011Z`):

| Value | Reported **with a figure** | Returned zero / "does not exist" |
|---|---|---|
| `High` (priority) | 25 questions | 2 |
| `ServiceRequest` (category) | 37 questions | 1 |
| `EmailOpen` (interaction type) | 25 questions | 2 |
| `Reengagement` (campaign type) | 20 questions | 4 |
| `FinancialServices` (industry) | 13 questions | 4 |

Two answers from the same run, minutes apart:

> **"How many support cases fall under access?"** — answered correctly (24,053),
> and its own breakdown reports figures for `High` priority cases.

> **"How many support cases are logged at high priority?"** — *"a 'High'
> priority value has not been confirmed to exist."*

**Both cannot be true.** The question to bring: why does the same value resolve
in 25 questions and come back unconfirmed in 2?

**Shape 1 — returns zero.** Dangerous, because a zero looks like an answer:

| Question sent | Correct answer | Platform said |
|---|---|---|
| "How many interactions are email opens?" | 28,725 | "a total of 0 interactions recorded as email opens" |
| "What is the total budget of re-engagement campaigns?" | 23,620,415.61 | "no budget data was found" |
| "What percentage of support cases for accounts in financial services were resolved within their SLA deadline?" | 22.84 | "returned zero resolved support cases" |
| "How many leads came from the event booth source?" | 3,010 | "no leads were found" |

**Shape 2 — asks instead.** Better behaviour, same cause. The correct answers
are exactly the row counts it calls unconfirmed:

| Question sent | Correct answer | Platform said |
|---|---|---|
| "How many support cases are logged at high priority?" | **32,666** | "a 'High' priority value has not been confirmed to exist" |
| "How many support cases fall under service request?" | **23,815** | "there is no 'Service Request' category in the data" |
| "How many high-priority support cases were resolved within their SLA deadline?" | 7,550 | "'High' is not a confirmed value" |

Corroboration, though the finding does not rest on it: the platform's own
`record_counts` reported **144,000** `support_cases` rows on every question in
that run, the upload verified `rows_on_platform=144000`, and the uploaded CSV
holds `High` on 32,666 rows and `ServiceRequest` on 23,815.

### b. Answers an aggregate when a list was asked — 16 CRM, 13 Sales

> **"Right now, which accounts are carrying the biggest unresolved queue of access support cases?"**
> Correct answer: `Brown LLC | 5; Gibson Ltd | 5; Johnson Nichols | 5; …`
> Pulse: *"Across the organization there are 13,070 unresolved access support cases currently open."*

The question asks which accounts; the answer is an organisation-wide total. Every
case counted here has been checked individually against the question wording and
the reference SQL, so each one holds up if challenged.

### c. Intermittent hangs — 16% of CRM

29 of 179 exceeded the 420s client timeout while the platform never
self-reported over 288s, so the retry recovers them. Worth being precise: these
are a **latency** problem, not a correctness one.

> **"How many awareness campaigns have no attributed interactions?"**
> Pulse answered **378** — correct. Platform self-reported 249s; client observed
> **1,160s**, i.e. timeout then retry.


### d. Substance-level non-determinism

Three runs of the **identical 10-question Sales set** scored **7/10, 5/10, 8/10**.
Same questions, same data, same day. This bounds how precisely any single run can
be quoted.

---

## 5. Two asks

- **Log our retries.** The 16% figure is inferred from the latency gap, not
  proven, because retries are not currently recorded.
- **`.env.example` is stale** — documents `PULSE_TIMEOUT_S=180` and "~30s per
  question"; the code default is 420s and the measured median is 161s. Configuring
  from the example would push the retry rate well above 16%.

---

## 6. Internal — hold unless asked

Not for the client call. These are ours to close before any accuracy figure goes
out, and they are the reason a number should not go out yet.

- **6 T5 pairs need a question-wording fix.** The reference SQL carries `LIMIT 5`
  while the question asks which accounts qualify, so the platform's roll-up answer
  is reasonable and our expected answer is the narrow one. Excluded from §4b
  above rather than counted against the platform. Owner: Q&A.
- **CRM sits at 101/179** after the Q&A fixes already landed (the T2-08 SLA
  denominator) plus our precision-rendering change. Do not quote it externally
  until the 6 above are resolved — it will move, and a number that moves after
  being quoted is worse than no number.

