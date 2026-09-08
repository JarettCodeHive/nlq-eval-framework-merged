"""
Scales T1 and T2 CRM Q&A pairs to their full quota (32 and 40) using the
per-family Jinja2 SQL templates in templates/t1/*.sql.j2 and
templates/t2/*.sql.j2 (one small template file per question family).

Pipeline (scope doc Section 9.6):
  1. Extract valid dataset values from the real CSVs (never invented).
  2. Render each family template once per real value combination.
  3. Execute the rendered SQL in DuckDB against the exported CSVs.
  4. expected_answer = the query result (never hand-typed).
  5. Block any pair with a SQL error, unintended zero-row result, or
     NULL scalar where a number is expected.
  6. Write a verification log per question.
  7. Attach business-vocabulary question text + judge_reference +
     derivation_rationale per rendered instance.

T1 -> crm_t1_pairs_HELD.csv  (held pending OI-1, per Risk R-10)
T2 -> crm_t2_pairs.csv       (clear to ship - no open-item dependency)

    python generator/scale_pairs.py --profile dev
    python generator/scale_pairs.py --profile full
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import duckdb
from jinja2 import Environment, FileSystemLoader

BASE = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE / "generator" / "templates"

FIELDS = [
    "question_id", "tier", "natural_language_question", "expected_answer",
    "reference_sql", "reference_tables", "reference_fields",
    "judge_reference", "derivation_rationale",
]

TABLES = ["accounts", "campaigns", "contacts", "contact_campaigns",
          "interactions", "support_cases"]

env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                  trim_blocks=True, lstrip_blocks=True)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def connect(profile: str):
    data_dir = BASE / "dataset" / profile
    if not data_dir.is_dir():
        raise SystemExit(f"run generate_crm.py --profile {profile} first")
    con = duckdb.connect()
    for tbl in TABLES:
        path = (data_dir / f"{tbl}.csv").as_posix()
        con.execute(
            f"CREATE TABLE {tbl} AS SELECT * FROM read_csv_auto('{path}', header=True)")
    return con


def value_catalog(con) -> dict:
    def distinct(sql):
        return [r[0] for r in con.execute(sql).fetchall() if r[0] is not None]
    return {
        "campaign_type": distinct(
            "SELECT DISTINCT campaign_type FROM campaigns ORDER BY 1"),
        "campaign_status": distinct(
            "SELECT DISTINCT status FROM campaigns ORDER BY 1"),
        "priority": distinct(
            "SELECT DISTINCT priority FROM support_cases ORDER BY 1"),
        "category": distinct(
            "SELECT DISTINCT category FROM support_cases ORDER BY 1"),
        "engagement_type": distinct(
            "SELECT DISTINCT engagement_type FROM interactions ORDER BY 1"),
        "region": distinct(
            "SELECT DISTINCT region FROM accounts WHERE region IS NOT NULL ORDER BY 1"),
        "industry": distinct(
            "SELECT DISTINCT industry FROM accounts WHERE industry IS NOT NULL ORDER BY 1"),
        "title": distinct(
            "SELECT DISTINCT title FROM contacts WHERE title IS NOT NULL ORDER BY 1"),
        "channel": distinct(
            "SELECT DISTINCT channel FROM interactions ORDER BY 1"),
        "customer_tier": distinct(
            "SELECT DISTINCT customer_tier FROM accounts WHERE customer_tier IS NOT NULL ORDER BY 1"),
        "is_active": ["true", "false"],
    }


def run_sql(con, log_dir: Path, dataset_version: str,
            question_id: str, tier: str, sql: str):
    t0 = time.time()
    status, error, rows, cols = "ok", None, [], []
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - report any DuckDB failure
        status, error = "error", str(exc)
    elapsed = time.time() - t0

    expected_answer = ""
    if status == "ok":
        if len(rows) == 0:
            status = "blocked_zero_rows"
        elif len(rows) == 1 and len(cols) == 1:
            v = rows[0][0]
            if v is None:
                status = "blocked_null_scalar"
            else:
                expected_answer = str(v)
        elif len(rows) == 1:
            expected_answer = " | ".join("" if v is None else str(v) for v in rows[0])
        else:
            expected_answer = "; ".join(
                " | ".join("" if v is None else str(v) for v in row) for row in rows)

    log = {
        "question_id": question_id, "domain": "crm", "tier": tier,
        "qa_version": "0.1.0-dev", "dataset_version": dataset_version,
        "reference_sql": sql, "execution_status": status, "error": error,
        "row_count_returned": len(rows),
        "execution_time_seconds": round(elapsed, 4),
        "result_hash": sha256_text(repr(rows)),
        "generated_expected_answer": expected_answer,
        "verification_timestamp": "2026-08-01T00:00:00",
    }
    (log_dir / f"{question_id}.json").write_text(
        json.dumps(log, indent=2), encoding="utf-8")
    return status, expected_answer


# ---------------------------------------------------------------------
# T1 - single-table aggregation. 6 families -> 32 pairs.
# ---------------------------------------------------------------------
def t1_specs(v: dict) -> list[dict]:
    return [
        dict(family="T1-01", block="t1/T1-01.sql.j2", param="campaign_type",
             values=v["campaign_type"],
             q="What is the total budget of {campaign_type} campaigns?",
             tables="campaigns", fields="campaigns.campaign_type, campaigns.budget_amount",
             judge="The answer should state a single total budget figure for campaigns of the named type.",
             rationale="Filter campaigns to the given campaign_type, sum budget_amount, round to 2 decimal places. NULL budgets do not contribute."),
        dict(family="T1-02", block="t1/T1-02.sql.j2", param="campaign_status",
             values=v["campaign_status"],
             q="How many campaigns are in the {campaign_status} status?",
             tables="campaigns", fields="campaigns.status",
             judge="The answer should state a single count of campaigns in the named status.",
             rationale="Filter campaigns to the given status and count rows."),
        dict(family="T1-03", block="t1/T1-03.sql.j2", param="priority",
             values=v["priority"],
             q="How many support cases are logged as {priority} priority?",
             tables="support_cases", fields="support_cases.priority",
             judge="The answer should state a single count of support cases at the named priority.",
             rationale="Filter support_cases to the given priority and count rows."),
        dict(family="T1-04", block="t1/T1-04.sql.j2", param="category",
             values=v["category"],
             q="How many support cases fall under the {category} category?",
             tables="support_cases", fields="support_cases.category",
             judge="The answer should state a single count of support cases in the named category.",
             rationale="Filter support_cases to the given category and count rows."),
        dict(family="T1-05", block="t1/T1-05.sql.j2", param="engagement_type",
             values=v["engagement_type"],
             q="How many interactions are of type {engagement_type}?",
             tables="interactions", fields="interactions.engagement_type",
             judge="The answer should state a single count of interactions of the named engagement type.",
             rationale="Filter interactions to the given engagement_type and count rows."),
        dict(family="T1-06", block="t1/T1-06.sql.j2", param="region",
             values=v["region"],
             q="How many accounts do we have in the {region} region?",
             tables="accounts", fields="accounts.region",
             judge="The answer should state a single count of accounts in the named region.",
             rationale="Filter accounts to the given region and count rows."),
    ]


# ---------------------------------------------------------------------
# T2 - two-table INNER JOIN. 7 families -> 40 pairs.
# ---------------------------------------------------------------------
def t2_specs(v: dict) -> list[dict]:
    return [
        dict(family="T2-01", block="t2/T2-01.sql.j2", param="region",
             values=v["region"],
             q="How many support cases belong to {region}-region accounts?",
             tables="support_cases, accounts",
             fields="support_cases.account_id, support_cases.case_id, accounts.account_id, accounts.region",
             judge="The answer should state a single count of support cases whose owning account is in the named region.",
             rationale="INNER JOIN support_cases to accounts on account_id, filter to the given region, count case rows."),
        dict(family="T2-02", block="t2/T2-02.sql.j2", param="priority",
             values=v["priority"],
             q="Which customer tier has the most {priority}-priority support cases?",
             tables="support_cases, accounts",
             fields="support_cases.account_id, support_cases.priority, support_cases.case_id, accounts.account_id, accounts.customer_tier",
             judge="The answer should name a single customer tier and its count of support cases at the named priority.",
             rationale="INNER JOIN support_cases to accounts on account_id, filter to the given priority, count cases grouped by customer_tier, sort descending with customer_tier as tie-break, take the top row."),
        dict(family="T2-03", block="t2/T2-03.sql.j2", param="industry",
             values=v["industry"],
             q="How many contacts do we have across accounts in the {industry} industry?",
             tables="contacts, accounts",
             fields="contacts.account_id, contacts.contact_id, accounts.account_id, accounts.industry",
             judge="The answer should state a single count of contacts belonging to accounts in the named industry.",
             rationale="INNER JOIN contacts to accounts on account_id, filter to the given industry, count contact rows. Unassigned contacts (NULL account_id) are excluded by the INNER JOIN."),
        dict(family="T2-04", block="t2/T2-04.sql.j2", param="campaign_type",
             values=v["campaign_type"],
             q="How many interactions are attributed to {campaign_type} campaigns?",
             tables="interactions, campaigns",
             fields="interactions.campaign_id, interactions.interaction_id, campaigns.campaign_id, campaigns.campaign_type",
             judge="The answer should state a single count of interactions attributed to campaigns of the named type.",
             rationale="INNER JOIN interactions to campaigns on campaign_id, filter to the given campaign_type, count interaction rows. Organic interactions (NULL campaign_id) are excluded by the INNER JOIN."),
        dict(family="T2-05", block="t2/T2-05.sql.j2", param="is_active",
             values=v["is_active"],
             q="What is the inbound/outbound interaction breakdown for {status_word} contacts?",
             q_ctx=lambda val: {"status_word": "active" if val == "true" else "inactive"},
             tables="interactions, contacts",
             fields="interactions.contact_id, interactions.direction, contacts.contact_id, contacts.is_active",
             judge="The answer should break interactions down by direction (Inbound/Outbound) with counts, restricted to contacts matching the active/inactive flag.",
             rationale="INNER JOIN interactions to contacts on contact_id, filter to the given is_active value, group by direction and count, sort descending with direction as tie-break."),
        dict(family="T2-06", block="t2/T2-06.sql.j2", param="engagement_type",
             values=v["engagement_type"],
             q="Which accounts had the most {engagement_type} interactions recorded directly against them?",
             tables="accounts, interactions",
             fields="accounts.account_id, accounts.account_name, interactions.account_id, interactions.engagement_type, interactions.interaction_id",
             judge="The answer should list accounts with their count of the named engagement type, ranked highest to lowest, using the direct account-to-interaction relationship.",
             rationale="INNER JOIN accounts to interactions on account_id, filter to the given engagement_type, count interactions grouped by account, sort descending with account_name as tie-break, show top 5."),
        dict(family="T2-07", block="t2/T2-07.sql.j2",
             params=[("channel", v["channel"]), ("title", v["title"])],
             q="How many {channel} interactions came from contacts with the title {title}?",
             tables="interactions, contacts",
             fields="interactions.contact_id, interactions.channel, interactions.interaction_id, contacts.contact_id, contacts.title",
             judge="The answer should state a single count of interactions on the named channel tied to contacts holding the named title.",
             rationale="INNER JOIN interactions to contacts on contact_id, filter to the given channel and title, count interaction rows."),
    ]


# ---------------------------------------------------------------------
# T3 - two-table LEFT OUTER JOIN + NULL handling. 6 families -> 32.
# HELD pending OI-4 (empty CSV field -> NULL vs empty string).
# ---------------------------------------------------------------------
def t3_specs(v: dict) -> list[dict]:
    return [
        dict(family="T3-01", block="t3/T3-01.sql.j2", param="region", values=v["region"],
             q="How many {region}-region accounts have no contacts on record?",
             tables="accounts, contacts",
             fields="accounts.account_id, accounts.region, contacts.account_id, contacts.contact_id",
             judge="The answer should state a single count of accounts in the named region with zero associated contacts.",
             rationale="LEFT JOIN accounts to contacts on account_id, filter accounts to the given region and keep only rows where contact_id IS NULL, count them."),
        dict(family="T3-02", block="t3/T3-02.sql.j2", param="industry", values=v["industry"],
             q="How many accounts in the {industry} industry have no contacts on record?",
             tables="accounts, contacts",
             fields="accounts.account_id, accounts.industry, contacts.account_id, contacts.contact_id",
             judge="The answer should state a single count of accounts in the named industry with zero associated contacts.",
             rationale="LEFT JOIN accounts to contacts on account_id, filter accounts to the given industry and keep only rows where contact_id IS NULL, count them."),
        dict(family="T3-03", block="t3/T3-03.sql.j2", param="title", values=v["title"],
             q="How many contacts with the title {title} have no recorded interactions?",
             tables="contacts, interactions",
             fields="contacts.contact_id, contacts.title, interactions.contact_id, interactions.interaction_id",
             judge="The answer should state a single count of contacts holding the named title that have zero interactions.",
             rationale="LEFT JOIN contacts to interactions on contact_id, filter contacts to the given title and keep only rows where interaction_id IS NULL, count them."),
        dict(family="T3-04", block="t3/T3-04.sql.j2", param="title", values=v["title"],
             q="How many contacts with the title {title} are not enrolled in any campaign?",
             tables="contacts, contact_campaigns",
             fields="contacts.contact_id, contacts.title, contact_campaigns.contact_id, contact_campaigns.campaign_id",
             judge="The answer should state a single count of contacts holding the named title with no campaign membership row.",
             rationale="LEFT JOIN contacts to contact_campaigns on contact_id, filter contacts to the given title and keep only rows where campaign_id IS NULL, count them."),
        dict(family="T3-05", block="t3/T3-05.sql.j2", param="campaign_type", values=v["campaign_type"],
             q="How many {campaign_type} campaigns have no attributed interactions?",
             tables="campaigns, interactions",
             fields="campaigns.campaign_id, campaigns.campaign_type, interactions.campaign_id, interactions.interaction_id",
             judge="The answer should state a single count of campaigns of the named type with zero attributed interactions.",
             rationale="LEFT JOIN campaigns to interactions on campaign_id, filter campaigns to the given campaign_type and keep only rows where interaction_id IS NULL, count them."),
        dict(family="T3-06", block="t3/T3-06.sql.j2", param="campaign_status", values=v["campaign_status"],
             q="How many campaigns in the {campaign_status} status have no attributed interactions?",
             tables="campaigns, interactions",
             fields="campaigns.campaign_id, campaigns.status, interactions.campaign_id, interactions.interaction_id",
             judge="The answer should state a single count of campaigns in the named status with zero attributed interactions.",
             rationale="LEFT JOIN campaigns to interactions on campaign_id, filter campaigns to the given status and keep only rows where interaction_id IS NULL, count them."),
    ]


# ---------------------------------------------------------------------
# T4 - three-table JOIN + aggregation. 4 families -> 32.
# ---------------------------------------------------------------------
def t4_specs(v: dict) -> list[dict]:
    return [
        dict(family="T4-01", block="t4/T4-01.sql.j2", param="region", values=v["region"],
             q="What is the total engagement-point score generated by contacts at {region}-region accounts?",
             tables="accounts, contacts, interactions",
             fields="accounts.account_id, accounts.region, contacts.account_id, contacts.contact_id, interactions.contact_id, interactions.engagement_points",
             judge="The answer should state a single total of engagement_points across all interactions belonging to contacts whose account is in the named region.",
             rationale="INNER JOIN accounts -> contacts -> interactions via account_id then contact_id, filter to the given region, sum engagement_points as delivered (outliers included)."),
        dict(family="T4-02", block="t4/T4-02.sql.j2", param="industry", values=v["industry"],
             q="What is the total engagement-point score generated by contacts at {industry}-industry accounts?",
             tables="accounts, contacts, interactions",
             fields="accounts.account_id, accounts.industry, contacts.account_id, contacts.contact_id, interactions.contact_id, interactions.engagement_points",
             judge="The answer should state a single total of engagement_points across all interactions belonging to contacts whose account is in the named industry.",
             rationale="INNER JOIN accounts -> contacts -> interactions via account_id then contact_id, filter to the given industry, sum engagement_points as delivered (outliers included)."),
        dict(family="T4-03", block="t4/T4-03.sql.j2", param="industry", values=v["industry"],
             q="How many support cases were raised by contacts at {industry}-industry accounts?",
             tables="accounts, contacts, support_cases",
             fields="accounts.account_id, accounts.industry, contacts.account_id, contacts.contact_id, support_cases.contact_id, support_cases.case_id",
             judge="The answer should state a single count of support cases whose requesting contact belongs to an account in the named industry.",
             rationale="INNER JOIN accounts -> contacts -> support_cases via account_id then contact_id, filter to the given industry, count case rows. Account-level cases (NULL contact_id) are excluded by the INNER JOIN."),
        dict(family="T4-04", block="t4/T4-04.sql.j2",
             params=[("engagement_type", v["engagement_type"]), ("customer_tier", v["customer_tier"])],
             q="How many {engagement_type} interactions came from contacts at {customer_tier}-tier accounts?",
             tables="interactions, contacts, accounts",
             fields="interactions.contact_id, interactions.engagement_type, interactions.interaction_id, contacts.contact_id, contacts.account_id, accounts.account_id, accounts.customer_tier",
             judge="The answer should state a single count of interactions of the named engagement type from contacts whose account is in the named customer tier.",
             rationale="INNER JOIN interactions -> contacts -> accounts via contact_id then account_id, filter to the given engagement_type and customer_tier, count interaction rows."),
    ]


# ---------------------------------------------------------------------
# T5 - semantic / contextual logic (deterministic numeric core). 5 families -> 24.
# ---------------------------------------------------------------------
def t5_specs(v: dict) -> list[dict]:
    return [
        dict(family="T5-01", block="t5/T5-01.sql.j2", param="region", values=v["region"],
             q="Which three campaign types drove the most engagement among {region}-region accounts?",
             tables="campaigns, interactions, contacts, accounts",
             fields="campaigns.campaign_id, campaigns.campaign_type, interactions.campaign_id, interactions.contact_id, interactions.engagement_points, contacts.contact_id, contacts.account_id, accounts.account_id, accounts.region",
             judge="The answer should name exactly three campaign types ranked by total engagement_points among accounts in the named region, highest first, with their totals.",
             rationale="Join campaigns -> interactions -> contacts -> accounts, filter to the region, sum engagement_points per campaign_type, order descending with campaign_type as tie-break, take top 3."),
        dict(family="T5-02", block="t5/T5-02.sql.j2", param="category", values=v["category"],
             q="Which accounts have the largest open backlog of {category} support cases right now?",
             tables="accounts, support_cases",
             fields="accounts.account_id, accounts.account_name, support_cases.account_id, support_cases.category, support_cases.status, support_cases.case_id",
             judge="The answer should list the top accounts by count of unresolved support cases in the named category, highest first.",
             rationale="INNER JOIN accounts to support_cases on account_id, filter to the given category and unresolved statuses, count cases per account, order descending with account_name as tie-break, top 5."),
        dict(family="T5-03", block="t5/T5-03.sql.j2", param="industry", values=v["industry"],
             q="Which {industry}-industry accounts look at risk based on an open support backlog and no recent engagement?",
             tables="accounts, support_cases, interactions",
             fields="accounts.account_id, accounts.account_name, accounts.industry, support_cases.account_id, support_cases.status, interactions.account_id, interactions.interaction_at",
             judge="The answer should identify accounts in the named industry with at least three unresolved support cases and zero interactions in the last 60 days as of the fixed reference date, with the two supporting numbers.",
             rationale="Count unresolved cases and interactions since 2026-06-02 (60 days before the fixed today 2026-08-01) per account. Left join both to accounts, filter to the industry, at least 3 open cases and zero recent interactions, order by open-case count descending with account_name as tie-break, top 5."),
        dict(family="T5-04", block="t5/T5-04.sql.j2", param="region", values=v["region"],
             q="How did engagement among {region}-region accounts change from last quarter to this quarter?",
             tables="interactions, accounts",
             fields="interactions.account_id, interactions.interaction_at, interactions.engagement_points, accounts.account_id, accounts.region",
             judge="The answer should state total engagement_points for the previous quarter and the current quarter for the named region (both anchored to the fixed reference date) and describe the change.",
             rationale="Previous quarter 2026-04-01..2026-07-01, current quarter 2026-07-01..2026-10-01, anchored to fixed today 2026-08-01. INNER JOIN interactions to accounts on account_id, filter to the region, sum engagement_points in each window."),
        dict(family="T5-05", block="t5/T5-05.sql.j2", param="customer_tier", values=v["customer_tier"],
             q="Among {customer_tier}-tier accounts, which region grew its engagement the fastest compared to last quarter?",
             tables="accounts, interactions",
             fields="accounts.account_id, accounts.region, accounts.customer_tier, interactions.account_id, interactions.engagement_points, interactions.interaction_at",
             judge="The answer should name a single region and give its percentage change in engagement_points from the previous quarter to the current quarter, restricted to accounts in the named customer tier.",
             rationale="INNER JOIN accounts to interactions on account_id, restrict to the customer tier, sum engagement_points per region for the previous and current quarter windows, compute percent change, regions with zero prior-quarter engagement sorted last, take the top region."),
    ]


def _combinations(spec):
    if "params" in spec:
        (n1, v1), (n2, v2) = spec["params"]
        for a in v1:
            for b in v2:
                yield {n1: a, n2: b}
    else:
        for val in spec["values"]:
            yield {spec["param"]: val}


def scale(con, log_dir, dataset_version, specs, tier, target):
    rows, seq = [], 1
    for spec in specs:
        for base_ctx in _combinations(spec):
            if len(rows) >= target:
                break
            ctx = dict(base_ctx)
            if "q_ctx" in spec:
                ctx.update(spec["q_ctx"](next(iter(base_ctx.values()))))
            sql = env.get_template(spec["block"]).render(**base_ctx).strip()
            question_id = f"CRM-{spec['family']}-{seq:02d}"
            status, expected = run_sql(con, log_dir, dataset_version,
                                       question_id, tier, sql)
            if status != "ok":
                continue  # blocked pairs are logged, not shipped
            rows.append({
                "question_id": question_id, "tier": tier,
                "natural_language_question": spec["q"].format(**ctx),
                "expected_answer": expected,
                "reference_sql": sql,
                "reference_tables": spec["tables"],
                "reference_fields": spec["fields"],
                "judge_reference": spec["judge"],
                "derivation_rationale": spec["rationale"],
            })
            seq += 1
        if len(rows) >= target:
            break
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, quoting=csv.QUOTE_MINIMAL,
                           lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in FIELDS})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    profile = ap.parse_args().profile

    manifest = json.loads(
        (BASE / "dataset" / f"manifest_{profile}.json").read_text())
    dataset_version = manifest["dataset_version"]

    out_dir = BASE / "qa_pairs" / profile
    log_dir = out_dir / "verification_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    con = connect(profile)
    v = value_catalog(con)

    plan = [
        ("T1", t1_specs(v), 32, "crm_t1_pairs_HELD.csv", "HELD (OI-1)"),
        ("T2", t2_specs(v), 40, "crm_t2_pairs.csv", "shipped"),
        ("T3", t3_specs(v), 32, "crm_t3_pairs_HELD.csv", "HELD (OI-4)"),
        ("T4", t4_specs(v), 32, "crm_t4_pairs.csv", "shipped"),
        ("T5", t5_specs(v), 24, "crm_t5_pairs.csv", "shipped"),
    ]
    total = 0
    for tier, specs, target, filename, note in plan:
        rows = scale(con, log_dir, dataset_version, specs, tier, target)
        write_csv(out_dir / filename, rows)
        total += len(rows)
        flag = "" if len(rows) == target else "  <-- UNDER QUOTA"
        print(f"[{profile}] {tier} ({note}): {len(rows)} / {target}{flag}")
    print(f"[{profile}] total templated pairs: {total} / 160")
    print(f"logs -> {log_dir}")


if __name__ == "__main__":
    main()
