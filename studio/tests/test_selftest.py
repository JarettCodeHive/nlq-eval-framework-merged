"""The live self-test must not fail on a bug of its own.

`studio/selftest.py` can only be exercised for real against the platform, which
is exactly when a typo in it is most expensive. These tests drive it end to end
against a fake transport so the script itself is known-good before it is pointed
at a real org — including the failure paths, and the guarantee that matters
most: the throwaway table is deleted even when a step blows up.
"""

from __future__ import annotations

import json

import pytest

from studio import selftest
from studio.client import StudioClient
from studio.config import StudioSettings


class _Response:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakePlatform:
    """A plausible Studio: assigns ids, remembers tables, answers lookups."""

    def __init__(self, *, create_id=901, fail_on: str | None = None):
        self.create_id = create_id
        self.fail_on = fail_on
        self.tables: dict[str, int] = {}
        self.loaded: list[list] = []
        self.requests: list[tuple[str, str]] = []

    def request(self, method, path, *, json=None, params=None, headers=None):
        self.requests.append((method, path))
        if self.fail_on and self.fail_on in path and method == self.fail_on_method:
            return _Response(500, {"error": "boom"})

        if method == "GET" and path.endswith("/entity"):
            return _Response(
                200,
                [{"Name": name, "Id": eid} for name, eid in self.tables.items()],
            )
        if method == "POST" and path.endswith("/entity"):
            self.tables[json["Name"]] = self.create_id
            return _Response(200, {"Id": self.create_id, "Name": json["Name"]})
        if method == "POST" and path.endswith("/job"):
            self.loaded.append(json)
            return _Response(200, {"JobId": "job-1", "Status": "queued"})
        if method == "DELETE":
            eid = int(path.rsplit("/", 1)[-1])
            for name, value in list(self.tables.items()):
                if value == eid:
                    del self.tables[name]
            return _Response(204)
        if method == "GET" and path.endswith("/context"):
            return _Response(200, [])
        if method == "POST" and path.endswith("/context"):
            return _Response(200, {"Id": 500})
        raise AssertionError(f"unexpected request {method} {path}")

    fail_on_method = "POST"

    def close(self):
        pass


@pytest.fixture()
def wired(monkeypatch):
    """Point selftest at a fake platform and capture what it does."""

    settings = StudioSettings(
        base_url="https://api-qa.platform.claris.com",
        auth_token="fake-token-without-exp-claim",
        org_id=4104,
        max_retries=0,
        backoff_base_s=0.001,
    )
    monkeypatch.setattr(selftest, "load_studio_settings", lambda: settings)

    def _make(platform):
        def factory(_settings):
            return StudioClient(
                settings, client=platform, token_provider=lambda: "fresh"
            )

        monkeypatch.setattr(selftest, "StudioClient", factory)
        return platform

    return _make


def test_the_happy_path_runs_clean_and_cleans_up(wired, capsys) -> None:
    platform = wired(_FakePlatform())

    exit_code = selftest.run()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "entity id = 901" in out
    assert "confirmed gone" in out
    # The throwaway table is gone, and nothing else was created.
    assert platform.tables == {}


def test_it_loads_the_rows_it_says_it_loads(wired) -> None:
    platform = wired(_FakePlatform())

    selftest.run()

    assert len(platform.loaded) == 1
    rows = platform.loaded[0]
    assert len(rows) == 3
    # A blank numeric is null, not 0 — the case that matters for nullable FKs.
    assert rows[0]["Fields"] == {
        "selftest_id": 1,
        "label": "first",
        "amount": 10,
        "blank": None,
    }
    assert rows[2]["Fields"]["amount"] is None


def test_it_only_ever_touches_the_throwaway_table(wired) -> None:
    """The guarantee the whole design rests on."""

    platform = wired(_FakePlatform())
    platform.tables["accounts"] = 25  # a real table, already in the org

    selftest.run()

    deletes = [p for m, p in platform.requests if m == "DELETE"]
    assert deletes == ["/org/4104/entity/901"]
    assert platform.tables == {"accounts": 25}  # untouched


def test_a_create_response_without_an_id_fails_loudly(wired, capsys) -> None:
    class _NoId(_FakePlatform):
        def request(self, method, path, **kw):
            if method == "POST" and path.endswith("/entity"):
                self.requests.append((method, path))
                return _Response(200, {"status": "created"})
            return super().request(method, path, **kw)

    wired(_NoId())

    assert selftest.run() == 1
    out = capsys.readouterr().out
    assert "no id found in the create response" in out
    assert "_extract_id" in out  # tells you exactly what to change


def test_the_table_is_deleted_even_when_a_later_step_fails(wired, capsys) -> None:
    """A crash mid-test must not leave a table behind in a shared org."""

    class _LoadFails(_FakePlatform):
        def request(self, method, path, **kw):
            if path.endswith("/job"):
                self.requests.append((method, path))
                return _Response(403, {"error": "nope"})
            return super().request(method, path, **kw)

    platform = wired(_LoadFails())

    assert selftest.run() == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "cleaning up" in out
    assert platform.tables == {}  # cleaned up despite the failure


def test_a_leftover_table_from_a_previous_run_is_cleared_first(wired, capsys) -> None:
    platform = wired(_FakePlatform())
    platform.tables[selftest.TEST_TABLE] = 777

    assert selftest.run() == 0
    assert "found entity 777 — deleting it first" in capsys.readouterr().out


def test_keep_leaves_the_table_for_inspection(wired, capsys) -> None:
    platform = wired(_FakePlatform())

    assert selftest.run(keep=True) == 0

    out = capsys.readouterr().out
    assert "left in place" in out
    assert platform.tables == {selftest.TEST_TABLE: 901}


def test_an_async_load_response_is_surfaced(wired, capsys) -> None:
    """A JobId in the reply would mean the pipeline must poll before evaluating."""

    wired(_FakePlatform())

    selftest.run()

    out = capsys.readouterr().out
    assert "JobId" in out
    assert "ASYNCHRONOUS" in out


def test_missing_credentials_exit_2_without_calling_anything(monkeypatch) -> None:
    from studio.config import MissingStudioCredentials

    def _boom():
        raise MissingStudioCredentials("Missing Studio credentials: PULSE_ORG_ID")

    monkeypatch.setattr(selftest, "load_studio_settings", _boom)

    assert selftest.run() == 2
