# CRM Component Configuration

This directory contains the component-based CRM generation configuration.

The entry point remains `config/generation/crm.json`, which contains this
descriptor:

```json
{
  "config_schema_version": "1.0",
  "domain": "crm",
  "components": {
    "release": "crm/release.json",
    "schema": "crm/schema.json",
    "generation": "crm/generation.json",
    "validation": "crm/validation.json"
  }
}
```

Component ownership:

- `release.json`: dataset identity, fixed references, output and release rules,
  table order, and generation notes.
- `schema.json`: table contracts and relationships.
- `generation.json`: domain values, generation rules, business mappings,
  distributions, and imperfection targets.
- `validation.json`: join-path and consistency requirements.

The component order is part of the descriptor contract. All paths must be
relative to `config/generation`, remain inside this domain directory, use the
component name as the filename, and identify an existing JSON file. Additional
JSON files are rejected.

The shared loader validates this descriptor and assembles the four component
objects into the same runtime dictionary contract used before the split.

References using `config/generation/crm.json:<dotted.path>` always resolve
against the complete assembled CRM configuration. Component filenames are
storage details and must not be used in generation-rule references or release
metadata.

## Editing Rules

- Keep each top-level section in its documented owner file.
- Do not duplicate a section across components.
- Preserve array order unless an intentional dataset change is approved;
  ordered domain values can affect deterministic random selection.
- Update `base.json` only when a default should change for every inheriting
  domain. Put CRM-specific behavior in `generation.json`.

## Validation

From the repository root:

```bash
python main.py validate-config --profile dev
python main.py validate-config --profile full
python -m pytest tests/config/generation/test_crm_config_layout.py
python -m pytest tests/generators/crm/test_config_compatibility_baseline.py
```

Configuration-only reorganization must preserve the assembled-config baseline,
all generated CSV bytes and SHA-256 hashes, and the normalized release manifest
hash. Run `python -m scripts.verify_crm_config_migration` for the isolated
full-profile gate; it does not modify the configured release directory.
