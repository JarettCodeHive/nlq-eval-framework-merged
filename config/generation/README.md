# Generation Config

Declarative settings for deterministic synthetic dataset generation.

Current files:

- `base.json`: shared seed, fixed date, profiles, row cap, CSV format, reusable
  distribution presets under `distribution_defaults`, imperfection percentages,
  and boundary dates. Presets are defaults, not mandatory cross-domain values.
- `crm.json`: stable CRM entry-point descriptor.
- `crm/release.json`: CRM identity, fixed references, paths, table order, and
  release rules.
- `crm/schema.json`: CRM table, field, row-target, key, and relationship
  contract.
- `crm/generation.json`: CRM domain values, generation rules, business
  mappings, distribution selections and targets, and imperfection targets.
- `crm/validation.json`: CRM join-path and consistency requirements.
- `sales.json`: stable Sales entry-point descriptor.
- `sales/release.json`: Sales identity, fixed references, paths, table order,
  release rules, and USD-only policy.
- `sales/schema.json`: Sales table, field, row-target, key, and physical or
  analytical relationship contract.
- `sales/generation.json`: Sales values, generation rules, business mappings,
  distribution selections and overrides, and imperfection targets.
- `sales/validation.json`: Sales join-path and consistency requirements.
- `finance.json`: stable Finance entry-point descriptor.
- `finance/release.json`: Finance identity, fixed references, paths, table
  order, release rules, and fixed-point precision metadata.
- `finance/schema.json`: Finance tables, fields, row targets, physical keys,
  hierarchy, and analytical FX lookup contract.
- `finance/generation.json`: Finance values, chart-of-accounts mappings,
  decimal policy, synthetic FX rules, distributions, and imperfections.
- `finance/validation.json`: Finance join paths, accounting rules, conversion
  rules, and cross-table consistency requirements.

Each domain distribution selects a shared preset by name. Any additional keys
in that domain setting override the selected preset. The framework validates
and merges both layers before generation, then exposes the result through
`GenerationSettings.distributions`.

For example, CRM currently selects:

| CRM setting | Shared preset | CRM overrides |
|---|---|---|
| `campaign_budget` | `pareto_amount` | None |
| `interaction_frequency` | `poisson_frequency` | None |
| `date_clustering` | `gaussian_mixture_dates` | None |

Sales currently selects:

| Sales setting | Shared preset | Sales overrides |
|---|---|---|
| `deal_amount` | `pareto_amount` | None |
| `quotation_frequency` | `poisson_frequency` | `lambda = 6.0` |
| `date_clustering` | `gaussian_mixture_dates` | None |
| `quota_amount` | `pareto_amount` | `alpha = 1.35`, USD 100,000–5,000,000 |

Finance currently selects:

| Finance setting | Shared preset | Finance overrides |
|---|---|---|
| `transaction_amount` | `pareto_amount` | `alpha = 1.16`, 10–10,000,000, scale 4 |
| `ledger_line_frequency` | `poisson_frequency` | `lambda = 3.2` |
| `date_clustering` | `gaussian_mixture_dates` | None |
| `budget_amount` | `pareto_amount` | `alpha = 1.35`, 10,000–25,000,000, scale 4 |

Finance also applies a domain-owned bounded Decimal movement algorithm to
`fx_rates.rate`. It is not a shared preset because its anchors, six-place
precision, identity-rate rule, and mean reversion are Finance semantics.

To tune one domain, add an algorithm-compatible parameter beside `preset` in
that domain's `generation.json`. Do not change `base.json` unless the reusable
default itself should change for every domain that inherits it.

The domain descriptor is the logical source and the component files own its
sections; `base.json` is the source for reusable defaults. The shared loader
assembles one runtime dictionary before Python validation checks structure, DDL
alignment, keys, relationships, references, and value shape. Generator modules
do not read individual component files or maintain a second hardcoded copy of
tables, enums, join paths, or effective distribution parameters.

The `dev` profile is for local validation. The `full` profile is the only
profile permitted to write a versioned release.
