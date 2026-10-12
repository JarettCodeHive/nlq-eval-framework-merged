# Logistics Generator

The Logistics generator produces a deterministic five-table golden dataset:

- `shipments`
- `orders`
- `carriers`
- `warehouses`
- `inventory`

`orders.warehouse_id` is an **analytical LEFT JOIN key, not a physical FK** —
it deliberately carries both NULLs and orphaned references (see below), so
reference SQL must use `LEFT JOIN`/`NOT EXISTS`, never an `INNER JOIN` that
would silently drop the rows the domain exists to exercise.

## Module Layout

- `config.py` loads the assembled Logistics component configuration
  (`release.json`, `schema.json`, `generation.json`, `validation.json` —
  see `config/generation/logistics/README.md`).
- `generator.py` creates deterministic clean base tables.
- `distributions.py` applies the configured statistical distributions.
- `imperfections.py` injects the controlled orphan/NULL warehouse references,
  near-duplicate shipments, and boundary dates.
- `export.py` validates and writes deterministic dev previews or full release
  CSVs.
- `hashes.py`, `data_dictionary.py`, `schema_sql.py`, and `manifest.py`
  produce release artifacts.
- `pipeline.py` sequences the stages for `build-dataset`.
- `summary.py` provides domain-specific root CLI summaries.
- `validators/` contains config, relational, DuckDB FK, join-path,
  imperfection-rate, row-cap, and reproducibility validation.

## Business Rules — the full semantic contract lives in `schemas/logistics/logistics_semantics.md`

This is the canonical source; do not duplicate its definitions in Python.
Summary of what makes Logistics distinct from the other domains:

- **Orphaned orders are intentional, not a bug.** `2.5%` of base orders get a
  NULL `warehouse_id` (the standard controlled-NULL rate); a *separate*
  `1.0%` get a deliberately orphaned `warehouse_id` that matches no real
  warehouse — the declared-orphan FK case other domains don't model. Orphan
  IDs live in a disjoint, deterministic namespace (`900000000 + order_id`) so
  they can never collide with a real warehouse ID. Validators must report
  declared orphans separately from unexpected FK failures and from plain
  NULLs — collapsing the three would hide exactly the imperfection this
  domain is built to test.
- **Duplicate shipments are by design**, at a controlled `1.0%` rate. A
  near-duplicate keeps the same `order_id`/`carrier_id`/`tracking_number`
  business key under a new `shipment_id`, varies at least one of
  `ship_date`/`status`/`shipping_cost`, and is never a byte-identical copy.
  Reference SQL must distinguish physical rows (`COUNT(*)`) from business
  shipment identity (`COUNT(DISTINCT tracking_number)`) wherever the question
  depends on which one is being asked.
- All dates are grounded against the fixed `reference_today`
  (`config/generation/base.json`), never the wall clock.

## CLI

Run Logistics commands from the repository root:

```bash
python main.py validate-config --domain logistics --profile dev
python main.py build-dataset --domain logistics --profile dev
python main.py build-dataset --domain logistics --profile full --version v1.0.0
```

The root `README.md` contains the complete dev and full release sequences;
`DEVELOPMENT.md` documents each individual stage command for troubleshooting.

## Release Rule

Only the `full` profile writes a sealed release, under
`release/<version>/logistics/dataset/` (the canonical version-first layout —
see `qa_pairs/utils/release_bundle.py`). `generate-manifest` seals the
directory last; issue a new release version for any correction rather than
editing in place.
