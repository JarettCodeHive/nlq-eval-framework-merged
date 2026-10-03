# Platform Data Cleanup Implementation Plan

## Objective

Make `run-pipeline` treat the Claris/Pulse dataset as temporary run data:

```bash
python main.py run-pipeline \
  --domain crm \
  --profile full \
  --version v1.0.0
```

By default, the pipeline will delete the platform entities it uploaded after the
judge stage finishes. The following opt-out will retain them for investigation:

```bash
python main.py run-pipeline \
  --domain crm \
  --profile full \
  --version v1.0.0 \
  --keep-platform-data
```

Cleanup must be ownership-based: it may delete only entity IDs created by the
current pipeline run. It must not discover targets from every entity in the
organization, and it must not delete an entity merely because its table name
matches a generated CSV.

## Scope Decisions

- Add `--keep-platform-data` for `run-pipeline`; its default is `False`.
- Preserve the standalone behavior and confirmation guard of
  `dataset-delete --yes`.
- Preserve the existing pre-upload refresh stage for compatibility. The new
  post-run cleanup will be a separate operation and will not call the broad
  domain/profile deletion path.
- Run cleanup after a successful judge pass and after a judge failure.
- If generation or Q&A construction fails before an upload starts, perform no
  cleanup because the run owns no platform entities.
- If upload fails partway through, clean up any entities already created by
  that upload attempt.
- `--keep-platform-data` disables post-run cleanup on both success and failure.
  This makes the debugging behavior predictable and deliberate.
- Retain release artifacts such as datasets, Q&A pairs, judge results,
  scorecards, logs, and the platform upload manifest. Only remote platform
  entities are deleted.

## Implementation Design

### 1. Parse and propagate the retention flag

Update the root argument parser in `main.py` with a boolean
`--keep-platform-data` option whose help text clearly states that it applies to
`run-pipeline`.

Pass the value into `run_pipeline`. Keep the default command behavior as
cleanup-enabled so existing command lines require no changes.

### 2. Track exactly what the current upload creates

Extend the upload result/session model in `studio/upload.py` so each table
records whether its entity was created by this invocation. Existing or skipped
entities must never be classified as run-owned.

Record a created entity as soon as `create_entity` succeeds, rather than only
after all row batches and schema-context operations finish. This is necessary
to recover safely from a partial upload failure.

Expose the run-owned entity IDs to the pipeline without re-resolving them by
table name. The existing upload manifest should retain these IDs and add an
explicit ownership/creation field so the audit record remains unambiguous after
cleanup.

### 3. Add an ID-based cleanup operation

Add a focused helper in `studio/upload.py` that accepts the current run's
created table/entity records and deletes those entity IDs in reverse creation
order.

The helper will:

1. Ignore skipped, pre-existing, or ID-less records.
2. Delete children before parents by reversing the upload order.
3. Report each deletion through the existing progress reporter.
4. Return a structured cleanup outcome for console reporting and tests.
5. Never fall back to name-based discovery when an ID is unavailable.

This helper is intentionally separate from `delete_domain`, whose purpose is an
explicit operator-confirmed domain cleanup based on local CSV table names.

### 4. Guarantee cleanup from pipeline orchestration

Refactor `run_pipeline` so it retains the upload session/result and performs
post-run cleanup in a `finally`-style path when `keep_platform_data` is false.

The pipeline flow will be:

```text
build dataset
  -> build Q&A
  -> pre-upload refresh
  -> upload and capture run-owned entity IDs
  -> judge
  -> delete only the captured entity IDs
```

Add cleanup as a visible pipeline stage/event so logs show whether it completed,
failed, or was skipped because `--keep-platform-data` was supplied. Update the
stage count and success output consistently.

Exception handling rules:

- A successful pipeline whose cleanup fails must exit non-zero; stale platform
  data is an operational failure.
- If judge/upload fails and cleanup succeeds, preserve and re-raise the original
  failure.
- If both the main operation and cleanup fail, preserve the original failure
  while also emitting the cleanup failure prominently.
- Never report overall pipeline success before required cleanup completes.

### 5. Keep the manifest as an audit record

Do not delete the local upload manifest after remote cleanup. Update it with
cleanup metadata such as cleanup status, timestamp, and any per-entity failure,
while preserving the originally uploaded entity IDs.

Manifest updates should be atomic so an interrupted write cannot destroy the
only ownership record. The manifest must contain no credentials.

### 6. Update documentation

Update the end-to-end pipeline section of `README.md` to document:

- Automatic cleanup as the default.
- The `--keep-platform-data` debugging example.
- The fact that only entities created by the current run are removed.
- The behavior on judge/upload failure and cleanup failure.
- The continued availability of `dataset-delete --yes` for explicit manual
  cleanup.

## Test Plan

### CLI and orchestration tests

Extend `tests/test_main_pipeline_cli.py` to verify:

- The parser accepts `--keep-platform-data` and defaults it to false.
- Default execution performs cleanup after the judge.
- The retained-data flag skips post-run cleanup.
- Cleanup runs when the judge raises an exception.
- Cleanup runs for entities created before a partial upload failure.
- No cleanup runs when failure occurs before any entity is created.
- Cleanup failure makes an otherwise successful run fail.
- A primary judge/upload error is not replaced by a secondary cleanup error.
- Pipeline stage ordering, stage totals, and final status messages are correct.

### Upload cleanup tests

Extend `studio/tests/test_upload.py` to verify:

- Only records marked as created by the current run are deleted.
- Pre-existing/skipped entities are never deleted.
- Deletion uses recorded entity IDs rather than table-name lookup.
- Entities are deleted in reverse upload order.
- Partial cleanup failures are represented in the returned outcome.
- Upload manifests distinguish created, retained, and cleaned-up entities.

### Platform CLI regression tests

Extend `tests/test_main_platform_cli.py` to verify that:

- Standalone `dataset-delete` still requires `--yes`.
- Standalone `dataset-upload` and `dataset-delete` retain their current CLI
  semantics.
- Dry-run behavior sends no platform mutations and creates no misleading
  cleanup state.

Run the focused tests first, followed by the repository's normal lint and full
test commands used by CI.

## Acceptance Criteria

- The unmodified `run-pipeline` command leaves no entities created by that run
  on the configured platform after success.
- `--keep-platform-data` leaves those entities available for debugging.
- Default cleanup also occurs after judge failure and partial upload failure.
- Cleanup targets only entity IDs proven to have been created by the current
  run.
- Local release artifacts and the audit manifest remain available.
- Cleanup failures are visible and produce a non-zero command result.
- Existing standalone platform commands remain backward compatible.
- README examples and automated tests describe and enforce the new behavior.

## Out of Scope

- Deleting unrelated entities already present in the Claris/Pulse organization.
- Automatically cleaning data intentionally retained by an earlier
  `--keep-platform-data` run.
- Changing dataset schemas, Q&A generation, judging logic, release-version
  semantics, or scorecard contents.
- Introducing domain-prefixed platform table names or separate platform
  organizations per domain.
