# Sales Generator

The Sales generator produces a deterministic five-table golden dataset:

- `leads`
- `deals`
- `products`
- `quotations`
- `targets`

`quotations` is both a line-item fact table and the explicit many-to-many bridge
between deals and products. Quota attainment uses the analytical relationship
between `deals.rep_name`, `targets.rep_name`, and the target reporting period;
it is not a SQL foreign key.

## Module Layout

- `config.py` loads the assembled Sales component configuration.
- `generator.py` creates deterministic clean base tables.
- `distributions.py` applies Pareto amounts, Poisson quote-line allocation, and
  Gaussian-mixture dates.
- `imperfections.py` injects missing product prices, near-duplicate quotation
  lines, deal-amount outliers, and product boundary timestamps.
- `export.py` validates and writes deterministic dev previews or full release
  CSVs.
- `hashes.py`, `data_dictionary.py`, `schema_sql.py`, and `manifest.py` produce
  release artifacts.
- `summary.py` provides domain-specific root CLI summaries.
- `validators/` contains config, immediate-stage, relational, row-cap, DuckDB
  FK, join-path, imperfection-rate, and reproducibility validation.

## Business Rules

- Every deal belongs to a converted lead and keeps that lead's representative.
- Every quotation references a valid deal and product.
- The quotation bridge deliberately creates many-to-many Deal-to-Product
  cardinality.
- Leads without deals and target periods without won deals are retained for
  LEFT JOIN questions.
- Only won deals inside a target's start-inclusive, end-exclusive period count
  toward quota attainment.
- Sales monetary values are USD-only; foreign exchange belongs to Finance.
- Row targets are pre-imperfection counts, so quotation duplicates increase the
  final quotation row count deterministically.

## CLI

Run Sales commands from the repository root:

```bash
python main.py validate-config --domain sales --profile dev
python main.py generate-base --domain sales --profile dev
python main.py apply-distributions --domain sales --profile dev
python main.py apply-imperfections --domain sales --profile dev
```

The root `README.md` contains the complete dev and full release sequences.
