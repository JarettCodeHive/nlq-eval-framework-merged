# Finance Component Configuration

This directory owns Finance-specific dataset configuration. The entry point is
`config/generation/finance.json`; the shared domain loader composes these files
in descriptor order.

- `release.json` defines dataset identity, fixed references, output paths,
  release rules, table order, and fixed-point precision metadata.
- `schema.json` mirrors the signed Finance DDL and CSV-header contract,
  including row targets, physical keys, hierarchy, and the analytical FX
  lookup.
- `generation.json` owns Finance values, chart-of-accounts mappings, synthetic
  FX anchors and calendar, generation rules, distributions, decimal policy,
  and controlled imperfections.
- `validation.json` defines physical and analytical join paths, accounting
  balance rules, conversion checks, and cross-table consistency requirements.

Shared defaults such as seed `42`, the fixed reference date, CSV formatting,
row cap, imperfection rates, and reusable distribution algorithms remain in
`config/generation/base.json`.

## Editing Rules

- Keep each top-level section in its current component.
- Keep the signed table and field order aligned with the Finance DDL and CSV
  header specification.
- Use `Decimal` semantics, `ROUND_HALF_UP`, and the configured scale; do not
  generate monetary or FX values through binary floating-point arithmetic.
- Keep USD as the v1.0 reporting currency and treat all FX rates as frozen,
  synthetic test data rather than live market rates.
- Treat `ledger_entries` as the Transaction-to-Account bridge and preserve
  exact double-entry balance.
- Treat the Transaction-to-FX relationship as a unique analytical composite
  lookup, not as a physical foreign key.
- Row targets are pre-imperfection counts. The budget duplicate rate determines
  the final budget row count.

Finance-specific semantic validation is implemented in
`generators/finance/validators/config.py`. It validates both profiles together
before generation begins. Finance is registered with the root CLI; validate the
assembled config from the repository root with:

```bash
python main.py validate-config --domain finance --profile dev
python main.py show-config --domain finance --profile dev
```

The `dev` profile writes previews only under `tmp/generated/finance/dev/`. The
`full` profile targets `release/finance/dataset-v1.0.0/`. Generate the manifest
only after CSV export, exported-data validation, hashes, schema SQL, data
dictionary, and reproducibility checks; the manifest makes that release
immutable.
