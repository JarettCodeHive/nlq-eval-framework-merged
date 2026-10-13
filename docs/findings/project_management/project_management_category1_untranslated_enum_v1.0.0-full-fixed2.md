# Project Management — Category 1: Multi-Word Status/Location Phrase Not Matched (22 questions)

22 questions ask about a status or location using spaced-out, natural
wording ("in progress status," "on hold projects," "Asia Pacific
location"), and get back a flat `0` even though the real count is in the
thousands.

## What's happening

The data stores these values as single compact words:
`tasks.status` → `NotStarted`, `InProgress`, `Blocked`, `Completed`;
`projects.status` → `OnHold`, `Active`, `Planning`, `Cancelled`,
`Completed`; `resources.location` → `AsiaPacific`, `NorthAmerica`,
`LatinAmerica`, `Europe`, `Remote`. The generated filter instead uses the
spaced, human-readable form:

- **`PM-T1-03-12`** — "How many tasks are currently in in progress
  status?" Filter used: `status ILIKE 'in progress'`. Stored value:
  `InProgress`. Result: `0`. Expected: `3,408`.
- **`PM-T2-09-35`** — "How many task assignments involve resources in the
  Asia Pacific location?" Filter used: `location ILIKE '%Asia Pacific%'`.
  Stored value: `AsiaPacific`. Result: `0`. Expected: `6,548` — the
  response itself notes "This could mean there are genuinely no Asia
  Pacific resources... or it could reflect how location data is
  recorded," correctly suspecting the real cause.

Single-word values (`Active`, `Completed`, `Cancelled`, `Planning`) aren't
affected — only the multi-word, CamelCase-stored ones.

## Instances

Status-filter: `PM-T1-03-12`, `PM-T1-03-13`, `PM-T2-03-12`, `PM-T2-03-13`,
`PM-T2-05-22`, `PM-T2-06-26`, `PM-T2-06-27`, `PM-T3-01-04`, `PM-T3-03-14`,
`PM-T3-04-19`, `PM-T4-01-03`, `PM-T4-01-04`, `PM-T4-05-22`, `PM-T4-06-26`,
`PM-T4-06-27`, `PM-T5-01-04`, `PM-T5-06-20`, `PM-T5-07-23`, `PM-T5-07-24`.

Location-filter: `PM-T2-09-35`, `PM-T2-09-37`, `PM-T3-09-31`.

## Suggested fix

Match against the real distinct values a status/location column contains
rather than a spaced-out guess — e.g. a space/case-insensitive comparison
as a fallback when an exact match returns nothing.
