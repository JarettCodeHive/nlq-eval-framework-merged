# Q&A Generators — Per-Domain Declarative Pattern

Each subfolder here is one domain's Track B implementation: `crm/`, `sales/`,
`finance/`, `logistics/`, `project_management/`. All five share the exact
same shape, proven first on `crm/` (the reference implementation — see
`../README.md` and `../docs/ARCHITECTURE.md`); this file documents what's
common across them instead of repeating five near-identical READMEs.

## The shared shape

Every domain folder contains:

| File | Role |
|---|---|
| `config.json` | domain name, question-id prefix, tier quotas, resolved open items |
| `catalog.json` | param name → SQL listing its allowed values (never hand-invented) |
| `families.json` | the question families — the declarative source of truth |
| `labels.json` | enum token → business phrase, applied to question text only |
| `generate_<domain>.py` | Step 1 — stage the generator's dataset output into a typed DuckDB + manifest |
| `validate_<domain>.py` | Step 2 — FK integrity / join-path / imperfection gate on the staged data |
| `scale_pairs[_<domain>].py` | Step 3 — the 160 pairs, templated from `families.json` + `catalog.json` |
| `rephrase.py` | Step 3b — 16 rephrase groups + map (§9.5 companion), every domain |
| `templates/t{1..5}/*.sql.j2` | one tiny SQL template per question family |

**CRM-only, not part of the shared pattern:** `author_qa_pairs.py` (32
hand-written seed fixtures, review-only, never released) and
`gen_family_matrix.py` (regenerates CRM's taxonomy matrix CSV). No other
domain has a seed-fixture script or a `taxonomy/`-style design-spec
document set — by design, not because the work is incomplete; see
`../README.md#other-domains`.

## Per-domain identity

| Domain | `id_prefix` | Question focus (see `generators/<domain>/README.md` for the dataset side) |
|---|---|---|
| `crm` | `CRM` | Account/contact engagement, campaign attribution, support-case SLA |
| `sales` | `SALES` | Pipeline deals, products, quote lines, quota attainment |
| `finance` | `FIN` | Accounting, FX-adjusted sums, budget vs. actual, rolling balances |
| `logistics` | `LOG` | Shipment routing, carrier performance, warehouse/inventory fulfillment |
| `project_management` | `PM` | Task/milestone status, resource allocation, logged-time breakdowns |

Every domain's tier split is identical (§9.1): T1 32 · T2 40 · T3 32 · T4 32
· T5 24 = 160 pairs, plus 16 rephrase groups (~35 rows after variants). A
question ID looks like `<id_prefix>-T<tier>-<family>-<seq>`, e.g.
`LOG-T3-09-31`, `PM-T4-04-16`.

## Adding a family (any domain)

One `families.json` entry plus one `.sql.j2` template — no Python change.
See `../docs/ARCHITECTURE.md` §8 "Extending it" (written against CRM, but
the mechanism — explicit per-family quota, `even_sample()` over the real
value catalog, `StrictUndefined` template rendering — is identical for all
five).

## Adding a sixth domain

`qa_pairs/utils/` is entirely domain-agnostic — nothing in it has ever
needed to change across the four domains added after CRM. Supply the same
nine items listed in "The shared shape" above, namespaced under
`generator/<new_domain>/` so nothing can collide with an existing domain's
files, and wire the four driver functions into `main.py`'s
`QA_STAGE`/`QA_VALIDATE`/`QA_GENERATE`/`QA_REPHRASE` dicts the same way the
existing five are. Enforce cross-domain question uniqueness with
`scripts/check_cross_domain_uniqueness.py` before release — it already
covers however many domains exist, no change needed there either.
