"""Immutable accuracy baseline store — Execution Spec §11.3.

    Baseline: the first scorecard run establishes the accuracy baseline for all
    future comparisons. It is recorded immutably, not recalculated.

    Regression flag: any domain dropping 5 percentage points or more from
    baseline triggers an automated flag.

One JSON file per ``platform_version`` under ``scorecard/baselines/``. The file
is written exactly once and then made read-only; a second attempt to establish a
baseline for the same platform version raises :class:`BaselineExists` rather than
silently overwriting the reference point every later run is judged against.
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# §11.3 — a domain losing this many percentage points against baseline is a
# regression. Applied at domain level only; tier-level moves are diagnostic.
REGRESSION_DELTA_PP: float = -5.0

BASELINE_DIR: Path = Path(__file__).resolve().parent / "baselines"


class BaselineExists(RuntimeError):
    """A baseline for this platform version is already recorded and is immutable."""


def _slug(platform_version: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", platform_version.strip()).strip("-")
    if not s:
        raise ValueError("platform_version must not be empty")
    return s


def baseline_path(platform_version: str, *, baseline_dir: Path | None = None) -> Path:
    return (baseline_dir or BASELINE_DIR) / f"{_slug(platform_version)}.json"


def load_baseline(
    platform_version: str, *, baseline_dir: Path | None = None
) -> dict | None:
    """Return the recorded baseline document, or ``None`` if none exists yet."""
    path = baseline_path(platform_version, baseline_dir=baseline_dir)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def establish_baseline(
    platform_version: str,
    per_domain_exact_match_pct: dict[str, float],
    *,
    run_id: str,
    run_timestamp_iso: str,
    dataset_version: str,
    provisional: bool = False,
    baseline_dir: Path | None = None,
) -> Path:
    """Write the one-time baseline for ``platform_version``.

    Raises :class:`BaselineExists` if a baseline is already recorded — the
    baseline is a fixed reference point, never recalculated (§11.3).
    """
    if not per_domain_exact_match_pct:
        raise ValueError("cannot establish a baseline with no domain results")

    path = baseline_path(platform_version, baseline_dir=baseline_dir)
    if path.exists():
        raise BaselineExists(
            f"baseline for platform_version={platform_version!r} already exists at "
            f"{path} and is immutable (§11.3). Delete it deliberately if a reset is "
            "genuinely intended."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "platform_version": platform_version,
        "dataset_version": dataset_version,
        "established_run_id": run_id,
        "established_timestamp_iso": run_timestamp_iso,
        "recorded_at_iso": datetime.now(timezone.utc).isoformat(),
        "provisional": provisional,
        "domains": {
            domain: round(float(pct), 4)
            for domain, pct in sorted(per_domain_exact_match_pct.items())
        },
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    # Make it read-only so an accidental rewrite fails loudly.
    path.chmod(path.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    return path


@dataclass(frozen=True)
class RegressionComparison:
    """Per-domain comparison of a run against the established baseline."""

    domain: str
    current_exact_match_pct: float | None
    baseline_exact_match_pct: float | None
    delta_pct: float | None
    regression_flag: bool

    @property
    def has_baseline(self) -> bool:
        return self.baseline_exact_match_pct is not None


def compare_to_baseline(
    baseline: dict | None,
    per_domain_exact_match_pct: dict[str, float | None],
) -> dict[str, RegressionComparison]:
    """Compare this run's per-domain exact-match % against a baseline document.

    ``regression_flag`` is ``True`` only when both sides are present and the drop
    is at least :data:`REGRESSION_DELTA_PP` percentage points.
    """
    baseline_domains: dict[str, float] = (baseline or {}).get("domains", {})
    out: dict[str, RegressionComparison] = {}
    for domain, current in per_domain_exact_match_pct.items():
        base = baseline_domains.get(domain)
        delta = (
            round(current - base, 4)
            if current is not None and base is not None
            else None
        )
        out[domain] = RegressionComparison(
            domain=domain,
            current_exact_match_pct=current,
            baseline_exact_match_pct=base,
            delta_pct=delta,
            regression_flag=delta is not None and delta <= REGRESSION_DELTA_PP,
        )
    return out
