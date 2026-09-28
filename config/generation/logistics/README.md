# Logistics Generation Configuration

`logistics.json` is the public descriptor. The shared loader assembles its four
components in this order:

1. `release.json` - version, fixed references, paths, table order, and release
   policy.
2. `schema.json` - row targets, ordered fields, keys, and relationships.
3. `generation.json` - domain values, generation rules, mappings,
   distributions, and controlled imperfections.
4. `validation.json` - physical and analytical relationships, orphan
   exclusions, join paths, and semantic validation rules.

These component files are the source for Logistics generator behavior. Do not
duplicate their enums, rates, row targets, date windows, orphan namespace, or
status mappings in Python. Development output belongs under
`tmp/generated/logistics/dev`; full output belongs under the immutable
versioned release path.
