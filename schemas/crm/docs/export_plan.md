# CRM Export Plan

> **Historical note:** written during initial CRM implementation planning (Track A). Any path below referencing the retired `release/crm/<version>/` domain-first layout is stale — the canonical layout is now `release/<version>/crm/...` (see `qa_pairs/utils/release_bundle.py` and the root `README.md`). Kept as the historical record of the original validation design, not a living reference; this pattern was not replicated for the other four domains — see `DOCUMENTATION_IMPLEMENTATION_PLAN.md` §6.

Sources of truth:

- CSV format: `config/generation/base.json`
- Output path and table order: `config/generation/crm/release.json`
- Table and header order: `config/generation/crm/schema.json`
- DDL and headers: `schemas/crm/`

The full-profile export regenerates base data, distributions, and
imperfections, validates relational integrity, and writes:

```text
release/crm/dataset-v1.0.0/
  accounts.csv
  contacts.csv
  campaigns.csv
  contact_campaigns.csv
  interactions.csv
  support_cases.csv
```

Files use UTF-8 without BOM, LF endings, empty fields for NULL, lowercase
booleans, ISO dates/timestamps, and configured DDL column order. Release export
is refused for `dev` and after `manifest.json` seals the release.

## CLI

```bash
python main.py export-csvs --profile full
python main.py validate-row-caps --profile full --exported
python main.py validate-fk --profile full
python main.py validate-join-paths --profile full
python main.py validate-imperfection-rates --profile full
```
