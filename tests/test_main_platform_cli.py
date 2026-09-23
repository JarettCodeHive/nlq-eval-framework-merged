"""The two platform commands on main.py: guards, exit codes, and the manifest.

These write to and delete from a shared QA org, so the behaviour that matters
most is what they refuse to do. No network: the Studio client is faked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import main as main_module
from main import COMMANDS, build_parser


@pytest.fixture()
def fake_platform(monkeypatch, tmp_path):
    """Replace the Studio client and settings; record what would happen."""

    state = {"deleted": [], "created": [], "manifest": None}

    class _Settings:
        org_id = 4104
        redacted = {"platform": "studio", "org_id": 4104}

    class _Outcome:
        domain, profile = "crm", "full"
        csv_dir = tmp_path

        def __init__(self, tables):
            self.tables = tables

        @property
        def total_rows(self):
            return sum(t.rows for t in self.tables)

    class _Table:
        def __init__(self, table, rows=10, confirmed=True):
            self.table = table
            self.rows = rows
            self.columns = 3
            self.batches = 1
            self.entity_id = 99
            self.existed = False
            self.skipped = ""
            self.job_ids = [1]
            self.rows_on_platform = rows if confirmed else rows - 1
            self.jobs_confirmed = confirmed
            self.loads_confirmed = confirmed

    class _Client:
        def __init__(self, _settings, dry_run=False):
            self.dry_run = dry_run
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

    # Patch the class, not the module: studio.upload imports helpers from
    # studio.client, and replacing the whole module breaks those imports.
    monkeypatch.setattr("studio.client.StudioClient", _Client)
    monkeypatch.setattr("studio.config.load_studio_settings", lambda: _Settings())
    monkeypatch.setattr(
        "studio.upload.table_order", lambda d, c: ["accounts", "contacts"]
    )
    monkeypatch.setattr("studio.upload._resolve_csv_dir", lambda d, p: tmp_path)
    monkeypatch.setattr("studio.upload.describe_plan", lambda c: "PLAN")
    monkeypatch.setattr(
        "studio.upload.summarise", lambda o, action: f"{action} {len(o.tables)} table(s)"
    )

    def _write_manifest(outcome, path):
        state["manifest"] = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({"tables": len(outcome.tables)}))
        return path

    monkeypatch.setattr("studio.upload.write_manifest", _write_manifest)
    monkeypatch.setattr(main_module, "REPO_ROOT", tmp_path)

    state["Outcome"], state["Table"] = _Outcome, _Table
    state["set_upload"] = lambda fn: monkeypatch.setattr(
        "studio.upload.upload_domain", fn
    )
    state["set_delete"] = lambda fn: monkeypatch.setattr(
        "studio.upload.delete_domain", fn
    )
    return state


def _args(*argv):
    return build_parser().parse_args(list(argv))


def test_both_commands_are_registered() -> None:
    assert "dataset-upload" in COMMANDS
    assert "dataset-delete" in COMMANDS


def test_the_per_domain_evaluation_command_is_called_judge() -> None:
    """One run of one domain against the platform is `judge`.

    It was `score`, but that name is wanted for the step that combines the
    per-domain results into a single scorecard and owns the baseline — so the
    evaluation and the scorecard are no longer the same word.
    """

    assert "judge" in COMMANDS
    assert main_module.COMMANDS["judge"] is main_module.run_judge
    # `score` is reserved, not retired: nothing may claim it in the meantime.
    assert COMMANDS.get("score") is not main_module.run_judge


def test_judge_still_passes_its_own_flags_through() -> None:
    """`judge` owns ~20 flags of its own; the top level must not reject them."""

    from main import PASSTHROUGH_COMMANDS

    assert "judge" in PASSTHROUGH_COMMANDS

    args, leftover = build_parser().parse_known_args(
        ["judge", "--domain", "crm", "--profile", "full", "--allow-uncalibrated"]
    )
    assert args.command == "judge"
    assert leftover == ["--allow-uncalibrated"]


def test_upload_records_the_entity_ids(fake_platform, capsys) -> None:
    """The question that started this: are the ids stored? They must be."""

    outcome = fake_platform["Outcome"](
        [fake_platform["Table"]("accounts"), fake_platform["Table"]("contacts")]
    )
    fake_platform["set_upload"](lambda *a, **k: outcome)

    main_module.run_dataset_upload(_args("dataset-upload", "--profile", "full"))

    manifest = Path(fake_platform["manifest"])
    assert manifest.name == "full.json"
    assert manifest.parent.name == "platform"
    assert "entity ids recorded" in capsys.readouterr().out


def test_upload_exits_non_zero_when_rows_are_not_verified(
    fake_platform, capsys
) -> None:
    """A pipeline must not proceed to evaluate against a short table."""

    outcome = fake_platform["Outcome"](
        [
            fake_platform["Table"]("accounts"),
            fake_platform["Table"]("contacts", confirmed=False),
        ]
    )
    fake_platform["set_upload"](lambda *a, **k: outcome)

    with pytest.raises(SystemExit) as excinfo:
        main_module.run_dataset_upload(_args("dataset-upload", "--profile", "full"))

    assert "contacts" in str(excinfo.value)
    assert "Do not evaluate" in str(excinfo.value)


def test_upload_is_quiet_when_everything_verified(fake_platform) -> None:
    outcome = fake_platform["Outcome"]([fake_platform["Table"]("accounts")])
    fake_platform["set_upload"](lambda *a, **k: outcome)

    main_module.run_dataset_upload(_args("dataset-upload", "--profile", "full"))


def test_upload_passes_replace_through(fake_platform) -> None:
    seen = {}

    def _upload(domain, profile, *, client, replace, progress):
        seen["replace"] = replace
        return fake_platform["Outcome"]([])

    fake_platform["set_upload"](_upload)

    main_module.run_dataset_upload(_args("dataset-upload", "--replace"))
    assert seen["replace"] is True

    main_module.run_dataset_upload(_args("dataset-upload"))
    assert seen["replace"] is False


def test_dry_run_upload_prints_the_plan_and_writes_no_manifest(
    fake_platform, capsys
) -> None:
    fake_platform["set_upload"](lambda *a, **k: fake_platform["Outcome"]([]))

    main_module.run_dataset_upload(_args("dataset-upload", "--dry-run"))

    out = capsys.readouterr().out
    assert "PLAN" in out
    assert "nothing was sent" in out
    assert fake_platform["manifest"] is None


def test_delete_refuses_without_yes(fake_platform, capsys) -> None:
    """Deleting from a shared org should take a deliberate act."""

    fake_platform["set_delete"](lambda *a, **k: fake_platform["Outcome"]([]))

    with pytest.raises(SystemExit) as excinfo:
        main_module.run_dataset_delete(_args("dataset-delete"))

    assert "--yes" in str(excinfo.value)
    # ...and it showed what it would have deleted, so the decision is informed.
    out = capsys.readouterr().out
    assert "about to DELETE 2 table(s)" in out
    assert "accounts" in out and "contacts" in out


def test_delete_proceeds_with_yes(fake_platform, capsys) -> None:
    called = {}

    def _delete(*_a, **_k):
        called["yes"] = True
        return fake_platform["Outcome"]([])

    fake_platform["set_delete"](_delete)

    main_module.run_dataset_delete(_args("dataset-delete", "--yes"))

    assert called["yes"] is True
    assert "Deleted" in capsys.readouterr().out


def test_dry_run_delete_needs_no_confirmation(fake_platform, capsys) -> None:
    """Nothing is destroyed, so nothing needs confirming."""

    fake_platform["set_delete"](lambda *a, **k: fake_platform["Outcome"]([]))

    main_module.run_dataset_delete(_args("dataset-delete", "--dry-run"))

    assert "nothing was sent" in capsys.readouterr().out
