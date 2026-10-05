# Scorecard Config

**Report paths are no longer configured here.** They are resolved by
`qa_pairs/utils/release_bundle.py`, the pipeline's canonical locator for a
release bundle:

| Artefact | Location |
|---|---|
| per-domain scorecard | `release/<version>/<domain>/scorecard/<run_id>` |
| combined scorecard | `release/<version>/scorecard/<run_id>` |

A combined card is deliberately not filed under any one domain — it spans
several, and the baseline it establishes is cross-domain by definition (§14.1).

`default.json` previously carried `report_output_root` and
`combined_report_root`. Both resolvers accepted a config object and ignored it,
and neither template was ever interpolated, so editing them changed nothing.
They were removed rather than wired up: `release_bundle` already owns this, and
two sources of truth for one path is how a scorecard ends up somewhere nobody
looks.

To move these paths, change `release_bundle` — that moves the dataset, Q&A,
judge and scorecard components together, which is the point of a bundle.
