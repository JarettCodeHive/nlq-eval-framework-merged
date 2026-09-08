"""Deterministic random number helpers for dataset generation.

This module owns the reproducibility contract for generation:

- one configured root seed;
- stable named child streams;
- Faker instances seeded from the same deterministic seed family;
- no wall-clock reads in the generation path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from math import isclose
from numbers import Real
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_ROOT = PROJECT_ROOT / "config" / "generation"

_DISTRIBUTION_PARAMETERS = {
    "pareto": {"alpha", "min_amount", "max_amount", "scale"},
    "poisson": {"lambda"},
    "gaussian_mixture": {
        "components",
        "component_centers",
        "component_weights",
        "std_fraction",
    },
}


def validate_column_contracts(
    table_order: Sequence[str],
    configured_tables: Mapping[str, Any],
    column_contracts: Mapping[str, Sequence[str]],
) -> None:
    """Validate static generator columns against the domain configuration."""

    expected_order = tuple(table_order)
    contract_order = tuple(column_contracts)
    if contract_order != expected_order:
        raise ValueError(
            "Generator column-contract order differs from table_order: "
            f"expected={list(expected_order)}, actual={list(contract_order)}"
        )
    if set(configured_tables) != set(expected_order):
        raise ValueError("Configured tables differ from the generation table_order")

    for table_name in expected_order:
        columns = tuple(column_contracts[table_name])
        if not columns:
            raise ValueError(f"{table_name} column contract cannot be empty")
        if len(columns) != len(set(columns)):
            raise ValueError(f"{table_name} column contract contains duplicates")
        configured_columns = tuple(
            field["name"] for field in configured_tables[table_name]["fields"]
        )
        if columns != configured_columns:
            raise ValueError(
                f"{table_name} generator columns differ from config: "
                f"expected={list(configured_columns)}, actual={list(columns)}"
            )


def resolve_distribution_settings(
    distribution_defaults: Mapping[str, Any],
    domain_distributions: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Merge shared distribution presets with domain-specific overrides."""

    if not isinstance(distribution_defaults, Mapping) or not distribution_defaults:
        raise ValueError("distribution_defaults must be a non-empty object")
    if not isinstance(domain_distributions, Mapping) or not domain_distributions:
        raise ValueError("domain distributions must be a non-empty object")

    resolved: dict[str, dict[str, Any]] = {}
    for settings_key, domain_spec in domain_distributions.items():
        if not isinstance(domain_spec, Mapping):
            raise ValueError(f"distribution {settings_key} must be an object")
        preset_name = domain_spec.get("preset")
        if not isinstance(preset_name, str) or not preset_name:
            raise ValueError(f"distribution {settings_key} must define a preset")
        if preset_name not in distribution_defaults:
            raise ValueError(f"unknown distribution preset '{preset_name}'")

        preset = distribution_defaults[preset_name]
        if not isinstance(preset, Mapping):
            raise ValueError(f"distribution preset {preset_name} must be an object")
        algorithm = preset.get("name")
        if algorithm not in _DISTRIBUTION_PARAMETERS:
            raise ValueError(
                f"distribution preset {preset_name} uses unsupported algorithm "
                f"'{algorithm}'"
            )
        _validate_distribution_spec(f"preset {preset_name}", preset)

        override_keys = set(domain_spec) - {"preset"}
        unsupported = override_keys - _DISTRIBUTION_PARAMETERS[algorithm]
        if unsupported:
            raise ValueError(
                f"unsupported distribution override(s) {sorted(unsupported)} "
                f"for {algorithm}"
            )

        effective = deepcopy(dict(preset))
        effective.update(
            deepcopy(
                {key: value for key, value in domain_spec.items() if key != "preset"}
            )
        )
        _validate_distribution_spec(f"distribution {settings_key}", effective)
        resolved[str(settings_key)] = effective
    return resolved


