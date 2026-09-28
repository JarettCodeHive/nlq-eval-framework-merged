"""Project Management release schema SQL generation."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.project_management.config import settings_for_profile
from generators.project_management.validators.config import (
    validate_project_management_config,
)


class ProjectManagementSchemaSQLGenerator:
    """Publish canonical PM DDL as a byte-identical release artifact."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "project_management":
            raise ValueError(
                "ProjectManagementSchemaSQLGenerator only supports "
                "project_management"
            )
        validate_project_management_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "ProjectManagementSchemaSQLGenerator":
        """Create a PM schema generator from validated profile configuration."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_sql(self) -> str:
        """Return the canonical validated PM DDL without transformation."""

        return self.settings.schema_source.read_text(encoding="utf-8")

    def write_release_schema(self) -> Path:
        """Atomically publish byte-identical schema.sql to an unsealed release."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Project Management release schema generation requires the full "
                f"profile; got profile={self.settings.profile}"
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
        temporary = output_path.with_suffix(".sql.tmp")
        temporary.write_bytes(source_bytes)
        if temporary.read_bytes() != source_bytes:
            temporary.unlink(missing_ok=True)
            raise OSError("Temporary PM schema does not match canonical DDL bytes")
        temporary.replace(output_path)
        return output_path


__all__ = ["ProjectManagementSchemaSQLGenerator"]
