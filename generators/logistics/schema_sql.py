"""Logistics release schema SQL generation."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.logistics.config import settings_for_profile
from generators.logistics.validators.config import validate_logistics_config


class LogisticsSchemaSQLGenerator:
    """Publish canonical Logistics DDL as a byte-identical release artifact."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "logistics":
            raise ValueError("LogisticsSchemaSQLGenerator only supports logistics")
        validate_logistics_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "LogisticsSchemaSQLGenerator":
        """Create a Logistics schema generator for a validated profile."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_sql(self) -> str:
        """Return the canonical validated Logistics DDL unchanged."""

        return self.settings.schema_source.read_text(encoding="utf-8")

    def write_release_schema(self) -> Path:
        """Atomically publish byte-identical schema.sql to an unsealed release."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Logistics release schema generation requires the full profile; "
                f"got profile={self.settings.profile}"
            )
        manifest_path = self.settings.output_path / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(
                "Refusing to modify immutable release after manifest exists: "
                f"{manifest_path}"
            )

        source_bytes = self.settings.schema_source.read_bytes()
        output_path = self.settings.output_path / "schema.sql"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".sql.tmp")
        temporary_path.write_bytes(source_bytes)
        if temporary_path.read_bytes() != source_bytes:
            temporary_path.unlink(missing_ok=True)
            raise OSError(
                "Temporary Logistics schema does not match canonical DDL bytes"
            )
        temporary_path.replace(output_path)
        return output_path


__all__ = ["LogisticsSchemaSQLGenerator"]
