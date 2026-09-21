# Finance Generator

The Finance generator produces a deterministic five-table golden dataset:

| Table | Role | Dev rows | Full base rows |
|---|---|---:|---:|
| `accounts` | Chart-of-accounts dimension with an acyclic parent hierarchy | 50 | 200 |
| `transactions` | Multi-currency transaction fact | 1,000 | 100,000 |
| `ledger_entries` | Double-entry fact and Transaction-to-Account bridge | 2,000 | 200,000 |
| `budgets` | Account-period budget reference | 100 | 800 |
| `fx_rates` | Synthetic dated source-currency-to-USD lookup | 370 | 3,650 |

Budget rows are base counts. Near-duplicate injection appends 1%, producing 101
dev rows and 808 full rows in the imperfect stage.

## Stage Modules

- `generator.py` creates clean accounts, rates, transactions, balanced ledger
  entries, and budgets from named random streams.
- `distributions.py` applies Pareto amounts, Poisson posting frequency,
  Gaussian-mixture dates, and bounded Decimal FX movement.
- `imperfections.py` adds near-duplicate budgets, transaction outliers,
  boundary dates, and NULL FX rates while preserving accounting integrity.
- `export.py` writes deterministic dev previews or full release CSVs.
- `validators/` contains config, immediate-stage, relational, DuckDB FK,
  join-path, imperfection-rate, row-cap, and reproducibility checks.
- `hashes.py`, `data_dictionary.py`, `schema_sql.py`, and `manifest.py` produce
  release evidence and support artifacts.

## Accounting and FX Contract

- Monetary values use `DECIMAL(19,4)` semantics; FX rates use
  `DECIMAL(19,6)` semantics.
- Decimal operations use `ROUND_HALF_UP`. Binary floating-point values are not
  accepted by the Finance decimal policy.
- Every posted transaction has balanced debit and credit totals equal to its
  source amount. Some transactions deliberately have no ledger rows for the
  required LEFT JOIN path.
- `ledger_entries` provides the explicit many-to-many path between transactions
  and accounts.
- Transactions join to rates analytically by source currency, target currency,
  and transaction date. This lookup is unique but is not a physical SQL FK.
- USD is the only reporting currency. `USD/USD` is always `1.000000`.
- Rates are synthetic, generated locally from seed `42`, and frozen in the
  released CSV. The generator never calls a live FX service.

For a populated rate, conversion is:

```text
converted_amount = ROUND_HALF_UP(total_amount * rate, 4)
```

A NULL rate means the matched transaction is intentionally unconvertible. It
must not be replaced with a current, previous, or default rate.

## Development Workflow

```bash
python main.py validate-config --domain finance --profile dev
python main.py generate-base --domain finance --profile dev --write-preview
python main.py apply-distributions --domain finance --profile dev --write-preview
python main.py apply-imperfections --domain finance --profile dev --write-preview
python main.py validate-relations --domain finance --profile dev
python main.py validate-fk --domain finance --profile dev --generated
python main.py validate-join-paths --domain finance --profile dev --generated
python main.py validate-imperfection-rates --domain finance --profile dev --generated
```

## Release Rule

Only the `full` profile can write `release/finance/dataset-v1.0.0/`. Generate
the CSVs and support artifacts, run all exported-data checks and clean-room
reproducibility, and generate `manifest.json` last. A manifest seals the
directory; issue a new dataset version for any correction.

See `Finance_Distributions_and_Imperfections_Matrix.md` for the exact columns
changed at each stage and `Currency_and_FX_Handling_Approach.md` for the
cross-domain currency boundary.
