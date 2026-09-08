# Generators

Seeded synthetic data generation code.

Each domain should have its own generator module. CRM is built first and should
establish the shared conventions for the remaining domains.

Generator requirements:

- Use a single deterministic root seed.
- Avoid wall-clock reads, network calls, unordered iteration dependence, and
  nondeterministic parallelism.
- Generate all required controlled imperfections.
- Enforce the 250,000-row-per-table hard cap before export.
- Write CSVs with fixed column order and stable formatting.

Expected answers must be verified from exported CSVs, not from intermediate
dataframes created by these generators.
