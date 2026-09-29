# Project Management Generation Configuration

`project_management.json` is the public descriptor. The shared loader assembles
its four components in this order:

1. `release.json` - version, fixed references, paths, table order, and release
   policy.
2. `schema.json` - row targets, fields, keys, and relationships.
3. `generation.json` - domain values, generation rules, semantic mappings,
   distributions, and imperfections.
4. `validation.json` - join paths and date, decimal, currency, and consistency
   rules.

The component files are the source for Project Management generator behavior.
Do not duplicate their enums, row targets, date windows, or business mappings
in Python. Development output belongs under
`tmp/generated/project_management/dev`; full output belongs under the immutable
versioned release path.
