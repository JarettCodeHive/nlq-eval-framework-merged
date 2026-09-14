"""
CRM Q&A seed FIXTURES: one hand-written pair per taxonomy family across
all five tiers. These are REVIEW FIXTURES - a small, readable set for an
independent reviewer to hand-check the approach. They are NOT part of the
160-pair release (that is scale_pairs.py) and are not counted toward any
quota.

Every expected_answer is still computed by executing reference_sql
against the typed CRM DuckDB (scope doc Section 9.6) - never hand-typed.

    python generator/author_qa_pairs.py --profile dev
    python generator/author_qa_pairs.py --profile full

Output (under fixtures/<profile>/):
  crm_seed_fixtures.csv            7-field contract, one per family (32)
  crm_seed_fixtures_companion.csv  question_id / tier / family / result_hash / question
  verification_logs/*.json         one log per fixture
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from utils import build_stamp, verify
from utils.duckdb_io import connect_typed

FIXED_TODAY = "2026-08-01"  # crm_dataset_v2 reference_today
# The two most recent COMPLETE calendar quarters relative to FIXED_TODAY.
# Data ends 2026-07-28, so Q3 2026 is incomplete and excluded.
Q1_START, Q1_END = "2026-01-01", "2026-04-01"  # previous complete quarter
Q2_START, Q2_END = "2026-04-01", "2026-07-01"  # most recent complete quarter
RECENT_SINCE = "2026-06-02"  # 60 days before FIXED_TODAY

CONTRACT_FIELDS = [
    "natural_language_question",
    "expected_answer",
    "reference_sql",
    "reference_tables",
    "reference_fields",
    "judge_reference",
    "derivation_rationale",
]
COMPANION_FIELDS = [
    "question_id",
    "tier",
    "family",
    "result_hash",
    "natural_language_question",
]

PAIRS = [
    # ---------------- T1 (HELD - OI-1 dependent) ----------------
    dict(
        question_id="CRM-T1-01-SEED-01",
        tier="T1",
        natural_language_question="What is the total budget of Retention campaigns?",
        reference_sql="SELECT ROUND(SUM(budget_amount), 2) AS total_budget FROM campaigns WHERE campaign_type = 'Retention';",
        reference_tables="campaigns",
        reference_fields="campaigns.campaign_type, campaigns.budget_amount",
        judge_reference="The answer should state a single total budget figure for all Retention-type campaigns.",
        derivation_rationale="Filter campaigns to campaign_type = 'Retention', sum budget_amount rounded to 2 decimals. Campaigns with a NULL budget contribute nothing.",
    ),
    dict(
        question_id="CRM-T1-02-SEED-01",
        tier="T1",
        natural_language_question="How many campaigns are currently active?",
        reference_sql="SELECT COUNT(*) AS active_campaigns FROM campaigns WHERE status = 'Active';",
        reference_tables="campaigns",
        reference_fields="campaigns.status",
        judge_reference="The answer should state a single count of campaigns whose status is Active.",
        derivation_rationale="Filter campaigns to status = 'Active' and count rows.",
    ),
    dict(
        question_id="CRM-T1-03-SEED-01",
        tier="T1",
        natural_language_question="How many support cases are logged as critical priority?",
        reference_sql="SELECT COUNT(*) AS critical_cases FROM support_cases WHERE priority = 'Critical';",
        reference_tables="support_cases",
        reference_fields="support_cases.priority",
        judge_reference="The answer should state a single count of support cases at Critical priority.",
        derivation_rationale="Filter support_cases to priority = 'Critical' and count rows, including boundary-date cases.",
    ),
    dict(
        question_id="CRM-T1-04-SEED-01",
        tier="T1",
        natural_language_question="How many support cases fall under the Billing category?",
        reference_sql="SELECT COUNT(*) AS billing_cases FROM support_cases WHERE category = 'Billing';",
        reference_tables="support_cases",
        reference_fields="support_cases.category",
        judge_reference="The answer should state a single count of support cases in the Billing category.",
        derivation_rationale="Filter support_cases to category = 'Billing' and count rows.",
    ),
    dict(
        question_id="CRM-T1-05-SEED-01",
        tier="T1",
        natural_language_question="How many interactions are meetings?",
        reference_sql="SELECT COUNT(*) AS meeting_interactions FROM interactions WHERE engagement_type = 'Meeting';",
        reference_tables="interactions",
        reference_fields="interactions.engagement_type",
        judge_reference="The answer should state a single count of interactions with engagement_type Meeting.",
        derivation_rationale="Filter interactions to engagement_type = 'Meeting' and count rows.",
    ),
    dict(
        question_id="CRM-T1-06-SEED-01",
        tier="T1",
        natural_language_question="How many accounts do we have in the West region?",
        reference_sql="SELECT COUNT(*) AS west_accounts FROM accounts WHERE region = 'West';",
        reference_tables="accounts",
        reference_fields="accounts.region",
        judge_reference="The answer should state a single count of accounts where region is West.",
        derivation_rationale="Filter accounts to region = 'West' and count rows.",
    ),
    dict(
        question_id="CRM-T1-07-SEED-01",
        tier="T1",
        natural_language_question="How many critical-priority support cases were resolved within their SLA deadline?",
        reference_sql="SELECT COUNT(*) AS resolved_within_sla FROM support_cases WHERE priority = 'Critical' AND resolved_at IS NOT NULL AND resolved_at <= sla_due_at;",
        reference_tables="support_cases",
        reference_fields="support_cases.priority, support_cases.resolved_at, support_cases.sla_due_at",
        judge_reference="A single count of Critical-priority support cases whose resolved_at is on or before sla_due_at. Unresolved cases are not counted.",
        derivation_rationale="Filter support_cases to priority = 'Critical'. Within SLA = resolved_at IS NOT NULL AND resolved_at <= sla_due_at. Count those rows.",
    ),
    # ---------------- T2 (clear to ship) ----------------
    dict(
        question_id="CRM-T2-01-SEED-01",
        tier="T2",
        natural_language_question="How many support cases belong to West-region accounts?",
        reference_sql="SELECT COUNT(s.case_id) AS case_count FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id WHERE a.region = 'West';",
        reference_tables="support_cases, accounts",
        reference_fields="support_cases.account_id, support_cases.case_id, accounts.account_id, accounts.region",
        judge_reference="The answer should state a single count of support cases whose owning account is in the West region.",
        derivation_rationale="Join support_cases to accounts on account_id, filter to region = 'West', count case rows.",
    ),
    dict(
        question_id="CRM-T2-02-SEED-01",
        tier="T2",
        natural_language_question="Which customer tier has the most critical-priority support cases?",
        reference_sql="SELECT a.customer_tier, COUNT(s.case_id) AS case_count FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id WHERE s.priority = 'Critical' GROUP BY a.customer_tier ORDER BY case_count DESC, a.customer_tier ASC LIMIT 1;",
        reference_tables="support_cases, accounts",
        reference_fields="support_cases.account_id, support_cases.priority, support_cases.case_id, accounts.account_id, accounts.customer_tier",
        judge_reference="The answer should name a single customer tier and its count of Critical-priority support cases.",
        derivation_rationale="Join support_cases to accounts on account_id, filter to priority = 'Critical', count cases grouped by customer_tier, sort descending with customer_tier as tie-break, take the top row.",
    ),
    dict(
        question_id="CRM-T2-03-SEED-01",
        tier="T2",
        natural_language_question="How many contacts do we have across accounts in the technology industry?",
        reference_sql="SELECT COUNT(c.contact_id) AS contact_count FROM contacts c INNER JOIN accounts a ON c.account_id = a.account_id WHERE a.industry = 'Technology';",
        reference_tables="contacts, accounts",
        reference_fields="contacts.account_id, contacts.contact_id, accounts.account_id, accounts.industry",
        judge_reference="The answer should state a single count of contacts belonging to accounts in the Technology industry.",
        derivation_rationale="Join contacts to accounts on account_id, filter to industry = 'Technology', count contact rows. Unassigned contacts (NULL account_id) are excluded by the INNER JOIN.",
    ),
    dict(
        question_id="CRM-T2-04-SEED-01",
        tier="T2",
        natural_language_question="How many interactions are attributed to Retention campaigns?",
        reference_sql="SELECT COUNT(i.interaction_id) AS interaction_count FROM interactions i INNER JOIN campaigns c ON i.campaign_id = c.campaign_id WHERE c.campaign_type = 'Retention';",
        reference_tables="interactions, campaigns",
        reference_fields="interactions.campaign_id, interactions.interaction_id, campaigns.campaign_id, campaigns.campaign_type",
        judge_reference="The answer should state a single count of interactions attributed to Retention-type campaigns.",
        derivation_rationale="Join interactions to campaigns on campaign_id, filter to campaign_type = 'Retention', count interaction rows. Organic interactions (NULL campaign_id) are excluded by the INNER JOIN.",
    ),
    dict(
        question_id="CRM-T2-05-SEED-01",
        tier="T2",
        natural_language_question="What is the inbound/outbound interaction breakdown for active contacts?",
        reference_sql="SELECT i.direction, COUNT(*) AS direction_count FROM interactions i INNER JOIN contacts c ON i.contact_id = c.contact_id WHERE c.is_active = true GROUP BY i.direction ORDER BY direction_count DESC, i.direction ASC;",
        reference_tables="interactions, contacts",
        reference_fields="interactions.contact_id, interactions.direction, contacts.contact_id, contacts.is_active",
        judge_reference="The answer should break interactions down by direction (Inbound/Outbound) with counts, restricted to interactions belonging to active contacts.",
        derivation_rationale="Join interactions to contacts on contact_id, filter to is_active = true, group by direction and count, sort descending with direction as tie-break.",
    ),
    dict(
        question_id="CRM-T2-06-SEED-01",
        tier="T2",
        natural_language_question="Which accounts had the most meeting interactions recorded directly against them?",
        reference_sql="SELECT a.account_name, COUNT(i.interaction_id) AS interaction_count FROM accounts a INNER JOIN interactions i ON a.account_id = i.account_id WHERE i.engagement_type = 'Meeting' GROUP BY a.account_id, a.account_name ORDER BY interaction_count DESC, a.account_name ASC LIMIT 5;",
        reference_tables="accounts, interactions",
        reference_fields="accounts.account_id, accounts.account_name, interactions.account_id, interactions.engagement_type, interactions.interaction_id",
        judge_reference="The answer should list the top accounts by count of Meeting-type interactions recorded on the direct account-to-interaction relationship, highest first.",
        derivation_rationale="Join accounts to interactions on account_id, filter to engagement_type = 'Meeting', count interactions grouped by account, sort descending with account_name as tie-break, show top 5.",
    ),
    dict(
        question_id="CRM-T2-07-SEED-01",
        tier="T2",
        natural_language_question="How many email interactions came from contacts with the title IT Director?",
        reference_sql="SELECT COUNT(i.interaction_id) AS interaction_count FROM interactions i INNER JOIN contacts c ON i.contact_id = c.contact_id WHERE i.channel = 'Email' AND c.title = 'IT Director';",
        reference_tables="interactions, contacts",
        reference_fields="interactions.contact_id, interactions.channel, interactions.interaction_id, contacts.contact_id, contacts.title",
        judge_reference="The answer should state a single count of Email-channel interactions tied to contacts holding the title IT Director.",
        derivation_rationale="Join interactions to contacts on contact_id, filter to channel = 'Email' and title = 'IT Director', count interaction rows.",
    ),
    dict(
        question_id="CRM-T2-08-SEED-01",
        tier="T2",
        natural_language_question="What percentage of support cases for accounts in technology were resolved within their SLA deadline?",
        reference_sql="SELECT ROUND(COUNT(*) FILTER (WHERE s.resolved_at IS NOT NULL AND s.resolved_at <= s.sla_due_at) * 100.0 / COUNT(*), 2) AS sla_compliance_pct FROM support_cases s INNER JOIN accounts a ON s.account_id = a.account_id WHERE a.industry = 'Technology';",
        reference_tables="support_cases, accounts",
        reference_fields="support_cases.account_id, support_cases.resolved_at, support_cases.sla_due_at, accounts.account_id, accounts.industry",
        judge_reference="A single percentage (0-100, two decimals): of all support cases for Technology-industry accounts, the share resolved on or before their SLA deadline. Unresolved cases count against the rate.",
        derivation_rationale="INNER JOIN support_cases to accounts on account_id, filter to industry = 'Technology'. Within SLA = resolved_at IS NOT NULL AND resolved_at <= sla_due_at. Rate = 100.0 * within-SLA count / total, rounded to 2 places.",
    ),
    # ---------------- T3 (HELD - OI-4 dependent) ----------------
    dict(
        question_id="CRM-T3-01-SEED-01",
        tier="T3",
        natural_language_question="How many customer accounts have no contacts on record?",
        reference_sql="SELECT COUNT(*) AS accounts_without_contacts FROM accounts a LEFT JOIN contacts c ON a.account_id = c.account_id WHERE c.contact_id IS NULL;",
        reference_tables="accounts, contacts",
        reference_fields="accounts.account_id, contacts.account_id, contacts.contact_id",
        judge_reference="The answer should state a single count of accounts that have zero associated contacts.",
        derivation_rationale="Left join accounts to contacts on account_id, keep only rows where the join produced no match (contact_id IS NULL), count those accounts.",
    ),
    dict(
        question_id="CRM-T3-02-SEED-01",
        tier="T3",
        natural_language_question="How many accounts have no interactions recorded directly against them?",
        reference_sql="SELECT COUNT(*) AS accounts_without_interactions FROM accounts a LEFT JOIN interactions i ON a.account_id = i.account_id WHERE i.interaction_id IS NULL;",
        reference_tables="accounts, interactions",
        reference_fields="accounts.account_id, interactions.account_id, interactions.interaction_id",
        judge_reference="The answer should state a single count of accounts that have zero interactions on the direct account-to-interaction relationship (interactions.account_id).",
        derivation_rationale="Left join accounts to interactions on account_id, keep unmatched rows (interaction_id IS NULL), count those accounts. Interactions carry a nullable account_id, so this is the accounts with no directly-attributed engagement.",
    ),
    dict(
        question_id="CRM-T3-03-SEED-01",
        tier="T3",
        natural_language_question="How many contacts have no recorded interactions?",
        reference_sql="SELECT COUNT(*) AS contacts_without_interactions FROM contacts c LEFT JOIN interactions i ON c.contact_id = i.contact_id WHERE i.interaction_id IS NULL;",
        reference_tables="contacts, interactions",
        reference_fields="contacts.contact_id, interactions.contact_id, interactions.interaction_id",
        judge_reference="The answer should state a single count of contacts with zero recorded interactions.",
        derivation_rationale="Left join contacts to interactions on contact_id, keep unmatched rows (interaction_id IS NULL), count those contacts.",
    ),
    dict(
        question_id="CRM-T3-04-SEED-01",
        tier="T3",
        natural_language_question="How many contacts are not enrolled in any campaign?",
        reference_sql="SELECT COUNT(*) AS contacts_without_campaigns FROM contacts c LEFT JOIN contact_campaigns cc ON c.contact_id = cc.contact_id WHERE cc.campaign_id IS NULL;",
        reference_tables="contacts, contact_campaigns",
        reference_fields="contacts.contact_id, contact_campaigns.contact_id, contact_campaigns.campaign_id",
        judge_reference="The answer should state a single count of contacts with no row in the contact-campaign membership table.",
        derivation_rationale="Left join contacts to contact_campaigns on contact_id, keep unmatched rows (campaign_id IS NULL), count those contacts.",
    ),
    dict(
        question_id="CRM-T3-05-SEED-01",
        tier="T3",
        natural_language_question="How many campaigns have no attributed interactions?",
        reference_sql="SELECT COUNT(*) AS campaigns_without_interactions FROM campaigns c LEFT JOIN interactions i ON c.campaign_id = i.campaign_id WHERE i.interaction_id IS NULL;",
        reference_tables="campaigns, interactions",
        reference_fields="campaigns.campaign_id, interactions.campaign_id, interactions.interaction_id",
        judge_reference="The answer should state a single count of campaigns that have zero attributed interactions.",
        derivation_rationale="Left join campaigns to interactions on campaign_id, keep unmatched rows (interaction_id IS NULL), count those campaigns.",
    ),
    dict(
        question_id="CRM-T3-06-SEED-01",
        tier="T3",
        natural_language_question="How many completed campaigns have no attributed interactions?",
        reference_sql="SELECT COUNT(*) AS campaign_count FROM campaigns c LEFT JOIN interactions i ON c.campaign_id = i.campaign_id WHERE c.status = 'Completed' AND i.interaction_id IS NULL;",
        reference_tables="campaigns, interactions",
        reference_fields="campaigns.campaign_id, campaigns.status, interactions.campaign_id, interactions.interaction_id",
        judge_reference="A single count of campaigns whose status is Completed that have zero attributed interactions.",
        derivation_rationale="Left join campaigns to interactions on campaign_id, filter campaigns to status = 'Completed', keep unmatched rows (interaction_id IS NULL), count.",
    ),
    # ---------------- T4 (clear to ship - all GROUP BY) ----------------
    dict(
        question_id="CRM-T4-01-SEED-01",
        tier="T4",
        natural_language_question="For West-region accounts, how does engagement break down by interaction channel?",
        reference_sql="SELECT i.channel AS entity, SUM(i.engagement_points) AS engagement_points FROM accounts a INNER JOIN contacts c ON a.account_id = c.account_id INNER JOIN interactions i ON c.contact_id = i.contact_id WHERE a.region = 'West' GROUP BY i.channel ORDER BY engagement_points DESC, entity ASC;",
        reference_tables="accounts, contacts, interactions",
        reference_fields="accounts.account_id, accounts.region, contacts.account_id, contacts.contact_id, interactions.contact_id, interactions.channel, interactions.engagement_points",
        judge_reference="Interaction channels ranked by total engagement_points for interactions of contacts whose account is in the West region, highest first, with totals.",
        derivation_rationale="Join accounts -> contacts -> interactions, filter to region = 'West', GROUP BY channel, SUM(engagement_points), order desc with channel tie-break. Engagement-point outliers are included.",
    ),
    dict(
        question_id="CRM-T4-02-SEED-01",
        tier="T4",
        natural_language_question="For accounts in healthcare, how do the support cases raised by their contacts break down by priority?",
        reference_sql="SELECT s.priority AS entity, COUNT(s.case_id) AS case_count FROM accounts a INNER JOIN contacts c ON a.account_id = c.account_id INNER JOIN support_cases s ON c.contact_id = s.contact_id WHERE a.industry = 'Healthcare' GROUP BY s.priority ORDER BY case_count DESC, entity ASC;",
        reference_tables="accounts, contacts, support_cases",
        reference_fields="accounts.account_id, accounts.industry, contacts.account_id, contacts.contact_id, support_cases.contact_id, support_cases.priority, support_cases.case_id",
        judge_reference="Support-case priorities ranked by count for cases whose requesting contact belongs to a Healthcare-industry account, highest first.",
        derivation_rationale="Join accounts -> contacts -> support_cases, filter to industry = 'Healthcare', GROUP BY priority, count cases, order desc with priority tie-break. Account-level cases (NULL contact_id) are excluded.",
    ),
    dict(
        question_id="CRM-T4-03-SEED-01",
        tier="T4",
        natural_language_question="For strategic-tier accounts, what is the SLA compliance rate of the support cases raised by their contacts, by category?",
        reference_sql="SELECT s.category AS entity, ROUND(COUNT(*) FILTER (WHERE s.resolved_at IS NOT NULL AND s.resolved_at <= s.sla_due_at) * 100.0 / COUNT(*), 2) AS sla_compliance_pct FROM accounts a INNER JOIN contacts c ON a.account_id = c.account_id INNER JOIN support_cases s ON c.contact_id = s.contact_id WHERE a.customer_tier = 'Strategic' GROUP BY s.category ORDER BY entity ASC;",
        reference_tables="accounts, contacts, support_cases",
        reference_fields="accounts.account_id, accounts.customer_tier, contacts.account_id, contacts.contact_id, support_cases.contact_id, support_cases.category, support_cases.resolved_at, support_cases.sla_due_at",
        judge_reference="For each support-case category, the percentage (0-100, two decimals) of cases resolved on or before the SLA deadline, for cases whose contact belongs to a Strategic-tier account. Categories alphabetical.",
        derivation_rationale="Join accounts -> contacts -> support_cases, filter to customer_tier = 'Strategic', GROUP BY category. Within SLA = resolved_at IS NOT NULL AND resolved_at <= sla_due_at. Rate = 100.0 * within-SLA / total per category, 2 places, ordered by category.",
    ),
    dict(
        question_id="CRM-T4-05-SEED-01",
        tier="T4",
        natural_language_question="For West-region accounts, how do the support cases raised by their contacts break down by status?",
        reference_sql="SELECT s.status AS entity, COUNT(s.case_id) AS case_count FROM accounts a INNER JOIN contacts c ON a.account_id = c.account_id INNER JOIN support_cases s ON c.contact_id = s.contact_id WHERE a.region = 'West' GROUP BY s.status ORDER BY case_count DESC, entity ASC;",
        reference_tables="accounts, contacts, support_cases",
        reference_fields="accounts.account_id, accounts.region, contacts.account_id, contacts.contact_id, support_cases.contact_id, support_cases.status, support_cases.case_id",
        judge_reference="Support-case statuses ranked by count for cases whose requesting contact belongs to a West-region account, highest first.",
        derivation_rationale="Join accounts -> contacts -> support_cases, filter to region = 'West', GROUP BY status, count cases, order desc with status tie-break.",
    ),
    dict(
        question_id="CRM-T4-06-SEED-01",
        tier="T4",
        natural_language_question="For accounts in healthcare, how does engagement break down by interaction type?",
        reference_sql="SELECT i.engagement_type AS entity, SUM(i.engagement_points) AS engagement_points FROM accounts a INNER JOIN contacts c ON a.account_id = c.account_id INNER JOIN interactions i ON c.contact_id = i.contact_id WHERE a.industry = 'Healthcare' GROUP BY i.engagement_type ORDER BY engagement_points DESC, entity ASC;",
        reference_tables="accounts, contacts, interactions",
        reference_fields="accounts.account_id, accounts.industry, contacts.account_id, contacts.contact_id, interactions.contact_id, interactions.engagement_type, interactions.engagement_points",
        judge_reference="Interaction engagement types ranked by total engagement_points for interactions of contacts whose account is in the Healthcare industry, highest first.",
        derivation_rationale="Join accounts -> contacts -> interactions, filter to industry = 'Healthcare', GROUP BY engagement_type, SUM(engagement_points), order desc with engagement_type tie-break.",
    ),
    dict(
        question_id="CRM-T4-04-SEED-01",
        tier="T4",
        natural_language_question="For Retention campaigns, how does total multi-touch attribution weight break down by member status?",
        reference_sql="SELECT cc.member_status, ROUND(SUM(cc.attribution_weight), 4) AS total_attribution_weight FROM contacts c INNER JOIN contact_campaigns cc ON c.contact_id = cc.contact_id INNER JOIN campaigns ca ON cc.campaign_id = ca.campaign_id WHERE ca.campaign_type = 'Retention' GROUP BY cc.member_status ORDER BY total_attribution_weight DESC, cc.member_status ASC;",
        reference_tables="contacts, contact_campaigns, campaigns",
        reference_fields="contacts.contact_id, contact_campaigns.contact_id, contact_campaigns.campaign_id, contact_campaigns.member_status, contact_campaigns.attribution_weight, campaigns.campaign_id, campaigns.campaign_type",
        judge_reference="Member statuses ranked by summed attribution_weight for Retention-campaign memberships, four decimals, highest first. Memberships with a missing attribution_weight contribute nothing.",
        derivation_rationale="Join contacts -> contact_campaigns -> campaigns, filter to campaign_type = 'Retention', GROUP BY member_status, ROUND(SUM(attribution_weight), 4). Rows where attribution_weight IS NULL (the ~2.5% missing-value imperfection) are skipped by SUM - the declared NULL handling for this measure. Order desc with member_status tie-break.",
    ),
    # ---------------- T5 (clear to ship) ----------------
    dict(
        question_id="CRM-T5-01-SEED-01",
        tier="T5",
        natural_language_question="Which three campaign types drove the most customer engagement?",
        reference_sql="SELECT c.campaign_type, SUM(i.engagement_points) AS total_points FROM campaigns c INNER JOIN interactions i ON c.campaign_id = i.campaign_id GROUP BY c.campaign_type ORDER BY total_points DESC, c.campaign_type ASC LIMIT 3;",
        reference_tables="campaigns, interactions",
        reference_fields="campaigns.campaign_id, campaigns.campaign_type, interactions.campaign_id, interactions.engagement_points",
        judge_reference="The answer should name exactly three campaign types ranked by total engagement_points from campaign-attributed interactions, highest to lowest, with their totals.",
        derivation_rationale="Join campaigns to interactions on campaign_id, sum engagement_points grouped by campaign_type, sort descending with campaign_type as tie-break, take the top three.",
    ),
    dict(
        question_id="CRM-T5-02-SEED-01",
        tier="T5",
        natural_language_question="Which accounts have the largest open support backlog right now?",
        reference_sql="SELECT a.account_name, COUNT(s.case_id) AS open_cases FROM accounts a INNER JOIN support_cases s ON a.account_id = s.account_id WHERE s.status IN ('New', 'InProgress', 'PendingCustomer') GROUP BY a.account_id, a.account_name ORDER BY open_cases DESC, a.account_name ASC LIMIT 5;",
        reference_tables="accounts, support_cases",
        reference_fields="accounts.account_id, accounts.account_name, support_cases.account_id, support_cases.status, support_cases.case_id",
        judge_reference="The answer should list the top accounts by number of unresolved support cases (status New, InProgress, or PendingCustomer), highest first.",
        derivation_rationale="Join accounts to support_cases on account_id, filter to unresolved statuses, count cases grouped by account, sort descending with account_name as tie-break, show top 5.",
    ),
    dict(
        question_id="CRM-T5-03-SEED-01",
        tier="T5",
        natural_language_question="Which accounts look at risk from an open support backlog and no recent engagement?",
        reference_sql=(
            "WITH open_cases AS (SELECT account_id, COUNT(*) AS open_case_count FROM support_cases "
            "WHERE status IN ('New', 'InProgress', 'PendingCustomer') GROUP BY account_id), "
            "recent_engagement AS (SELECT c.account_id, COUNT(*) AS recent_interaction_count "
            "FROM interactions i INNER JOIN contacts c ON i.contact_id = c.contact_id "
            f"WHERE i.interaction_at >= DATE '{RECENT_SINCE}' AND c.account_id IS NOT NULL GROUP BY c.account_id) "
            "SELECT a.account_name, COALESCE(oc.open_case_count, 0) AS open_case_count, "
            "COALESCE(re.recent_interaction_count, 0) AS recent_interaction_count "
            "FROM accounts a LEFT JOIN open_cases oc ON a.account_id = oc.account_id "
            "LEFT JOIN recent_engagement re ON a.account_id = re.account_id "
            "WHERE COALESCE(oc.open_case_count, 0) >= 5 AND COALESCE(re.recent_interaction_count, 0) = 0 "
            "ORDER BY open_case_count DESC, a.account_name ASC LIMIT 5;"
        ),
        reference_tables="accounts, support_cases, interactions, contacts",
        reference_fields="accounts.account_id, accounts.account_name, support_cases.account_id, support_cases.status, interactions.contact_id, interactions.interaction_at, contacts.contact_id, contacts.account_id",
        judge_reference="An at-risk list: accounts with at least five unresolved support cases and zero interactions (through their contacts) in the 60 days before the reference date, with the two supporting numbers.",
        derivation_rationale=f"Count unresolved support cases per account, and interactions since {RECENT_SINCE} (60 days before the fixed today {FIXED_TODAY}) attributed through the contact's account. Left join both to accounts, keep accounts with >= 5 open cases and 0 recent interactions, order by open-case count descending with account_name tie-break, top 5.",
    ),
    dict(
        question_id="CRM-T5-04-SEED-01",
        tier="T5",
        natural_language_question="How did customer engagement change from Q1 2026 to Q2 2026?",
        reference_sql=(
            "WITH q AS (SELECT "
            f"SUM(CASE WHEN interaction_at >= DATE '{Q1_START}' AND interaction_at < DATE '{Q1_END}' THEN engagement_points ELSE 0 END) AS prev_q, "
            f"SUM(CASE WHEN interaction_at >= DATE '{Q2_START}' AND interaction_at < DATE '{Q2_END}' THEN engagement_points ELSE 0 END) AS this_q "
            "FROM interactions) "
            "SELECT prev_q AS prev_quarter_points, this_q AS this_quarter_points, "
            "this_q - prev_q AS absolute_change, "
            "ROUND(CASE WHEN prev_q = 0 THEN NULL ELSE (this_q - prev_q) / CAST(prev_q AS DOUBLE) * 100 END, 2) AS pct_change, "
            "CASE WHEN this_q > prev_q THEN 'up' WHEN this_q < prev_q THEN 'down' ELSE 'flat' END AS direction "
            "FROM q;"
        ),
        reference_tables="interactions",
        reference_fields="interactions.interaction_at, interactions.engagement_points",
        judge_reference="Total engagement_points for Q1 2026 and Q2 2026, the absolute and percentage change, and the direction. Q1 and Q2 are the two most recent complete calendar quarters relative to 2026-08-01.",
        derivation_rationale=f"Q1 2026 = {Q1_START}..{Q1_END}, Q2 2026 = {Q2_START}..{Q2_END} - the two most recent COMPLETE quarters (data ends 2026-07-28, so Q3 is excluded). Sum engagement_points in each window over all interactions, then compute absolute and percentage change and direction.",
    ),
    dict(
        question_id="CRM-T5-05-SEED-01",
        tier="T5",
        natural_language_question="Which region had the best customer-engagement trend from Q1 2026 to Q2 2026?",
        reference_sql=(
            "WITH by_region AS (SELECT a.region, "
            f"SUM(CASE WHEN i.interaction_at >= DATE '{Q1_START}' AND i.interaction_at < DATE '{Q1_END}' THEN i.engagement_points ELSE 0 END) AS prev_q, "
            f"SUM(CASE WHEN i.interaction_at >= DATE '{Q2_START}' AND i.interaction_at < DATE '{Q2_END}' THEN i.engagement_points ELSE 0 END) AS this_q "
            "FROM accounts a "
            "INNER JOIN contacts c ON a.account_id = c.account_id "
            "INNER JOIN interactions i ON c.contact_id = i.contact_id "
            "WHERE a.region IS NOT NULL GROUP BY a.region) "
            "SELECT region, prev_q AS prev_quarter_points, this_q AS this_quarter_points, "
            "ROUND(CASE WHEN prev_q = 0 THEN NULL ELSE (this_q - prev_q) / CAST(prev_q AS DOUBLE) * 100 END, 2) AS pct_change "
            "FROM by_region ORDER BY pct_change DESC NULLS LAST, region ASC LIMIT 1;"
        ),
        reference_tables="accounts, contacts, interactions",
        reference_fields="accounts.account_id, accounts.region, contacts.account_id, contacts.contact_id, interactions.contact_id, interactions.engagement_points, interactions.interaction_at",
        judge_reference="The single region with the highest percentage change in engagement_points from Q1 2026 to Q2 2026 - the largest gain, or (if every region declined) the smallest decline. Regions with zero Q1 engagement are excluded.",
        derivation_rationale=f"Q1 2026 = {Q1_START}..{Q1_END}, Q2 2026 = {Q2_START}..{Q2_END}. Join accounts -> contacts -> interactions, sum engagement_points per region per window, pct_change = (this_q - prev_q) / prev_q * 100, regions with zero Q1 sorted last, take the region with the highest pct_change (this does NOT assume growth).",
    ),
]


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fields})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=["dev", "full"], default="dev")
    profile = ap.parse_args().profile

    manifest = json.loads((BASE / "dataset" / f"manifest_{profile}.json").read_text())
    dataset_version = manifest["dataset_version"]

    con = connect_typed(BASE / "dataset" / f"crm_{profile}.duckdb")
    build_stamp.require_fresh_db(con, manifest, BASE, profile)  # before touching output

    # Build in memory; publish only if every fixture verified.
    fixtures, companion, blocked, log_entries = [], [], [], []
    for p in PAIRS:
        fam = p["question_id"].rsplit("-", 2)[0].replace("CRM-", "")
        res = verify.run(con, p["reference_sql"])
        log_entries.append(
            (
                p["question_id"],
                verify.log_payload(
                    question_id=p["question_id"],
                    tier=p["tier"],
                    dataset_version=dataset_version,
                    profile=profile,
                    sql=p["reference_sql"],
                    result=res,
                    domain=manifest["domain"],
                    kind="seed_fixture",
                    family=fam,
                ),
            )
        )
        if res.status != "ok":
            blocked.append((p["question_id"], f"{res.status}: {res.error}"))
            continue
        contract_row = {k: p.get(k, "") for k in CONTRACT_FIELDS}
        contract_row["expected_answer"] = res.answer
        fixtures.append(contract_row)
        companion.append(
            {
                "question_id": p["question_id"],
                "tier": p["tier"],
                "family": fam,
                "result_hash": res.result_hash,
                "natural_language_question": p["natural_language_question"],
            }
        )

    if blocked:
        raise SystemExit(
            "BLOCKED fixtures (nothing written):\n"
            + "\n".join(f"  {qid}: {why}" for qid, why in blocked)
        )

    out_dir = BASE / "fixtures" / profile
    if out_dir.exists():
        shutil.rmtree(out_dir)
    log_dir = out_dir / "verification_logs"
    for name, payload in log_entries:
        verify.write_payload(log_dir, name, payload)
    write_csv(out_dir / "crm_seed_fixtures.csv", CONTRACT_FIELDS, fixtures)
    write_csv(out_dir / "crm_seed_fixtures_companion.csv", COMPANION_FIELDS, companion)
    print(f"[{profile}] {len(fixtures)} seed fixtures (review only, NOT part of the 160)")


if __name__ == "__main__":
    main()
