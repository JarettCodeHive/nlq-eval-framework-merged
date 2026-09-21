# CRM Imperfection Plan

Sources of truth:

- Shared rates and boundaries: `config/generation/base.json`
- CRM target declarations: `config/generation/crm/generation.json`
- Canonical DDL: `schemas/crm/crm_ddl.sql`

Imperfections are injected after distributions and remain deterministic.

## Controlled Targets

| Type | Rate/value | Target | Rule |
|---|---:|---|---|
| Near-duplicates | `1.0%` | `contacts.first_name`, `contacts.email` | Append typo-based variations with new contact IDs. |
| Missing values | `2.5%` | `contact_campaigns.attribution_weight` | Write empty CSV fields without changing bridge keys. |
| Outliers | `0.5%` | `interactions.engagement_points` | Replace selected values with integers from 50 through 100. |
| Boundary timestamps | Four fixed dates | `support_cases.opened_at` | Set midnight openings and rederive SLA, creation, and applicable resolution timestamps. |

The boundary dates are `1900-01-01`, `2038-01-19`, `2099-12-31`, and
`2026-08-01`. Boundary opening and SLA fields are not NULL targets.

Nullable business states such as campaign end dates, organic interaction
campaign IDs, optional direct account links, optional case contacts, and
unresolved-case timestamps are not counted as injected NULL imperfections.

## CLI

```bash
python main.py apply-imperfections --profile dev
python main.py apply-imperfections --profile dev --write-preview
```

Preview CSVs are written to `tmp/generated/crm/dev/imperfect/`.
