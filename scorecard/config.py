"""Scorecard output paths.

Resolved through `qa_pairs.utils.release_bundle`, the pipeline's canonical
locator for a release bundle — which also carries the fallback that still reads
the retired domain-first layout.

There used to be a `ScorecardConfig` here, fed by `config/scorecard/*.json`. Both
resolvers below took it as an argument and ignored it, and the path templates it
held were never interpolated, so editing that file changed nothing. A knob
connected to nothing is worse than no knob, so it is gone; see
config/scorecard/README.md.

The §11 deliverables live apart from the judge's run artifacts on purpose. A
scorecard is what goes to stakeholders and what §13.1 wants versioned in
`release/` under Git LFS; `pulse_raw/` is ~72KB per question of raw org data that
should not be tracked. Splitting the two means the deliverables can be committed
without dragging ~11MB of platform payloads per run along with them.
"""

from __future__ import annotations

from pathlib import Path

from qa_pairs.utils.release_bundle import component_dir
from qa_pairs.utils.release_bundle import release_root

MODULE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODULE_ROOT.parent


def report_output_dir(domain: str, run_id: str) -> Path:
    """Resolve where one run's §11 deliverables are written.

    Anchored at the repository root so a run launched from a subdirectory still
    lands in the same place. The `run_id` matches the judge's run directory, so
    a scorecard can always be traced back to the evidence that produced it.
    """

    return component_dir(domain, "scorecard") / run_id


def combined_report_dir(run_id: str) -> Path:
    """Where a multi-domain scorecard is written.

    Deliberately not under `release/<version>/<domain>/` — a scorecard covering
    five domains filed under one of them would misrepresent what it is, and the
    baseline it establishes is cross-domain by definition (§14.1).
    """

    return release_root(repo_root=REPO_ROOT) / "scorecard" / run_id
