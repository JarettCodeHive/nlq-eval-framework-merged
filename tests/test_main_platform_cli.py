"""The two platform commands on main.py: guards, exit codes, and the manifest.

These write to and delete from a shared QA org, so the behaviour that matters
most is what they refuse to do. No network: the Studio client is faked.
"""

from __future__ import annotations

import argparse
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

        def delete_entity(self, entity_id):
            state["deleted"].append(entity_id)

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


def test_partial_upload_writes_ownership_manifest(fake_platform) -> None:
    outcome = fake_platform["Outcome"]([fake_platform["Table"]("accounts")])

    def fail_upload(*_args, on_outcome, **_kwargs):
        on_outcome(outcome)
        raise RuntimeError("load failed")

    fake_platform["set_upload"](fail_upload)

    with pytest.raises(RuntimeError, match="load failed"):
        main_module.run_dataset_upload(_args("dataset-upload"))

    assert Path(fake_platform["manifest"]).is_file()


def test_run_owned_cleanup_deletes_recorded_ids_and_keeps_manifest(
    fake_platform, capsys
) -> None:
    from studio.upload import TableOutcome, UploadOutcome

    outcome = UploadOutcome(domain="crm", profile="full", csv_dir=Path("dataset"))
    outcome.tables = [
        TableOutcome(
            table="accounts", entity_id=101, created_by_run=True
        ),
        TableOutcome(
            table="contacts", entity_id=102, created_by_run=True
        ),
    ]
    args = argparse.Namespace(
        domain="crm",
        profile="full",
        release_version="v1.0.0",
        upload_outcome=outcome,
    )

    main_module.run_uploaded_data_cleanup(args)

    assert fake_platform["deleted"] == [102, 101]
    assert Path(fake_platform["manifest"]).is_file()
    assert "2/2 run-owned platform entities" in capsys.readouterr().out


def test_upload_is_quiet_when_everything_verified(fake_platform) -> None:
    outcome = fake_platform["Outcome"]([fake_platform["Table"]("accounts")])
    fake_platform["set_upload"](lambda *a, **k: outcome)

    main_module.run_dataset_upload(_args("dataset-upload", "--profile", "full"))


def test_upload_passes_replace_through(fake_platform) -> None:
    seen = {}

    def _upload(domain, profile, *, client, replace, progress, on_outcome):
        seen["replace"] = replace
        outcome = fake_platform["Outcome"]([])
        on_outcome(outcome)
        return outcome

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


def test_no_top_level_flag_shadows_a_passthrough_subcommand_flag() -> None:
    """`judge` and `score` parse their own argv, so a top-level flag of the same
    name is consumed by main.py's parser before it ever reaches them.

    This is not hypothetical: adding `--run` for `anchors-export` silently broke
    `score --run DOMAIN=RUN_ID`, which combined the most recent runs instead of
    the pinned ones and gave no error at all.
    """

    from judge.cli import build_argparser as judge_parser
    from main import build_parser
    from scorecard.combine import build_argparser as score_parser

    def flags(parser):
        return {s for a in parser._actions for s in a.option_strings}

    # --domain/--profile are shared on purpose: main.py forwards them.
    allowed = {"-h", "--help", "--domain", "--profile"}
    top = flags(build_parser())
    for name, parser in (("judge", judge_parser()), ("score", score_parser())):
        clash = (top & flags(parser)) - allowed
        assert not clash, f"top-level flag(s) {sorted(clash)} shadow `{name}`'s own"


def test_every_command_body_resolves_its_globals() -> None:
    """A registered handler can be importable and still broken.

    `judge-build-input` shipped calling `build_judge_input` with no import for
    it: the module imported, the command appeared in `--help`, and the handler
    was a perfectly valid callable — it raised NameError the moment it ran.
    Checking that a command is registered does not check that it works, so this
    reads each handler's referenced globals and confirms the module can supply
    them, which costs nothing and catches a dead command without invoking it.
    """

    import builtins

    available = set(vars(main_module)) | set(vars(builtins))
    missing: dict[str, list[str]] = {}

    for command, handler in COMMANDS.items():
        code = getattr(handler, "__code__", None)
        if code is None:  # a partial or callable object, nothing to inspect
            continue
        unresolved = sorted(
            name
            for name in code.co_names
            # co_names also holds attribute names (`args.domain` -> "domain"),
            # which are not globals; a global reference is loaded by LOAD_GLOBAL.
            if name in _loaded_globals(code) and name not in available
        )
        if unresolved:
            missing[command] = unresolved

    assert not missing, f"commands referencing undefined globals: {missing}"


def _loaded_globals(code) -> set[str]:
    """The names a code object actually loads as globals, including nested defs."""

    import dis

    names = {
        instruction.argval
        for instruction in dis.get_instructions(code)
        if instruction.opname in {"LOAD_GLOBAL", "STORE_GLOBAL", "DELETE_GLOBAL"}
    }
    for constant in code.co_consts:
        if hasattr(constant, "co_names"):
            names |= _loaded_globals(constant)
    return names
