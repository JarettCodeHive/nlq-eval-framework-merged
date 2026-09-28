# Logistics Generator Tests

These tests protect the complete Logistics schema, generation, validation,
release, and reproducibility contracts. Run only the Logistics tests with:

```bash
python -m pytest tests/generators/logistics -q
```

Coverage includes configuration and schema alignment, deterministic base data,
distributions, imperfections, relational and persisted validators, CSV export,
hashes, data dictionary, schema SQL, manifest, reproducibility comparisons,
summaries, and ordered dev/full pipelines.
