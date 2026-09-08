"""
Authors CRM Q&A seed pairs: one hand-written pair per taxonomy family
across all five tiers. Executes each reference_sql against the exported
CRM CSVs in DuckDB and writes expected_answer from the query result -
never hand-typed (scope doc Section 9.6).

    python generator/author_qa_pairs.py --profile dev
    python generator/author_qa_pairs.py --profile full

Output (under qa_pairs/<profile>/):
  crm_qa_pairs_seed.csv        T2/T4/T5 pairs - clear to ship
  crm_qa_pairs_seed_HELD.csv   T1 (OI-1) and T3 (OI-4) pairs - held
  verification_logs/*.json     one log per question
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent.parent
TABLES = ["accounts", "campaigns", "contacts", "contact_campaigns",
          "interactions", "support_cases"]

FIXED_TODAY = "2026-08-01"                      # crm_dataset_v2 reference_today
Q_START, Q_END = "2026-07-01", "2026-10-01"     # current quarter (Q3 2026)
PREV_Q_START, PREV_Q_END = "2026-04-01", "2026-07-01"
RECENT_SINCE = "2026-06-02"                     # 60 days before FIXED_TODAY

FIELDS = [
    "question_id", "tier", "natural_language_question", "expected_answer",
    "reference_sql", "reference_tables", "reference_fields",
    "judge_reference", "derivation_rationale",
]

PAIRS = [
    # ---------------- T1 (HELD - OI-1 dependent) ----------------
    dict(question_id="CRM-T1-01-SEED-01", tier="T1", held=True,
         natural_language_question="What is the total budget of Retention campaigns?",
         reference_sql="SELECT ROUND(SUM(budget_amount), 2) AS total_budget FROM campaigns WHERE campaign_type = 'Retention';",
         reference_tables="campaigns",
         reference_fields="campaigns.campaign_type, campaigns.budget_amount",
         judge_reference="The answer should state a single total budget figure for all Retention-type campaigns.",
         derivation_rationale="Filter campaigns to campaign_type = 'Retention', sum budget_amount rounded to 2 decimals. Campaigns with a NULL budget contribute nothing."),
    dict(question_id="CRM-T1-02-SEED-01", tier="T1", held=True,
         natural_language_question="How many campaigns are currently active?",
         reference_sql="SELECT COUNT(*) AS active_campaigns FROM campaigns WHERE status = 'Active';",
         reference_tables="campaigns", reference_fields="campaigns.status",
         judge_reference="The answer should state a single count of campaigns whose status is Active.",
         derivation_rationale="Filter campaigns to status = 'Active' and count rows."),
    dict(question_id="CRM-T1-03-SEED-01", tier="T1", held=True,
         natural_language_question="How many support cases are logged as critical priority?",
         reference_sql="SELECT COUNT(*) AS critical_cases FROM support_cases WHERE priority = 'Critical';",
         reference_tables="support_cases", reference_fields="support_cases.priority",
         judge_reference="The answer should state a single count of support cases at Critical priority.",
         derivation_rationale="Filter support_cases to priority = 'Critical' and count rows, including boundary-date cases."),
    dict(question_id="CRM-T1-04-SEED-01", tier="T1", held=True,
         natural_language_question="How many support cases fall under the Billing category?",
         reference_sql="SELECT COUNT(*) AS billing_cases FROM support_cases WHERE category = 'Billing';",
         reference_tables="support_cases", reference_fields="support_cases.category",
         judge_reference="The answer should state a single count of support cases in the Billing category.",
         derivation_rationale="Filter support_cases to category = 'Billing' and count rows."),
    dict(question_id="CRM-T1-05-SEED-01", tier="T1", held=True,
         natural_language_question="How many interactions are meetings?",
         reference_sql="SELECT COUNT(*) AS meeting_interactions FROM interactions WHERE engagement_type = 'Meeting';",
         reference_tables="interactions", reference_fields="interactions.engagement_type",
         judge_reference="The answer should state a single count of interactions with engagement_type Meeting.",
         derivation_rationale="Filter interactions to engagement_type = 'Meeting' and count rows."),
    dict(question_id="CRM-T1-06-SEED-01", tier="T1", held=True,
         natural_language_question="How many accounts do we have in the West region?",
         reference_sql="SELECT COUNT(*) AS west_accounts FROM accounts WHERE region = 'West';",
         reference_tables="accounts", reference_fields="accounts.region",
         judge_reference="The answer should state a single count of accounts where region is West.",
         derivation_rationale="Filter accounts to region = 'West' and count rows."),

    # ---------------- T2 (clear to ship) ----------------
    dict(question_id="CRM-T2-01-SEED-01", tier="T2", held=False,
         natural_language_question="How many support cases belong to West-region accounts?",
         reference_sql="SELECT COUNT(s.case_id) AS case_count FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id WHERE a.region = 'West';",
         reference_tables="support_cases, accounts",
         reference_fields="support_cases.account_id, support_cases.case_id, accounts.account_id, accounts.region",
         judge_reference="The answer should state a single count of support cases whose owning account is in the West region.",
         derivation_rationale="Join support_cases to accounts on account_id, filter to region = 'West', count case rows."),
    dict(question_id="CRM-T2-02-SEED-01", tier="T2", held=False,
         natural_language_question="Which customer tier has the most critical-priority support cases?",
         reference_sql="SELECT a.customer_tier, COUNT(s.case_id) AS case_count FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id WHERE s.priority = 'Critical' GROUP BY a.customer_tier ORDER BY case_count DESC, a.customer_tier ASC LIMIT 1;",
         reference_tables="support_cases, accounts",
         reference_fields="support_cases.account_id, support_cases.priority, support_cases.case_id, accounts.account_id, accounts.customer_tier",
         judge_reference="The answer should name a single customer tier and its count of Critical-priority support cases.",
         derivation_rationale="Join support_cases to accounts on account_id, filter to priority = 'Critical', count cases grouped by customer_tier, sort descending with customer_tier as tie-break, take the top row."),
    dict(question_id="CRM-T2-03-SEED-01", tier="T2", held=False,
         natural_language_question="How many contacts do we have across accounts in the technology industry?",
         reference_sql="SELECT COUNT(c.contact_id) AS contact_count FROM contacts c INNER JOIN accounts a ON c.account_id = a.account_id WHERE a.industry = 'Technology';",
         reference_tables="contacts, accounts",
         reference_fields="contacts.account_id, contacts.contact_id, accounts.account_id, accounts.industry",
         judge_reference="The answer should state a single count of contacts belonging to accounts in the Technology industry.",
         derivation_rationale="Join contacts to accounts on account_id, filter to industry = 'Technology', count contact rows. Unassigned contacts (NULL account_id) are excluded by the INNER JOIN."),
    dict(question_id="CRM-T2-04-SEED-01", tier="T2", held=False,
         natural_language_question="How many interactions are attributed to Retention campaigns?",
         reference_sql="SELECT COUNT(i.interaction_id) AS interaction_count FROM interactions i INNER JOIN campaigns c ON i.campaign_id = c.campaign_id WHERE c.campaign_type = 'Retention';",
         reference_tables="interactions, campaigns",
         reference_fields="interactions.campaign_id, interactions.interaction_id, campaigns.campaign_id, campaigns.campaign_type",
         judge_reference="The answer should state a single count of interactions attributed to Retention-type campaigns.",
         derivation_rationale="Join interactions to campaigns on campaign_id, filter to campaign_type = 'Retention', count interaction rows. Organic interactions (NULL campaign_id) are excluded by the INNER JOIN."),
    dict(question_id="CRM-T2-05-SEED-01", tier="T2", held=False,
         natural_language_question="What is the inbound/outbound interaction breakdown for active contacts?",
         reference_sql="SELECT i.direction, COUNT(*) AS direction_count FROM interactions i INNER JOIN contacts c ON i.contact_id = c.contact_id WHERE c.is_active = true GROUP BY i.direction ORDER BY direction_count DESC, i.direction ASC;",
         reference_tables="interactions, contacts",
         reference_fields="interactions.contact_id, interactions.direction, contacts.contact_id, contacts.is_active",
         judge_reference="The answer should break interactions down by direction (Inbound/Outbound) with counts, restricted to interactions belonging to active contacts.",
         derivation_rationale="Join interactions to contacts on contact_id, filter to is_active = true, group by direction and count, sort descending with direction as tie-break."),
    dict(question_id="CRM-T2-06-SEED-01", tier="T2", held=False,
         natural_language_question="Which accounts had the most meeting interactions recorded directly against them?",
         reference_sql="SELECT a.account_name, COUNT(i.interaction_id) AS interaction_count FROM accounts a INNER JOIN interactions i ON a.account_id = i.account_id WHERE i.engagement_type = 'Meeting' GROUP BY a.account_id, a.account_name ORDER BY interaction_count DESC, a.account_name ASC LIMIT 5;",
         reference_tables="accounts, interactions",
         reference_fields="accounts.account_id, accounts.account_name, interactions.account_id, interactions.engagement_type, interactions.interaction_id",
         judge_reference="The answer should list the top accounts by count of Meeting-type interactions recorded on the direct account-to-interaction relationship, highest first.",
         derivation_rationale="Join accounts to interactions on account_id, filter to engagement_type = 'Meeting', count interactions grouped by account, sort descending with account_name as tie-break, show top 5."),
    dict(question_id="CRM-T2-07-SEED-01", tier="T2", held=False,
         natural_language_question="How many email interactions came from contacts with the title IT Director?",
         reference_sql="SELECT COUNT(i.interaction_id) AS interaction_count FROM interactions i INNER JOIN contacts c ON i.contact_id = c.contact_id WHERE i.channel = 'Email' AND c.title = 'IT Director';",
         reference_tables="interactions, contacts",
         reference_fields="interactions.contact_id, interactions.channel, interactions.interaction_id, contacts.contact_id, contacts.title",
         judge_reference="The answer should state a single count of Email-channel interactions tied to contacts holding the title IT Director.",
         derivation_rationale="Join interactions to contacts on contact_id, filter to channel = 'Email' and title = 'IT Director', count interaction rows."),

    # ---------------- T3 (HELD - OI-4 dependent) ----------------
    dict(question_id="CRM-T3-01-SEED-01", tier="T3", held=True,
         natural_language_question="How many customer accounts have no contacts on record?",
         reference_sql="SELECT COUNT(*) AS accounts_without_contacts FROM accounts a LEFT JOIN contacts c ON a.account_id = c.account_id WHERE c.contact_id IS NULL;",
         reference_tables="accounts, contacts",
         reference_fields="accounts.account_id, contacts.account_id, contacts.contact_id",
         judge_reference="The answer should state a single count of accounts that have zero associated contacts.",
         derivation_rationale="Left join accounts to contacts on account_id, keep only rows where the join produced no match (contact_id IS NULL), count those accounts."),
    dict(question_id="CRM-T3-02-SEED-01", tier="T3", held=True,
         natural_language_question="How many accounts have no interactions recorded directly against them?",
         reference_sql="SELECT COUNT(*) AS accounts_without_interactions FROM accounts a LEFT JOIN interactions i ON a.account_id = i.account_id WHERE i.interaction_id IS NULL;",
         reference_tables="accounts, interactions",
         reference_fields="accounts.account_id, interactions.account_id, interactions.interaction_id",
         judge_reference="The answer should state a single count of accounts that have zero interactions on the direct account-to-interaction relationship (interactions.account_id).",
         derivation_rationale="Left join accounts to interactions on account_id, keep unmatched rows (interaction_id IS NULL), count those accounts. Interactions carry a nullable account_id, so this is the accounts with no directly-attributed engagement."),
    dict(question_id="CRM-T3-03-SEED-01", tier="T3", held=True,
         natural_language_question="How many contacts have no recorded interactions?",
         reference_sql="SELECT COUNT(*) AS contacts_without_interactions FROM contacts c LEFT JOIN interactions i ON c.contact_id = i.contact_id WHERE i.interaction_id IS NULL;",
         reference_tables="contacts, interactions",
         reference_fields="contacts.contact_id, interactions.contact_id, interactions.interaction_id",
         judge_reference="The answer should state a single count of contacts with zero recorded interactions.",
         derivation_rationale="Left join contacts to interactions on contact_id, keep unmatched rows (interaction_id IS NULL), count those contacts."),
    dict(question_id="CRM-T3-04-SEED-01", tier="T3", held=True,
         natural_language_question="How many contacts are not enrolled in any campaign?",
         reference_sql="SELECT COUNT(*) AS contacts_without_campaigns FROM contacts c LEFT JOIN contact_campaigns cc ON c.contact_id = cc.contact_id WHERE cc.campaign_id IS NULL;",
         reference_tables="contacts, contact_campaigns",
         reference_fields="contacts.contact_id, contact_campaigns.contact_id, contact_campaigns.campaign_id",
         judge_reference="The answer should state a single count of contacts with no row in the contact-campaign membership table.",
         derivation_rationale="Left join contacts to contact_campaigns on contact_id, keep unmatched rows (campaign_id IS NULL), count those contacts."),
    dict(question_id="CRM-T3-05-SEED-01", tier="T3", held=True,
         natural_language_question="How many campaigns have no attributed interactions?",
         reference_sql="SELECT COUNT(*) AS campaigns_without_interactions FROM campaigns c LEFT JOIN interactions i ON c.campaign_id = i.campaign_id WHERE i.interaction_id IS NULL;",
         reference_tables="campaigns, interactions",
         reference_fields="campaigns.campaign_id, interactions.campaign_id, interactions.interaction_id",
         judge_reference="The answer should state a single count of campaigns that have zero attributed interactions.",
         derivation_rationale="Left join campaigns to interactions on campaign_id, keep unmatched rows (interaction_id IS NULL), count those campaigns."),

    # ---------------- T4 (clear to ship) ----------------
    dict(question_id="CRM-T4-01-SEED-01", tier="T4", held=False,
         natural_language_question="What is the total engagement-point score generated by contacts at West-region accounts?",
         reference_sql="SELECT SUM(i.engagement_points) AS total_points FROM accounts a INNER JOIN contacts c ON a.account_id = c.account_id INNER JOIN interactions i ON c.contact_id = i.contact_id WHERE a.region = 'West';",
         reference_tables="accounts, contacts, interactions",
         reference_fields="accounts.account_id, accounts.region, contacts.account_id, contacts.contact_id, interactions.contact_id, interactions.engagement_points",
         judge_reference="The answer should state a single total of engagement_points across all interactions belonging to contacts whose account is in the West region.",
         derivation_rationale="Join accounts to contacts to interactions via account_id then contact_id, filter to region = 'West', sum engagement_points. The ~0.5% engagement-point outliers are included because the question asks for the total as delivered."),
    dict(question_id="CRM-T4-02-SEED-01", tier="T4", held=False,
         natural_language_question="How many support cases were raised by contacts at healthcare-industry accounts?",
         reference_sql="SELECT COUNT(s.case_id) AS case_count FROM accounts a INNER JOIN contacts c ON a.account_id = c.account_id INNER JOIN support_cases s ON c.contact_id = s.contact_id WHERE a.industry = 'Healthcare';",
         reference_tables="accounts, contacts, support_cases",
         reference_fields="accounts.account_id, accounts.industry, contacts.account_id, contacts.contact_id, support_cases.contact_id, support_cases.case_id",
         judge_reference="The answer should state a single count of support cases whose requesting contact belongs to a Healthcare-industry account.",
         derivation_rationale="Join accounts to contacts to support_cases via account_id then contact_id, filter to industry = 'Healthcare', count case rows. Account-level cases (NULL contact_id) are excluded by the INNER JOIN."),
    dict(question_id="CRM-T4-03-SEED-01", tier="T4", held=False,
         natural_language_question="Which accounts' contact base generated the most engagement?",
         reference_sql="SELECT a.account_name, SUM(i.engagement_points) AS total_points FROM accounts a INNER JOIN contacts c ON a.account_id = c.account_id INNER JOIN interactions i ON c.contact_id = i.contact_id GROUP BY a.account_id, a.account_name ORDER BY total_points DESC, a.account_name ASC LIMIT 5;",
         reference_tables="accounts, contacts, interactions",
         reference_fields="accounts.account_id, accounts.account_name, contacts.account_id, contacts.contact_id, interactions.contact_id, interactions.engagement_points",
         judge_reference="The answer should list the top accounts by total engagement_points generated through their contacts, highest first.",
         derivation_rationale="Join accounts to contacts to interactions via account_id then contact_id, sum engagement_points grouped by account, sort descending with account_name as tie-break, show top 5."),

    # ---------------- T5 (clear to ship) ----------------
    dict(question_id="CRM-T5-01-SEED-01", tier="T5", held=False,
         natural_language_question="Which three campaign types drove the most customer engagement?",
         reference_sql="SELECT c.campaign_type, SUM(i.engagement_points) AS total_points FROM campaigns c INNER JOIN interactions i ON c.campaign_id = i.campaign_id GROUP BY c.campaign_type ORDER BY total_points DESC, c.campaign_type ASC LIMIT 3;",
         reference_tables="campaigns, interactions",
         reference_fields="campaigns.campaign_id, campaigns.campaign_type, interactions.campaign_id, interactions.engagement_points",
         judge_reference="The answer should name exactly three campaign types ranked by total engagement_points from campaign-attributed interactions, highest to lowest, with their totals.",
         derivation_rationale="Join campaigns to interactions on campaign_id, sum engagement_points grouped by campaign_type, sort descending with campaign_type as tie-break, take the top three."),
    dict(question_id="CRM-T5-02-SEED-01", tier="T5", held=False,
         natural_language_question="Which accounts have the largest open support backlog right now?",
         reference_sql="SELECT a.account_name, COUNT(s.case_id) AS open_cases FROM accounts a INNER JOIN support_cases s ON a.account_id = s.account_id WHERE s.status IN ('New', 'InProgress', 'PendingCustomer') GROUP BY a.account_id, a.account_name ORDER BY open_cases DESC, a.account_name ASC LIMIT 5;",
         reference_tables="accounts, support_cases",
         reference_fields="accounts.account_id, accounts.account_name, support_cases.account_id, support_cases.status, support_cases.case_id",
         judge_reference="The answer should list the top accounts by number of unresolved support cases (status New, InProgress, or PendingCustomer), highest first.",
         derivation_rationale="Join accounts to support_cases on account_id, filter to unresolved statuses, count cases grouped by account, sort descending with account_name as tie-break, show top 5."),
    dict(question_id="CRM-T5-03-SEED-01", tier="T5", held=False,
         natural_language_question="Which accounts look at risk based on an open support backlog and no recent engagement?",
         reference_sql=(
             "WITH open_cases AS (SELECT account_id, COUNT(*) AS open_case_count FROM support_cases "
             "WHERE status IN ('New', 'InProgress', 'PendingCustomer') GROUP BY account_id), "
             "recent_engagement AS (SELECT account_id, COUNT(*) AS recent_interaction_count FROM interactions "
             f"WHERE interaction_at >= DATE '{RECENT_SINCE}' AND account_id IS NOT NULL GROUP BY account_id) "
             "SELECT a.account_name, COALESCE(oc.open_case_count, 0) AS open_case_count, "
             "COALESCE(re.recent_interaction_count, 0) AS recent_interaction_count "
             "FROM accounts a LEFT JOIN open_cases oc ON a.account_id = oc.account_id "
             "LEFT JOIN recent_engagement re ON a.account_id = re.account_id "
             "WHERE COALESCE(oc.open_case_count, 0) >= 5 AND COALESCE(re.recent_interaction_count, 0) = 0 "
             "ORDER BY open_case_count DESC, a.account_name ASC LIMIT 5;"),
         reference_tables="accounts, support_cases, interactions",
         reference_fields="accounts.account_id, accounts.account_name, support_cases.account_id, support_cases.status, interactions.account_id, interactions.interaction_at",
         judge_reference="The answer should identify accounts that have a sizeable open support backlog and zero interactions in the last 60 days as of the fixed reference date, presented as an at-risk list with the two supporting numbers.",
         derivation_rationale=f"Count unresolved support cases per account and interactions since {RECENT_SINCE} (60 days before the fixed today constant {FIXED_TODAY}) per account. Left join both to accounts, filter to accounts with at least 5 open cases and zero recent interactions, sort by open-case count descending with account_name as tie-break, take top 5."),
    dict(question_id="CRM-T5-04-SEED-01", tier="T5", held=False,
         natural_language_question="How did customer engagement change from last quarter to this quarter?",
         reference_sql=(
             f"SELECT SUM(CASE WHEN interaction_at >= DATE '{PREV_Q_START}' AND interaction_at < DATE '{PREV_Q_END}' THEN engagement_points ELSE 0 END) AS prev_quarter_points, "
             f"SUM(CASE WHEN interaction_at >= DATE '{Q_START}' AND interaction_at < DATE '{Q_END}' THEN engagement_points ELSE 0 END) AS this_quarter_points "
             "FROM interactions;"),
         reference_tables="interactions",
         reference_fields="interactions.interaction_at, interactions.engagement_points",
         judge_reference="The answer should state total engagement_points for the previous quarter and the current quarter (both anchored to the fixed reference date) and describe the direction and size of the change.",
         derivation_rationale=f"Previous quarter is {PREV_Q_START} (inclusive) to {PREV_Q_END} (exclusive); current quarter is {Q_START} (inclusive) to {Q_END} (exclusive), both anchored to the fixed today constant {FIXED_TODAY}. Sum engagement_points within each window over all interactions."),
    dict(question_id="CRM-T5-05-SEED-01", tier="T5", held=False,
         natural_language_question="Which region grew its customer engagement the fastest compared to last quarter?",
         reference_sql=(
             "WITH by_region AS (SELECT a.region, "
             f"SUM(CASE WHEN i.interaction_at >= DATE '{PREV_Q_START}' AND i.interaction_at < DATE '{PREV_Q_END}' THEN i.engagement_points ELSE 0 END) AS prev_q, "
             f"SUM(CASE WHEN i.interaction_at >= DATE '{Q_START}' AND i.interaction_at < DATE '{Q_END}' THEN i.engagement_points ELSE 0 END) AS this_q "
             "FROM accounts a INNER JOIN interactions i ON a.account_id = i.account_id "
             "WHERE a.region IS NOT NULL GROUP BY a.region) "
             "SELECT region, prev_q AS prev_quarter_points, this_q AS this_quarter_points, "
             "ROUND(CASE WHEN prev_q = 0 THEN NULL ELSE (this_q - prev_q) / CAST(prev_q AS DOUBLE) * 100 END, 2) AS pct_change "
             "FROM by_region ORDER BY pct_change DESC NULLS LAST, region ASC LIMIT 1;"),
         reference_tables="accounts, interactions",
         reference_fields="accounts.account_id, accounts.region, interactions.account_id, interactions.engagement_points, interactions.interaction_at",
         judge_reference="The answer should name a single region and state its percentage change in engagement_points from the previous quarter to the current quarter, both anchored to the fixed reference date.",
         derivation_rationale=f"Join accounts to interactions on account_id, sum engagement_points per region for the previous quarter ({PREV_Q_START} to {PREV_Q_END}) and current quarter ({Q_START} to {Q_END}). Percentage change is (this_q - prev_q) / prev_q * 100. Regions with zero prior-quarter engagement are undefined and sorted last. Take the top region."),
]


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


def run_pair(con, log_dir: Path, dataset_version: str, p: dict):
    t0 = time.time()
    status, error, rows, cols = "ok", None, [], []
    try:
        cur = con.execute(p["reference_sql"])
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
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
        "question_id": p["question_id"], "domain": "crm", "tier": p["tier"],
        "qa_version": "0.1.0-dev", "dataset_version": dataset_version,
        "reference_sql": p["reference_sql"], "execution_status": status,
        "error": error, "row_count_returned": len(rows),
        "execution_time_seconds": round(elapsed, 4),
        "result_hash": sha256_text(repr(rows)),
        "generated_expected_answer": expected_answer,
        "verification_timestamp": "2026-08-01T00:00:00",
    }
    (log_dir / f"{p['question_id']}.json").write_text(
        json.dumps(log, indent=2), encoding="utf-8")
    return status, expected_answer


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, quoting=csv.QUOTE_MINIMAL,
                           lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


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
    shipped, held, blocked = [], [], []
    for p in PAIRS:
        status, expected = run_pair(con, log_dir, dataset_version, p)
        if status != "ok":
            blocked.append((p["question_id"], status))
            continue
        row = {f: p.get(f, "") for f in FIELDS}
        row["expected_answer"] = expected
        (held if p["held"] else shipped).append(row)

    write_csv(out_dir / "crm_qa_pairs_seed.csv", shipped)
    write_csv(out_dir / "crm_qa_pairs_seed_HELD.csv", held)

    print(f"[{profile}] shipped seed pairs (T2/T4/T5): {len(shipped)}")
    print(f"[{profile}] held seed pairs (T1/T3):       {len(held)}")
    if blocked:
        print("BLOCKED:")
        for qid, status in blocked:
            print(f"  {qid}: {status}")
    else:
        print("no blocked pairs")


if __name__ == "__main__":
    main()
