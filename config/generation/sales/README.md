# Sales Component Configuration

This directory owns the Sales-specific dataset configuration. The entry point
is `config/generation/sales.json`; the shared loader composes its components in
descriptor order.

- `release.json` defines dataset identity, fixed references, output paths,
  release rules, table order, and the USD-only policy.
- `schema.json` mirrors the signed DDL/header contract, row targets, keys, and
  physical or analytical relationships.
- `generation.json` owns Sales enums, generation rules, shared distribution
  preset selections, business mappings, and imperfection targets.
- `validation.json` defines required join paths and cross-table consistency
  rules.

Shared defaults such as seed `42`, the fixed reference date, CSV formatting,
row cap, and distribution algorithms remain in `config/generation/base.json`.
Sales configuration may select or override those presets but must not redefine
domain-independent defaults.

## Editing Rules

- Keep each top-level section in its current component.
- Preserve table, field, enum, and join-path order unless a contract change is
  approved.
- Keep Sales USD-only; mixed-currency and FX behavior belong to Finance.
- Treat `quotations` as the Deal-to-Product junction and line-item fact table.
- Treat the deal-to-target relationship as analytical, not as a foreign key.
- Row targets are pre-imperfection counts. The configured quotation duplicate
  rate determines the final quotation count.

Sales-specific semantic validation is implemented under
`generators/sales/validators`. Validate either profile from the repository root:

```bash
python main.py validate-config --domain sales --profile dev
python main.py validate-config --domain sales --profile full
```