def _validate_distribution_spec(context: str, spec: Mapping[str, Any]) -> None:
    """Validate one complete distribution specification by algorithm."""

    algorithm = spec.get("name")
    if algorithm not in _DISTRIBUTION_PARAMETERS:
        raise ValueError(f"{context} has unsupported distribution name '{algorithm}'")

    required = _DISTRIBUTION_PARAMETERS[algorithm]
    missing = required - set(spec)
    if missing:
        raise ValueError(f"{context} is missing parameters: {sorted(missing)}")
    unsupported = set(spec) - required - {"name"}
    if unsupported:
        raise ValueError(
            f"{context} has unsupported parameters for {algorithm}: "
            f"{sorted(unsupported)}"
        )

    if algorithm == "pareto":
        _validate_pareto_spec(context, spec)
    elif algorithm == "poisson":
        _validate_poisson_spec(context, spec)
    else:
        _validate_gaussian_mixture_spec(context, spec)


def _validate_pareto_spec(context: str, spec: Mapping[str, Any]) -> None:
    """Validate effective Pareto amount parameters."""

    alpha = _real_parameter(context, "alpha", spec["alpha"])
    minimum = _real_parameter(context, "min_amount", spec["min_amount"])
    maximum = _real_parameter(context, "max_amount", spec["max_amount"])
    scale = spec["scale"]
    if alpha <= 0:
        raise ValueError(f"{context}.alpha must be positive")
    if minimum <= 0:
        raise ValueError(f"{context}.min_amount must be positive")
    if maximum < minimum:
        raise ValueError(
            f"{context}.max_amount must be greater than or equal to min_amount"
        )
    if not isinstance(scale, int) or isinstance(scale, bool) or scale < 0:
        raise ValueError(f"{context}.scale must be a non-negative integer")


def _validate_poisson_spec(context: str, spec: Mapping[str, Any]) -> None:
    """Validate effective Poisson frequency parameters."""

    lam = _real_parameter(context, "lambda", spec["lambda"])
    if lam <= 0:
        raise ValueError(f"{context}.lambda must be positive")


def _validate_gaussian_mixture_spec(
    context: str,
    spec: Mapping[str, Any],
) -> None:
    """Validate effective Gaussian-mixture parameters."""

    components = spec["components"]
    centers = spec["component_centers"]
    weights = spec["component_weights"]
    standard_deviation = _real_parameter(
        context,
        "std_fraction",
        spec["std_fraction"],
    )
    if (
        not isinstance(components, int)
        or isinstance(components, bool)
        or components <= 0
    ):
        raise ValueError(f"{context}.components must be a positive integer")
    if not isinstance(centers, list):
        raise ValueError(f"{context}.component_centers must be a list")
    if not isinstance(weights, list):
        raise ValueError(f"{context}.component_weights must be a list")
    if len(centers) != components:
        raise ValueError(f"{context}.components must match component_centers length")
    if len(weights) != components:
        raise ValueError(f"{context}.components must match component_weights length")

    numeric_centers = [
        _real_parameter(context, "component_centers", value) for value in centers
    ]
    numeric_weights = [
        _real_parameter(context, "component_weights", value) for value in weights
    ]
    if any(not 0 <= value <= 1 for value in numeric_centers):
        raise ValueError(f"{context}.component_centers values must be between 0 and 1")
    if any(value < 0 for value in numeric_weights):
        raise ValueError(f"{context}.component_weights values cannot be negative")
    if not isclose(sum(numeric_weights), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"{context}.component_weights must sum to 1.0")
    if standard_deviation <= 0:
        raise ValueError(f"{context}.std_fraction must be positive")


def _real_parameter(context: str, name: str, value: Any) -> float:
    """Return a numeric parameter as float while rejecting booleans."""

    if not isinstance(value, Real) or isinstance(value, bool):
        raise ValueError(f"{context}.{name} must be numeric")
    return float(value)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open(encoding="utf-8") as config_file:
        raw = json.load(config_file)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return raw


def _missing_dependency(package: str) -> ImportError:
    return ImportError(
        f"Missing required dependency '{package}'. "
        "Create the root .venv and install requirements.txt before generating data."
    )


