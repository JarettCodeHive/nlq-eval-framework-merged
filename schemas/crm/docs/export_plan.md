# CRM Export Plan

Sources of truth:

- CSV format: `config/generation/base.json`
- Output path and table order: `config/generation/crm.json`
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
