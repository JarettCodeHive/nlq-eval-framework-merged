"""Upload/delete orchestration: order, idempotence, and what gets sent.

Runs against a fake StudioClient — the HTTP layer is covered in
`test_client.py`. What matters here is that the right calls happen in the right
order for a real domain laid out on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from judge.resolve import ResolutionError
from studio import upload

ACCOUNTS = "account_id,account_name\n1,Williams Ltd\n2,Moreno Smith\n"
CONTACTS = "contact_id,account_id\n10,1\n11,\n12,2\n"


class _FakeClient:
    """Records calls; answers `find_entity_id` from a seeded table."""

    def __init__(
        self,
        existing: dict[str, int] | None = None,
        batch_rows: int = 2,
        batch_bytes: int = 10**9,
        no_job_id: bool = False,
        no_status_route: bool = False,
        row_count: int | None = -1,
    ):
        self.existing = dict(existing or {})
        self.batch_rows = batch_rows
        self.batch_bytes = batch_bytes
        self.no_job_id = no_job_id
        self.no_status_route = no_status_route
        # -1 means "answer truthfully"; anything else is the lie to tell.
        self.row_count = row_count
        self.loaded_rows: dict[int, int] = {}
        self.calls: list[tuple] = []
        self._next_id = 100
        self._next_job = 300

    def find_entity_id(self, name):
        self.calls.append(("find", name))
        return self.existing.get(name)

    def create_entity(self, definition):
        self._next_id += 1
        self.calls.append(("create", definition["Name"], len(definition["Fields"])))
        self.existing[definition["Name"]] = self._next_id
        return self._next_id

    def load_rows(self, entity_id, rows):
        self.calls.append(("load", entity_id, len(rows)))
        # Recorded before the early return: withholding a job id does not mean
        # the platform failed to load the rows.
        self.loaded_rows[entity_id] = self.loaded_rows.get(entity_id, 0) + len(rows)
        if self.no_job_id:
            return None
        self._next_job += 1
        return {"ID": self._next_job, "Status": "waiting"}

    def count_rows(self, entity_id):
        self.calls.append(("count", entity_id))
        if self.row_count == -1:
            return self.loaded_rows.get(entity_id, 0)
        return self.row_count

    def wait_for_job(self, entity_id, job_id, **_kw):
        self.calls.append(("wait", entity_id, job_id))
        if self.no_status_route:
            return None
        return {"ID": job_id, "Status": "complete"}

    def delete_entity(self, entity_id):
        self.calls.append(("delete", entity_id))
        for name, value in list(self.existing.items()):
            if value == entity_id:
                del self.existing[name]

    def find_schema_context(self, entity_id):
        self.calls.append(("find_context", entity_id))
        return None

    def create_schema_context(self, entity_id, source_name, description=""):
        self.calls.append(("create_context", entity_id, source_name))


@pytest.fixture()
def crm_repo(tmp_path: Path, monkeypatch) -> Path:
    """A repo skeleton with a two-table CRM release on disk."""

    config_dir = tmp_path / "config" / "generation" / "crm"
    config_dir.mkdir(parents=True)
    (config_dir / "release.json").write_text(
        json.dumps(
            {
                "dataset_version": "dataset-v1.0.0",
                "output_paths": {"full": "release/crm/dataset-v1.0.0"},
                # Deliberately not alphabetical: parents before children.
                "table_order": ["accounts", "contacts"],
            }
        ),
        encoding="utf-8",
    )
    csv_dir = tmp_path / "release" / "crm" / "dataset-v1.0.0"
    csv_dir.mkdir(parents=True)
    (csv_dir / "accounts.csv").write_text(ACCOUNTS, encoding="utf-8")
    (csv_dir / "contacts.csv").write_text(CONTACTS, encoding="utf-8")

    monkeypatch.setattr(upload, "REPO_ROOT_OVERRIDE", tmp_path, raising=False)
    monkeypatch.setattr("judge.resolve.REPO_ROOT", tmp_path)
    monkeypatch.setattr("studio.upload.dataset_csv_dir", lambda d, p: csv_dir)
    monkeypatch.setattr(
        "studio.upload.dataset_release_config",
        lambda d, **_: json.loads((config_dir / "release.json").read_text()),
    )
    return tmp_path


def test_tables_upload_parents_before_children(crm_repo: Path) -> None:
    """Alphabetical order would put contact_campaigns ahead of contacts."""

    csv_dir = crm_repo / "release" / "crm" / "dataset-v1.0.0"

    assert upload.table_order("crm", csv_dir) == ["accounts", "contacts"]


def test_a_table_on_disk_but_absent_from_config_is_still_uploaded(
    crm_repo: Path,
) -> None:
    csv_dir = crm_repo / "release" / "crm" / "dataset-v1.0.0"
    (csv_dir / "zz_extra.csv").write_text("a\n1\n", encoding="utf-8")

    # Ordered tables first, then the unlisted one — never silently dropped.
    assert upload.table_order("crm", csv_dir) == ["accounts", "contacts", "zz_extra"]


def test_upload_creates_loads_and_attaches_context_per_table(crm_repo: Path) -> None:
    client = _FakeClient(batch_rows=2)

    outcome = upload.upload_domain("crm", "full", client=client)

    assert client.calls == [
        ("find", "accounts"),
        ("create", "accounts", 2),
        ("load", 101, 2),  # 2 rows, batch size 2
        ("wait", 101, 301),  # ...and we wait for it to land
        ("count", 101),  # ...then check the table really holds them
        ("find_context", 101),
        ("create_context", 101, "accounts.csv"),
        ("find", "contacts"),
        ("create", "contacts", 2),
        ("load", 102, 2),  # 3 rows at batch size 2 -> 2 + 1
        ("wait", 102, 302),
        ("load", 102, 1),
        ("wait", 102, 303),
        ("count", 102),
        ("find_context", 102),
        ("create_context", 102, "contacts.csv"),
    ]
    assert outcome.total_rows == 5
    assert [t.batches for t in outcome.tables] == [1, 2]


def test_an_existing_table_is_left_alone_without_replace(crm_repo: Path) -> None:
    """Re-running must not silently double-load a table."""

    client = _FakeClient(existing={"accounts": 25})

    outcome = upload.upload_domain("crm", "full", client=client)

    accounts = next(t for t in outcome.tables if t.table == "accounts")
    assert accounts.skipped
    assert "--replace" in accounts.skipped
    assert accounts.entity_id == 25
    assert ("delete", 25) not in client.calls
    assert ("create", "accounts", 2) not in client.calls
    # The other table still uploads.
    assert ("create", "contacts", 2) in client.calls


def test_replace_deletes_the_existing_table_first(crm_repo: Path) -> None:
    client = _FakeClient(existing={"accounts": 25})

    upload.upload_domain("crm", "full", client=client, replace=True)

    assert client.calls[:3] == [
        ("find", "accounts"),
        ("delete", 25),
        ("create", "accounts", 2),
    ]


def test_delete_removes_children_before_parents(crm_repo: Path) -> None:
    client = _FakeClient(existing={"accounts": 25, "contacts": 26})

    outcome = upload.delete_domain("crm", "full", client=client)

    assert client.calls == [
        ("find", "contacts"),
        ("delete", 26),
        ("find", "accounts"),
        ("delete", 25),
    ]
    assert all(t.existed for t in outcome.tables)


def test_delete_is_quiet_about_tables_that_are_not_there(crm_repo: Path) -> None:
    client = _FakeClient(existing={"accounts": 25})

    outcome = upload.delete_domain("crm", "full", client=client)

    contacts = next(t for t in outcome.tables if t.table == "contacts")
    assert contacts.skipped == "not present on the platform"
    assert ("delete", 25) in client.calls


def test_delete_only_targets_tables_this_pipeline_owns(crm_repo: Path) -> None:
    """Resolved from local CSVs, never from a listing of the whole org."""

    client = _FakeClient(existing={"accounts": 25, "someone_elses_table": 99})

    upload.delete_domain("crm", "full", client=client)

    assert ("delete", 99) not in client.calls
    assert ("find", "someone_elses_table") not in client.calls


def test_a_missing_dataset_names_the_command_that_builds_it(monkeypatch) -> None:
    monkeypatch.setattr("studio.upload.dataset_csv_dir", lambda d, p: None)

    with pytest.raises(ResolutionError) as excinfo:
        upload.upload_domain("crm", "full", client=_FakeClient())

    assert "build-dataset --domain crm --profile full" in str(excinfo.value)


def test_nullable_numeric_survives_the_round_trip(crm_repo: Path) -> None:
    """contacts.account_id is blank on row 2 — it must stay a null number."""

    sent: list = []
    client = _FakeClient(batch_rows=100)
    original = client.load_rows
    client.load_rows = lambda eid, rows: (sent.extend(rows), original(eid, rows))[1]

    upload.upload_domain("crm", "full", client=client)

    contacts_rows = sent[2:]  # accounts loaded first
    assert contacts_rows[0]["Fields"] == {"contact_id": 10, "account_id": 1}
    assert contacts_rows[1]["Fields"] == {"contact_id": 11, "account_id": None}


def test_manifest_records_the_entity_ids(crm_repo: Path, tmp_path: Path) -> None:
    """A later delete should not have to re-discover what we created."""

    outcome = upload.upload_domain("crm", "full", client=_FakeClient())

    path = upload.write_manifest(outcome, tmp_path / "platform" / "full.json")
    recorded = json.loads(path.read_text(encoding="utf-8"))

    assert recorded["domain"] == "crm"
    assert [t["table"] for t in recorded["tables"]] == ["accounts", "contacts"]
    assert all(t["entity_id"] for t in recorded["tables"])


def test_summary_reads_as_a_report(crm_repo: Path) -> None:
    outcome = upload.upload_domain("crm", "full", client=_FakeClient())

    text = upload.summarise(outcome, action="Uploaded")

    assert "Uploaded crm / full" in text
    assert "rows=5" in text


def test_each_batch_is_waited_for_before_the_next_is_sent(crm_repo: Path) -> None:
    """Async loads: firing batch N+1 blind would queue jobs and confirm nothing."""

    client = _FakeClient(batch_rows=1)

    outcome = upload.upload_domain("crm", "full", client=client)

    loads_and_waits = [c for c in client.calls if c[0] in ("load", "wait")]
    # Strictly alternating: load, wait, load, wait...
    assert [c[0] for c in loads_and_waits] == ["load", "wait"] * 5
    assert all(t.loads_confirmed for t in outcome.tables)
    assert [t.job_ids for t in outcome.tables] == [[301, 302], [303, 304, 305]]


def test_no_job_id_but_a_matching_row_count_is_still_confirmed(
    crm_repo: Path,
) -> None:
    """The row count is the stronger evidence — it proves the rows are there."""

    client = _FakeClient(no_job_id=True)

    outcome = upload.upload_domain("crm", "full", client=client)

    assert not any(c[0] == "wait" for c in client.calls)
    assert all(not t.jobs_confirmed for t in outcome.tables)  # weaker signal failed
    assert all(t.loads_confirmed for t in outcome.tables)  # ...the count carried it
    assert "UNCONFIRMED" not in upload.summarise(outcome, action="Uploaded")


def test_neither_job_status_nor_row_count_means_unconfirmed(crm_repo: Path) -> None:
    """Both signals gone: never claim the rows landed."""

    client = _FakeClient(no_status_route=True, row_count=None)

    outcome = upload.upload_domain("crm", "full", client=client)

    assert any(c[0] == "wait" for c in client.calls)
    assert all(not t.loads_confirmed for t in outcome.tables)
    summary = upload.summarise(outcome, action="Uploaded")
    assert "UNVERIFIED" in summary
    assert "Do not evaluate against these tables" in summary


def test_confirmed_loads_say_nothing_alarming(crm_repo: Path) -> None:
    outcome = upload.upload_domain("crm", "full", client=_FakeClient())

    assert "UNCONFIRMED" not in upload.summarise(outcome, action="Uploaded")


def test_job_ids_and_confirmation_reach_the_manifest(
    crm_repo: Path, tmp_path: Path
) -> None:
    outcome = upload.upload_domain("crm", "full", client=_FakeClient())

    recorded = json.loads(
        upload.write_manifest(outcome, tmp_path / "m.json").read_text(encoding="utf-8")
    )

    assert recorded["tables"][0]["job_ids"] == [301]
    assert recorded["tables"][0]["loads_confirmed"] is True


def test_row_counts_are_verified_against_the_platform(crm_repo: Path) -> None:
    outcome = upload.upload_domain("crm", "full", client=_FakeClient())

    assert [t.rows_on_platform for t in outcome.tables] == [2, 3]
    assert all(t.loads_confirmed for t in outcome.tables)
    assert "verified=2" in upload.summarise(outcome, action="Uploaded")


def test_a_short_table_is_caught_even_when_every_job_succeeded(
    crm_repo: Path,
) -> None:
    """The case job status alone cannot see: a batch that never ran."""

    client = _FakeClient(row_count=1)  # platform holds fewer rows than we sent

    outcome = upload.upload_domain("crm", "full", client=client)

    assert all(not t.loads_confirmed for t in outcome.tables)
    summary = upload.summarise(outcome, action="Uploaded")
    assert "MISMATCH: platform holds 1" in summary
    assert "Do not evaluate against these tables" in summary


def test_no_row_count_falls_back_to_job_status(crm_repo: Path) -> None:
    """Without a count, clean job statuses are the best evidence available."""

    client = _FakeClient(row_count=None)

    outcome = upload.upload_domain("crm", "full", client=client)

    assert all(t.jobs_confirmed for t in outcome.tables)
    assert all(t.loads_confirmed for t in outcome.tables)
    assert all(t.rows_on_platform is None for t in outcome.tables)
