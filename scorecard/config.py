"""Scorecard configuration — report output paths, per domain.

Mirrors `judge/config.py`: `config/scorecard/default.json` holds the defaults and
`config/scorecard/<domain>.json` carries only overrides, so a path can move
without a code change. §13.1 lists `config/scorecard/` for exactly this, and its
README names "report paths" as expected contents.

The §11 deliverables live apart from the judge's run artifacts on purpose. A
scorecard is what goes to stakeholders and what §13.1 wants versioned in
`release/` under Git LFS; `pulse_raw/` is ~72KB per question of raw org data that
should not be tracked. Splitting the two means the deliverables can be committed
without dragging ~11MB of platform payloads per run along with them.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

MODULE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = MODULE_ROOT.parent
CONFIGS_DIR = REPO_ROOT / "config" / "scorecard"
DEFAULT_DOMAIN_CONFIG = "default"


class ScorecardConfig(BaseModel):
    """Resolved scorecard settings for one domain."""

    # Where the §11 deliverables land. `{domain}` is interpolated; the path is
    # relative to the repository root.
    report_output_root: str = "release/{domain}/scorecards"


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_scorecard_config(
    domain: str, config_dir: Path | None = None
) -> ScorecardConfig:
    """Merge `default.json` with the `<domain>.json` override."""

    root = config_dir or CONFIGS_DIR
    base = _load_json(root / f"{DEFAULT_DOMAIN_CONFIG}.json")
    override = _load_json(root / f"{domain}.json")
    return ScorecardConfig(**{**base, **override})


def report_output_dir(
    domain: str, run_id: str, cfg: ScorecardConfig | None = None
) -> Path:
    """Resolve where one run's §11 deliverables are written.

    Anchored at the repository root so a run launched from a subdirectory still
    lands in the same place. The `run_id` matches the judge's run directory, so
    a scorecard can always be traced back to the evidence that produced it.
    """

    settings = cfg or load_scorecard_config(domain)
    return REPO_ROOT / settings.report_output_root.format(domain=domain) / run_id
