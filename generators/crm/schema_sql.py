"""CRM release schema SQL generation."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.crm.config import settings_for_profile
from generators.crm.validators.config import validate_crm_config


class CRMSchemaSQLGenerator:
    """Generate release schema.sql from the canonical CRM DDL."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "crm":
            raise ValueError("CRMSchemaSQLGenerator only supports the crm domain")
        validate_crm_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "CRMSchemaSQLGenerator":
        """Create a CRM schema SQL generator from config files."""

        settings = settings_for_profile(profile)
        return cls(DeterministicGenerator(settings))

    def generate_sql(self) -> str:
        """Return canonical CRM DDL SQL text."""

        return self.settings.schema_source.read_text(encoding="utf-8")

    def write_release_schema(self) -> Path:
        """Write schema.sql into the CRM release directory."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Step 18 generates release schema.sql only for the full profile; "
                f"got profile={self.settings.profile}"
            )

        manifest_path = self.settings.output_path / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(
                "Refusing to modify immutable release after manifest exists: "
                f"{manifest_path}"
            )

        output_path = self.settings.output_path / "schema.sql"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(".sql.tmp")
        tmp_path.write_text(self.generate_sql(), encoding="utf-8", newline="\n")
        tmp_path.replace(output_path)
        return output_path
