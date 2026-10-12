# Studio

Claris Studio data-plane integration — uploads a released dataset to the
platform, and deletes it. This is the only module in the repository that
mutates state on the Claris/Pulse platform; everything else only reads from
it (the judge) or runs entirely locally (generators, qa_pairs, scorecard).

## Why it exists separately from `judge/`

Studio and the judge talk to the **same host, org, and credential** (both
read `PULSE_*` from `.env`), but different route families:

- The judge talks to `/api-proxy/org/{org}/ai-svc/...` — the AI service
  (chat, guidelines) — and only ever reads.
- Studio talks to plain `/org/{org}/entity` and `/org/{org}/context` — data
  management (create/load/delete tables) — reverse-engineered from the
  Studio QA web app's own network traffic, because the published Swagger
  covers only the AI service and has nothing for data management.

`STUDIO_*` environment variables exist as overrides for the unusual case
where the data plane sits somewhere else; normally one `.env` configures
both.

## Module layout

- **`client.py`** — `StudioClient`, the low-level HTTP layer. One method per
  captured request:

  ```text
  GET    /org/{org}/entity?query=<base64>   list / find by name
  POST   /org/{org}/entity                  create, returns the entity id
  POST   /org/{org}/entity/{id}/job         bulk-load rows
  DELETE /org/{org}/entity/{id}             delete
  GET    /org/{org}/context?query=<base64>  find a schema context
  POST   /org/{org}/context                 attach a schema context
  ```

  `query` is a base64-encoded Mongo-style filter document, not a query
  string. Retry, TLS, and token handling are shared with the judge's
  platform client (`judge/pulse_client.py`) so the two cannot drift apart on
  a corporate network.
- **`config.py`** — `StudioSettings`, read from the same `PULSE_*` variables
  the judge uses, with `STUDIO_*` overrides.
- **`schema.py`** — translates a release CSV into the entity definition and
  row payloads Studio expects, deliberately reproducing what the Studio UI's
  own CSV import produces (a column whose every value parses as a number
  becomes `Number`/`float64`; everything else becomes a string column).
- **`upload.py`** — the orchestration layer `main.py` calls. See
  [Upload, and the ownership-based cleanup model](#upload-and-the-ownership-based-cleanup-model)
  below.
- **`probe.py`**, **`selftest.py`**, **`livetest.py`** — manual diagnostic
  tools, not part of any automated test suite or CI gate:
  - `probe.py` is the discovery tool used while reverse-engineering the two
    endpoints that needed it most (bulk load is asynchronous and returns a
    job, not a result).
  - `selftest.py` is a live smoke test against the real platform on a
    throwaway table — the one thing unit tests (which run entirely against
    fakes in `studio/tests/`) cannot exercise.
  - `livetest.py` runs the real round trip (create → load every batch → wait
    for each job → delete) on a real release table, at the smallest scale
    that still exercises multi-batch loading.

## Upload, and the ownership-based cleanup model

`upload_domain()` takes `--domain`/`--profile` like every other pipeline
stage and resolves the CSVs through `judge.resolve.dataset_csv_dir`, so the
dataset that gets uploaded is, by construction, the same one `score --pulse
sql` replays offline and the same one the Q&A pairs were authored against.
Tables upload in the domain's configured `table_order` — parents before
children — so a partial upload leaves a referentially sensible prefix
rather than orphan rows.

Every table's `TableOutcome` records whether **this invocation** created the
entity (as opposed to it already existing and being left alone). That
ownership record, not a table-name guess, is what `cleanup_uploaded_entities()`
deletes — it only ever removes entity IDs this run is proven to have
created, in reverse creation order (children before parents), and it never
discovers targets by scanning the organization or matching on table name.
This is the mechanism behind `main.py run-pipeline`'s `--cleanup-platform-data`
flag (see the root `README.md`): the pipeline can safely upload, judge, and
then clean up only what it just created, without risking any other table in
the shared org.

`delete_domain()` is a different, intentionally broader operation — it
resolves targets from the local CSVs' table names and is used only by the
standalone, operator-confirmed `dataset-delete --yes` command. It is not
used anywhere in the automatic post-run cleanup path, on purpose: an
ownership-based cleanup and a name-based manual delete solve different
problems and must not be allowed to collapse into one code path.

`write_manifest()` records every created/retained/cleaned-up entity ID,
timestamp, and cleanup status to `release/<version>/<domain>/platform/<profile>.json`
as the audit trail — it is never deleted, even after the entities it
describes have been cleaned up remotely.

## Tests

`studio/tests/` runs entirely against fakes (`_Client`/`_Settings` doubles —
see `tests/test_main_platform_cli.py` for how `main.py`'s platform commands
stub `StudioClient`). Nothing in the automated suite talks to the real
platform; `probe.py`/`selftest.py`/`livetest.py` above are the only things
that do, and are run by hand.
