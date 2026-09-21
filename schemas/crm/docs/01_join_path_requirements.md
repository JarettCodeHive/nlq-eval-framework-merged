# CRM Join Path Requirements

Sources of truth:

- DDL: `schemas/crm/crm_ddl.sql`
- ERD: `schemas/crm/crm_er.dbml`
- Generation config entry point: `config/generation/crm.json`
- Executable join paths: `config/generation/crm/validation.json`

This specification defines the engagement-focused CRM join paths that must be
present in generated and released datasets. Sales-pipeline tables and paths are
outside the CRM domain.

## Validation Rules

- Every INNER JOIN path must return at least one row.
- Every LEFT JOIN path must return rows and include at least one unmatched row
  from its driving table.
- Every non-null foreign-key value must reference an existing parent row.
- Attributed interactions must use an existing `(contact_id, campaign_id)`
  membership.
- The `contact_campaigns` bridge must demonstrate many-to-many cardinality in
  both directions.
- Validation runs after CSV serialization by loading all files into DuckDB
  under the canonical DDL.

## Required Paths

| ID | Path | Join | Required result | Purpose |
|---|---|---|---|---|
| `crm_jp_001` | `accounts -> contacts` | LEFT | Non-empty, with accounts having no contacts | Optional account membership and account coverage |
| `crm_jp_002` | `contacts -> interactions` | LEFT | Non-empty, with contacts having no interactions | Engagement scoring and inactive-contact analysis |
| `crm_jp_003` | `accounts -> contacts -> interactions` | INNER | Non-empty | Account engagement aggregation through contacts |
| `crm_jp_004` | `contacts -> contact_campaigns -> campaigns` | INNER | Non-empty | Campaign participation and attribution |
| `crm_jp_005` | `campaigns -> interactions` | LEFT | Non-empty, with campaigns having no attributed interactions | Campaign engagement coverage |
| `crm_jp_006` | `interactions -> campaigns` | LEFT | Non-empty, with organic interactions | Organic and unattributed engagement |
| `crm_jp_007` | `accounts -> support_cases` | INNER | Non-empty | Account-level case volume and SLA metrics |
| `crm_jp_008` | `contacts -> support_cases` | LEFT | Non-empty, with contacts having no cases | Requester and account-level case analysis |
| `crm_jp_009` | `accounts -> support_cases -> contacts` | INNER | Non-empty | Account, case, and requester analysis |

## Reference SQL

These queries specify validator behavior. They are not Q&A reference queries.

```sql
-- crm_jp_001
SELECT COUNT(*)
FROM accounts
LEFT JOIN contacts
    ON accounts.account_id = contacts.account_id;

-- crm_jp_002
SELECT COUNT(*)
FROM contacts
LEFT JOIN interactions
    ON contacts.contact_id = interactions.contact_id;

-- crm_jp_003
SELECT COUNT(*)
FROM accounts
INNER JOIN contacts
    ON contacts.account_id = accounts.account_id
INNER JOIN interactions
    ON interactions.contact_id = contacts.contact_id;

-- crm_jp_004
SELECT COUNT(*)
FROM contacts
INNER JOIN contact_campaigns
    ON contact_campaigns.contact_id = contacts.contact_id
INNER JOIN campaigns
    ON contact_campaigns.campaign_id = campaigns.campaign_id;

-- crm_jp_005
SELECT COUNT(*)
FROM campaigns
LEFT JOIN interactions
    ON campaigns.campaign_id = interactions.campaign_id;

-- crm_jp_006
SELECT COUNT(*)
FROM interactions
LEFT JOIN campaigns
    ON interactions.campaign_id = campaigns.campaign_id;

-- crm_jp_007
SELECT COUNT(*)
FROM accounts
INNER JOIN support_cases
    ON support_cases.account_id = accounts.account_id;

-- crm_jp_008
SELECT COUNT(*)
FROM contacts
LEFT JOIN support_cases
    ON contacts.contact_id = support_cases.contact_id;

-- crm_jp_009
SELECT COUNT(*)
FROM accounts
INNER JOIN support_cases
    ON support_cases.account_id = accounts.account_id
INNER JOIN contacts
    ON support_cases.contact_id = contacts.contact_id;
```

## Required Unmatched Rows

The LEFT JOIN paths use these predicates:

| ID | Unmatched predicate |
|---|---|
| `crm_jp_001` | `contacts.account_id IS NULL` |
| `crm_jp_002` | `interactions.contact_id IS NULL` |
| `crm_jp_005` | `interactions.campaign_id IS NULL` |
| `crm_jp_006` | `campaigns.campaign_id IS NULL` |
| `crm_jp_008` | `support_cases.contact_id IS NULL` |

## Many-to-Many Cardinality

Both checks must return at least one group:

```sql
SELECT contact_id
FROM contact_campaigns
GROUP BY contact_id
HAVING COUNT(DISTINCT campaign_id) > 1;

SELECT campaign_id
FROM contact_campaigns
GROUP BY campaign_id
HAVING COUNT(DISTINCT contact_id) > 1;
```
