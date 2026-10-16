# CRM Row Count Plan

> **Historical note:** written during initial CRM implementation planning (Track A). Any path below referencing the retired `release/crm/<version>/` domain-first layout is stale — the canonical layout is now `release/<version>/crm/...` (see `qa_pairs/utils/release_bundle.py` and the root `README.md`). Kept as the historical record of the original validation design, not a living reference; this pattern was not replicated for the other four domains.

Sources of truth:

- Shared cap: `config/generation/base.json`
- CRM targets: `config/generation/crm/schema.json`
- Current plan: `CRM_Engagement_Schema_Implementation_Plan.md`

CRM uses explicit row targets. The `dev` profile supports fast local checks;
the `full` profile supplies delivery-scale data. The global cap is `250000`
rows per table.

## Targets

| Table | Role | Dev | Full | Final full after imperfections |
|---|---|---:|---:|---:|
| `accounts` | Dimension | 100 | 24,000 | 24,000 |
| `contacts` | Dimension | 300 | 48,000 | 48,480 |
| `campaigns` | Dimension | 50 | 5,000 | 5,000 |
| `contact_campaigns` | Bridge | 750 | 180,000 | 180,000 |
| `interactions` | Fact | 1,000 | 200,000 | 200,000 |
| `support_cases` | Fact | 800 | 144,000 | 144,000 |

Only contacts gain rows: `duplicate_pct=1.0` appends 3 dev or 480 full
near-duplicates. Every final table remains below the cap.

## Validation

```bash
python main.py validate-row-caps --profile dev
python main.py validate-row-caps --profile dev --generated
python main.py validate-row-caps --profile full
python main.py validate-row-caps --profile full --generated
python main.py validate-row-caps --profile full --exported
```
