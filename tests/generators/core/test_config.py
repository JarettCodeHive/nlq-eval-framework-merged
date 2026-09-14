from __future__ import annotations

from pathlib import Path
import json

import pytest

from generators.core.config import is_domain_config_descriptor
from generators.core.config import load_domain_config
from generators.core.config import load_json_object
from generators.core.config import validate_domain_config_descriptor


def test_descriptor_returns_components_in_descriptor_order(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)

    paths = validate_domain_config_descriptor(descriptor, descriptor_path)

    assert tuple(paths) == tuple(descriptor["components"])
    assert [path.name for path in paths.values()] == [
        "release.json",
        "schema.json",
        "generation.json",
        "validation.json",
    ]


def test_descriptor_rejects_unsupported_version(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    descriptor["config_schema_version"] = "2.0"

    with pytest.raises(ValueError, match="Unsupported.*version"):
        validate_domain_config_descriptor(descriptor, descriptor_path)


def test_descriptor_rejects_domain_filename_mismatch(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    descriptor["domain"] = "sales"

    with pytest.raises(ValueError, match="differs from its filename"):
        validate_domain_config_descriptor(descriptor, descriptor_path)


def test_descriptor_rejects_empty_components(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    descriptor["components"] = {}

    with pytest.raises(ValueError, match="components cannot be empty"):
        validate_domain_config_descriptor(descriptor, descriptor_path)


@pytest.mark.parametrize(
    ("component_name", "configured_path", "message"),
    [
        ("release", "/tmp/release.json", "relative path"),
        ("release", "../release.json", "escapes config root"),
    ],
)
def test_descriptor_rejects_invalid_component_paths(
    tmp_path: Path,
    component_name: str,
    configured_path: str,
    message: str,
) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    descriptor["components"][component_name] = configured_path

    with pytest.raises((ValueError, FileNotFoundError), match=message):
        validate_domain_config_descriptor(descriptor, descriptor_path)


def test_descriptor_allows_custom_component_names_and_files(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    (tmp_path / "crm" / "runtime.json").write_text("{}\n", encoding="utf-8")
    descriptor["components"] = {
        "runtime": "crm/runtime.json",
        "schema": "crm/schema.json",
    }

    paths = validate_domain_config_descriptor(descriptor, descriptor_path)

    assert tuple(paths) == ("runtime", "schema")
    assert paths["runtime"].name == "runtime.json"


def test_descriptor_rejects_missing_component_file(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    (tmp_path / "crm" / "validation.json").unlink()

    with pytest.raises(FileNotFoundError, match="Missing.*validation"):
        validate_domain_config_descriptor(descriptor, descriptor_path)


def test_descriptor_allows_unreferenced_json_files(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    (tmp_path / "crm" / "extra.json").write_text("{}\n", encoding="utf-8")

    paths = validate_domain_config_descriptor(descriptor, descriptor_path)

    assert "extra.json" not in [path.name for path in paths.values()]


def test_descriptor_rejects_unexpected_top_level_key(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    descriptor["unexpected"] = True

    with pytest.raises(ValueError, match="descriptor keys are invalid"):
        validate_domain_config_descriptor(descriptor, descriptor_path)


def test_loader_returns_monolithic_config_unchanged(tmp_path: Path) -> None:
    config_path = tmp_path / "sales.json"
    expected = {"domain": "sales", "tables": {"leads": {}}}
    _write_json(config_path, expected)

    assert load_domain_config(config_path) == expected


def test_loader_composes_components_in_descriptor_order(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    component_values = {
        "release": {"domain": "crm", "table_order": ["accounts"]},
        "schema": {"tables": {"accounts": {}}, "relationships": []},
        "generation": {"domain_values": {"regions": ["North", "South"]}},
        "validation": {"join_path_requirements": [], "consistency_rules": []},
    }
    for component_name, component in component_values.items():
        _write_json(tmp_path / descriptor["components"][component_name], component)
    _write_json(descriptor_path, descriptor)

    assembled = load_domain_config(descriptor_path)

    assert list(assembled) == [
        "domain",
        "table_order",
        "tables",
        "relationships",
        "domain_values",
        "join_path_requirements",
        "consistency_rules",
    ]
    assert assembled["domain_values"]["regions"] == ["North", "South"]


def test_loader_rejects_duplicate_top_level_sections(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    _write_json(tmp_path / "crm" / "release.json", {"domain": "crm"})
    _write_json(tmp_path / "crm" / "schema.json", {"domain": "duplicate"})
    _write_json(descriptor_path, descriptor)

    with pytest.raises(ValueError, match="schema duplicates.*domain"):
        load_domain_config(descriptor_path)


def test_loader_rejects_partial_descriptor(tmp_path: Path) -> None:
    config_path = tmp_path / "crm.json"
    _write_json(config_path, {"domain": "crm", "components": {}})

    with pytest.raises(ValueError, match="descriptor keys are invalid"):
        load_domain_config(config_path)


def test_loader_reports_malformed_component_json(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    (tmp_path / "crm" / "generation.json").write_text(
        '{\n  "domain_values":\n}\n',
        encoding="utf-8",
    )
    _write_json(descriptor_path, descriptor)

    with pytest.raises(
        ValueError,
        match=r"invalid JSON at line 3, column 1: .*generation\.json",
    ):
        load_domain_config(descriptor_path)


def test_loader_rejects_non_object_component_json(tmp_path: Path) -> None:
    descriptor_path, descriptor = _valid_descriptor(tmp_path)
    (tmp_path / "crm" / "validation.json").write_text(
        "[]\n",
        encoding="utf-8",
    )
    _write_json(descriptor_path, descriptor)

    with pytest.raises(
        ValueError,
        match=r"must contain a JSON object: .*validation\.json",
    ):
        load_domain_config(descriptor_path)


@pytest.mark.parametrize("contents", ["[1, 2, 3]\n", '"crm"\n'])
def test_json_loader_requires_top_level_object(
    tmp_path: Path,
    contents: str,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a JSON object"):
        load_json_object(config_path)


def test_json_loader_reports_malformed_json_location(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{\n  "domain":\n}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON at line 3, column 1"):
        load_json_object(config_path)


def test_descriptor_detection_accepts_only_descriptor_markers() -> None:
    assert is_domain_config_descriptor({"config_schema_version": "1.0"})
    assert is_domain_config_descriptor({"components": {}})
    assert not is_domain_config_descriptor({"domain": "crm", "tables": {}})


def _valid_descriptor(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    component_root = tmp_path / "crm"
    component_root.mkdir()
    components = {
        "release": "crm/release.json",
        "schema": "crm/schema.json",
        "generation": "crm/generation.json",
        "validation": "crm/validation.json",
    }
    for relative_path in components.values():
        (tmp_path / relative_path).write_text("{}\n", encoding="utf-8")
    return tmp_path / "crm.json", {
        "config_schema_version": "1.0",
        "domain": "crm",
        "components": components,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
