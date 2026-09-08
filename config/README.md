# Config

Configuration files for dataset generation, schema behavior, Q&A verification,
judge scoring, and scorecard output.

Expected contents:

- Per-domain generation configs.
- Fixed seed and `reference_today` constants.
- Row-count profiles such as `dev` and `full`.
- Declared imperfection rates.
- Judge model settings, retry policy, concurrency limits, and output schema.

Do not store secrets here. Commit `.env.example` when needed, but never commit
real credentials.
