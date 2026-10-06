"""
Step 2 of the Project Management Q&A pipeline: DuckDB integrity checks on the
staged dataset. Mirrors ``validate_sales.py``, but checks Project
Management's own declared join paths
(config/generation/project_management/validation.json) and imperfection
targets (config/generation/project_management/generation.json:imperfection_targets)
instead of Sales's.

    python generator/project_management/validate_project_management.py --profile dev
    python generator/project_management/validate_project_management.py --profile full

Checks:
  * every declared FK resolves (no orphan child rows)
  * every INNER JOIN path returns at least one row
  * every LEFT JOIN path leaves at least one unmatched parent row
  * the five controlled imperfections are present
  * every time_entries (task_id, resource_id) pair has a matching
    task_resources row (pm_jp_010's "complete_membership" requirement)

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE.parent))
from qa_pairs.utils.duckdb_io import connect_typed  # noqa: E402

FK_CHECKS = [
    ("tasks.project_id -> projects", "tasks", "project_id", "projects", "project_id"),
    (
        "task_resources.task_id -> tasks",
        "task_resources",
        "task_id",
        "tasks",
        "task_id",
    ),
    (
        "task_resources.resource_id -> resources",
        "task_resources",
        "resource_id",
        "resources",
        "resource_id",
    ),
    (
        "milestones.project_id -> projects",
        "milestones",
        "project_id",
        "projects",
        "project_id",
    ),
    ("time_entries.task_id -> tasks", "time_entries", "task_id", "tasks", "task_id"),
    (
        "time_entries.resource_id -> resources",
        "time_entries",
        "resource_id",
        "resources",
        "resource_id",
    ),
]

INNER_PATHS = {
    "tasks x projects (pm_jp_001)": (
        "SELECT COUNT(*) FROM tasks t JOIN projects p ON t.project_id = p.project_id"
    ),
    "task_resources x tasks (pm_jp_003)": (
        "SELECT COUNT(*) FROM task_resources tr JOIN tasks t ON tr.task_id = t.task_id"
    ),
    "task_resources x resources (pm_jp_004)": (
        "SELECT COUNT(*) FROM task_resources tr JOIN resources r ON tr.resource_id = r.resource_id"
    ),
    "tasks x task_resources x resources (pm_jp_005)": (
        "SELECT COUNT(*) FROM tasks t JOIN task_resources tr ON t.task_id = tr.task_id "
        "JOIN resources r ON tr.resource_id = r.resource_id"
    ),
    "milestones x projects (pm_jp_006)": (
        "SELECT COUNT(*) FROM milestones m JOIN projects p ON m.project_id = p.project_id"
    ),
    "time_entries x tasks (pm_jp_007)": (
        "SELECT COUNT(*) FROM time_entries te JOIN tasks t ON te.task_id = t.task_id"
    ),
    "time_entries x resources (pm_jp_008)": (
        "SELECT COUNT(*) FROM time_entries te JOIN resources r ON te.resource_id = r.resource_id"
    ),
    "projects x tasks x time_entries (pm_jp_009)": (
        "SELECT COUNT(*) FROM projects p JOIN tasks t ON p.project_id = t.project_id "
        "JOIN time_entries te ON t.task_id = te.task_id"
    ),
}

LEFT_UNMATCHED = {
    "projects without tasks (pm_jp_002)": (
        "SELECT COUNT(*) FROM projects p LEFT JOIN tasks t ON p.project_id = t.project_id "
        "WHERE t.task_id IS NULL"
    ),
}

IMPERFECTIONS = {
    "near-duplicate time entries": (
        "SELECT COUNT(*) FROM (SELECT task_id, resource_id, entry_date, work_type "
        "FROM time_entries GROUP BY 1, 2, 3, 4 HAVING COUNT(*) > 1)"
    ),
    "open-ended projects (missing end_date)": "SELECT COUNT(*) FROM projects WHERE end_date IS NULL",
    "open-ended tasks (missing due_date)": "SELECT COUNT(*) FROM tasks WHERE due_date IS NULL",
    "task-estimate outliers (>=500)": "SELECT COUNT(*) FROM tasks WHERE estimate_hours >= 500",
    "boundary-date tasks": (
        "SELECT COUNT(*) FROM tasks WHERE start_date IN "
        "('1900-01-01', '2038-01-19', '2099-12-31', '2026-08-01')"
    ),
}


def connect(profile: str):
    return connect_typed(BASE / "dataset" / f"project_management_{profile}.duckdb")


def validate(profile: str) -> None:
    """Validate the staged Project Management dataset used for Q&A generation."""

    con = connect(profile)
    failures = 0

    print(f"[{profile}] FK integrity")
    for label, ct, cc, pt, pc in FK_CHECKS:
        orphans = con.execute(
            f"SELECT COUNT(*) FROM {ct} x WHERE x.{cc} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {pt} p WHERE p.{pc} = x.{cc})"
        ).fetchone()[0]
        ok = orphans == 0
        failures += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {label}: {orphans} orphan(s)")

    print(f"[{profile}] INNER JOIN paths (must be non-empty)")
    for label, sql in INNER_PATHS.items():
        n = con.execute(sql).fetchone()[0]
        ok = n > 0
        failures += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {label}: {n:,} rows")

    print(f"[{profile}] LEFT JOIN paths (must have unmatched rows)")
    for label, sql in LEFT_UNMATCHED.items():
        n = con.execute(sql).fetchone()[0]
        ok = n > 0
        failures += not ok
        print(f"  {'ok ' if ok else 'FAIL'} {label}: {n:,} unmatched")

    print(f"[{profile}] time_entries -> task_resources membership (pm_jp_010)")
    total_te = con.execute("SELECT COUNT(*) FROM time_entries").fetchone()[0]
    matched_te = con.execute(
        "SELECT COUNT(*) FROM time_entries te JOIN task_resources tr "
        "ON te.task_id = tr.task_id AND te.resource_id = tr.resource_id"
    ).fetchone()[0]
    membership_ok = matched_te == total_te
    failures += not membership_ok
    print(
        f"  {'ok ' if membership_ok else 'FAIL'} every time_entries row has a task_resources "
        f"assignment: {matched_te:,} / {total_te:,}"
    )

    print(
        f"[{profile}] controlled imperfections (present at full scale; "
        f"exact-match proxies may read 0 on the dev sample)"
    )
    for label, sql in IMPERFECTIONS.items():
        n = con.execute(sql).fetchone()[0]
        if n > 0:
            mark = "ok  "
        elif profile == "dev":
            mark = "warn"  # dev sample is too small to guarantee every type
        else:
            mark = "FAIL"
            failures += 1
        print(f"  {mark} {label}: {n:,}")

    manifest = BASE / "dataset" / f"manifest_project_management_{profile}.json"
    print(
        f"[{profile}] manifest: {json.loads(manifest.read_text())['dataset_version']}"
    )

    print(
        f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}"
    )
    con.close()
    if failures:
        raise ValueError(f"{failures} Q&A dataset validation check(s) failed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    try:
        validate(ap.parse_args().profile)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
