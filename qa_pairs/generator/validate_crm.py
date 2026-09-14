"""
Step 2 of the CRM Q&A POC pipeline: DuckDB integrity checks on the
staged dataset. Mirrors the scope doc's referential-integrity gate
(Section 7.6) and the join-path preconditions Track B depends on.

    python generator/validate_crm.py --profile dev
    python generator/validate_crm.py --profile full

Checks:
  * every declared FK resolves (no orphan child rows)
  * every INNER JOIN path returns at least one row
  * every LEFT JOIN path leaves at least one unmatched parent row
  * the four controlled imperfections are present

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from utils.duckdb_io import connect_typed  # noqa: E402

TABLES = ["accounts", "campaigns", "contacts", "contact_campaigns", "interactions", "support_cases"]

FK_CHECKS = [
    ("contacts.account_id -> accounts", "contacts", "account_id", "accounts", "account_id"),
    (
        "contact_campaigns.contact_id -> contacts",
        "contact_campaigns",
        "contact_id",
        "contacts",
        "contact_id",
    ),
    (
        "contact_campaigns.campaign_id -> campaigns",
        "contact_campaigns",
        "campaign_id",
        "campaigns",
        "campaign_id",
    ),
    ("interactions.contact_id -> contacts", "interactions", "contact_id", "contacts", "contact_id"),
    ("interactions.account_id -> accounts", "interactions", "account_id", "accounts", "account_id"),
    (
        "interactions.campaign_id -> campaigns",
        "interactions",
        "campaign_id",
        "campaigns",
        "campaign_id",
    ),
    (
        "support_cases.account_id -> accounts",
        "support_cases",
        "account_id",
        "accounts",
        "account_id",
    ),
    (
        "support_cases.contact_id -> contacts",
        "support_cases",
        "contact_id",
        "contacts",
        "contact_id",
    ),
]

INNER_PATHS = {
    "contacts x contact_campaigns": "SELECT COUNT(*) FROM contacts c JOIN contact_campaigns cc ON c.contact_id = cc.contact_id",
    "campaigns x contact_campaigns": "SELECT COUNT(*) FROM campaigns ca JOIN contact_campaigns cc ON ca.campaign_id = cc.campaign_id",
    "accounts x support_cases": "SELECT COUNT(*) FROM accounts a JOIN support_cases s ON a.account_id = s.account_id",
    "contacts x interactions": "SELECT COUNT(*) FROM contacts c JOIN interactions i ON c.contact_id = i.contact_id",
    "campaigns x interactions": "SELECT COUNT(*) FROM campaigns ca JOIN interactions i ON ca.campaign_id = i.campaign_id",
}

LEFT_UNMATCHED = {
    "accounts without contacts": "SELECT COUNT(*) FROM accounts a LEFT JOIN contacts c ON a.account_id = c.account_id WHERE c.contact_id IS NULL",
    "contacts without interactions": "SELECT COUNT(*) FROM contacts c LEFT JOIN interactions i ON c.contact_id = i.contact_id WHERE i.interaction_id IS NULL",
    "contacts without support cases": "SELECT COUNT(*) FROM contacts c LEFT JOIN support_cases s ON c.contact_id = s.contact_id WHERE s.case_id IS NULL",
    "campaigns without interactions": "SELECT COUNT(*) FROM campaigns ca LEFT JOIN interactions i ON ca.campaign_id = i.campaign_id WHERE i.interaction_id IS NULL",
    "organic interactions (null campaign_id)": "SELECT COUNT(*) FROM interactions WHERE campaign_id IS NULL",
}

IMPERFECTIONS = {
    "near-duplicate contacts (same name+account, distinct id/email)": "SELECT COUNT(*) FROM (SELECT first_name, last_name, account_id FROM contacts "
    "GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)",
    "missing attribution_weight (~2.5%)": "SELECT COUNT(*) FROM contact_campaigns WHERE attribution_weight IS NULL",
    "engagement_points outliers (>=50)": "SELECT COUNT(*) FROM interactions WHERE engagement_points >= 50",
    "boundary-date support cases": "SELECT COUNT(*) FROM support_cases WHERE opened_at <= TIMESTAMP '1900-01-02' OR opened_at >= TIMESTAMP '2099-01-01'",
}


def connect(profile: str):
    return connect_typed(BASE / "dataset" / f"crm_{profile}.duckdb")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    profile = ap.parse_args().profile
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

    print(
        f"[{profile}] controlled imperfections (present at full scale; "
        f"exact-match proxies may read 0 on the 1%% dev sample)"
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

    manifest = BASE / "dataset" / f"manifest_{profile}.json"
    print(f"[{profile}] manifest: {json.loads(manifest.read_text())['dataset_version']}")

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