@dataclass(frozen=True)
class GenerationSettings:
    """Settings required to make a generation run reproducible."""

    domain: str
    dataset_version: str
    profile: str
    seed: int
    reference_today: date
    manifest_generated_at: str
    schema_source: Path
    output_path: Path
    table_order: tuple[str, ...]
    row_counts: dict[str, int]
    max_rows_per_table: int
    release_profile: str
    release_disallowed_profiles: tuple[str, ...] = ()
    csv_format: dict[str, Any] = field(default_factory=dict)
    distributions: dict[str, Any] = field(default_factory=dict)
    imperfections: dict[str, Any] = field(default_factory=dict)
    base_config_path: Path | None = None
    domain_config_path: Path | None = None

    @classmethod
    def from_config_files(
        cls,
        domain: str,
        profile: str,
        config_root: Path = DEFAULT_CONFIG_ROOT,
    ) -> "GenerationSettings":
        """Build settings from shared and domain generation config files."""

        base_path = config_root / "base.json"
        domain_path = config_root / f"{domain}.json"
        base_config = _load_json(base_path)
        domain_config = _load_json(domain_path)
        return cls.from_configs(
            base_config=base_config,
            domain_config=domain_config,
            profile=profile,
            base_config_path=base_path,
            domain_config_path=domain_path,
        )

    @classmethod
    def from_configs(
        cls,
        base_config: dict[str, Any],
        domain_config: dict[str, Any],
        profile: str,
        base_config_path: Path | None = None,
        domain_config_path: Path | None = None,
    ) -> "GenerationSettings":
        """Build settings from already-loaded config dictionaries."""

        required_base = {
            "seed",
            "reference_today",
            "max_rows_per_table",
            "release_profile",
            "profiles",
            "csv_format",
            "distribution_defaults",
            "imperfections",
        }
        required_domain = {
            "domain",
            "dataset_version",
            "schema_source",
            "fixed_values",
            "output_paths",
            "release_rules",
            "table_order",
            "tables",
            "distributions",
        }
        _assert_required_keys(base_config, required_base, "base config")
        _assert_required_keys(domain_config, required_domain, "domain config")

        if profile not in base_config["profiles"]:
            raise ValueError(f"Unknown generation profile: {profile}")
        if profile not in domain_config["output_paths"]:
            raise ValueError(f"Missing output path for profile: {profile}")

        table_order = tuple(str(table) for table in domain_config["table_order"])
        row_counts = _row_counts_for_profile(domain_config, table_order, profile)

        settings = cls(
            domain=str(domain_config["domain"]),
            dataset_version=str(domain_config["dataset_version"]),
            profile=str(profile),
            seed=int(base_config["seed"]),
            reference_today=date.fromisoformat(str(base_config["reference_today"])),
            manifest_generated_at=str(
                domain_config["fixed_values"]["manifest_generated_at"]
            ),
            schema_source=PROJECT_ROOT / str(domain_config["schema_source"]),
            output_path=PROJECT_ROOT / str(domain_config["output_paths"][profile]),
            table_order=table_order,
            row_counts=row_counts,
            max_rows_per_table=int(base_config["max_rows_per_table"]),
            release_profile=str(base_config["release_profile"]),
            release_disallowed_profiles=tuple(
                str(name)
                for name in domain_config["release_rules"].get(
                    "refuse_release_export_for_profiles", []
                )
            ),
            csv_format=dict(base_config["csv_format"]),
            distributions=resolve_distribution_settings(
                base_config["distribution_defaults"],
                domain_config["distributions"],
            ),
            imperfections=dict(base_config["imperfections"]),
            base_config_path=base_config_path,
            domain_config_path=domain_config_path,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Validate invariants that must hold before generation starts."""

        if not self.domain:
            raise ValueError("domain is required")
        if not self.dataset_version.startswith("dataset-v"):
            raise ValueError("dataset_version must start with 'dataset-v'")
        if not self.table_order:
            raise ValueError("table_order cannot be empty")
        if set(self.row_counts) != set(self.table_order):
            missing = set(self.table_order) - set(self.row_counts)
            extra = set(self.row_counts) - set(self.table_order)
            details = []
            if missing:
                details.append(f"missing={sorted(missing)}")
            if extra:
                details.append(f"extra={sorted(extra)}")
            raise ValueError("row_counts must match table_order: " + ", ".join(details))
        for table_name in self.table_order:
            row_count = self.row_counts[table_name]
            if row_count <= 0:
                raise ValueError(f"{table_name} row count must be positive")
            if row_count > self.max_rows_per_table:
                raise ValueError(
                    f"{table_name} has {row_count} rows; "
                    f"max is {self.max_rows_per_table}"
                )
        if self.profile in self.release_disallowed_profiles:
            if self.output_path.parts[-2:] == ("crm", self.dataset_version):
                raise ValueError(f"{self.profile} cannot target a release directory")

    @property
    def is_release_profile(self) -> bool:
        """Return whether this settings object targets release-scale output."""

        return self.profile == self.release_profile

    def metadata(self) -> dict[str, Any]:
        """Return deterministic run metadata suitable for manifest input."""

        return {
            "domain": self.domain,
            "dataset_version": self.dataset_version,
            "profile": self.profile,
            "seed": self.seed,
            "reference_today": self.reference_today.isoformat(),
            "manifest_generated_at": self.manifest_generated_at,
            "schema_source": str(self.schema_source.relative_to(PROJECT_ROOT)),
            "output_path": str(self.output_path.relative_to(PROJECT_ROOT)),
            "max_rows_per_table": self.max_rows_per_table,
            "table_order": list(self.table_order),
            "row_counts": dict(self.row_counts),
            "distributions": dict(self.distributions),
            "imperfections": dict(self.imperfections),
        }


class DeterministicGenerator:
    """Base deterministic generation context.

    Named streams are derived from the root seed and stream name using SHA-256.
    That keeps a table's random sequence stable even when other streams are
    added later.
    """

    def __init__(self, settings: GenerationSettings) -> None:
        settings.validate()
        self.settings = settings
        self._rng_cache: dict[str, Any] = {}
        self._faker_cache: dict[str, Any] = {}

    @classmethod
    def for_domain_profile(
        cls,
        domain: str,
        profile: str,
        config_root: Path = DEFAULT_CONFIG_ROOT,
    ) -> "DeterministicGenerator":
        """Create a generator context from config files."""

        return cls(GenerationSettings.from_config_files(domain, profile, config_root))

    def child_seed(self, stream_name: str) -> int:
        """Return a stable 64-bit child seed for a named stream."""

        if not stream_name:
            raise ValueError("stream_name is required")
        payload = f"{self.settings.seed}:{stream_name}".encode("utf-8")
        digest = sha256(payload).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=False)

    def rng_for(self, stream_name: str) -> Any:
        """Return a cached numpy Generator for a named deterministic stream."""

        if stream_name not in self._rng_cache:
            try:
                import numpy as np
            except ImportError as exc:
                raise _missing_dependency("numpy") from exc
            self._rng_cache[stream_name] = np.random.default_rng(
                self.child_seed(stream_name)
            )
        return self._rng_cache[stream_name]

    def faker_for(self, stream_name: str) -> Any:
        """Return a cached Faker instance for a named deterministic stream."""

        if stream_name not in self._faker_cache:
            try:
                from faker import Faker
            except ImportError as exc:
                raise _missing_dependency("Faker") from exc
            fake = Faker()
            fake.seed_instance(self.child_seed(f"faker:{stream_name}"))
            self._faker_cache[stream_name] = fake
        return self._faker_cache[stream_name]

    def row_count(self, table_name: str) -> int:
        """Return configured row count for a table."""

        try:
            return self.settings.row_counts[table_name]
        except KeyError as exc:
            raise KeyError(
                f"Unknown table for {self.settings.domain}: {table_name}"
            ) from exc

    def make_integer_ids(self, count: int, start: int = 1) -> list[int]:
        """Return stable ordered integer IDs for primary keys."""

        if count < 0:
            raise ValueError("count cannot be negative")
        return list(range(start, start + count))

    def metadata(self) -> dict[str, Any]:
        """Return deterministic generator metadata."""

        return self.settings.metadata()


def _assert_required_keys(
    config: dict[str, Any],
    required_keys: set[str],
    label: str,
) -> None:
    missing = required_keys - set(config)
    if missing:
        raise ValueError(f"Missing {label} key(s): {', '.join(sorted(missing))}")


def _row_counts_for_profile(
    domain_config: dict[str, Any],
    table_order: tuple[str, ...],
    profile: str,
) -> dict[str, int]:
    row_counts: dict[str, int] = {}
    tables = domain_config["tables"]
    for table_name in table_order:
        if table_name not in tables:
            raise ValueError(f"table_order references missing table: {table_name}")
        row_targets = tables[table_name].get("row_targets")
        if not isinstance(row_targets, dict):
            raise ValueError(f"{table_name} is missing row_targets")
        if profile not in row_targets:
            raise ValueError(f"{table_name} is missing {profile} row target")
        row_counts[table_name] = int(row_targets[profile])
    return row_counts
