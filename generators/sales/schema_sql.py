"""Sales release schema SQL generation."""

from __future__ import annotations

from pathlib import Path

from generators.core.base import DeterministicGenerator
from generators.sales.config import settings_for_profile
from generators.sales.validators.config import validate_sales_config


class SalesSchemaSQLGenerator:
    """Publish canonical Sales DDL as a byte-identical release artifact."""

    def __init__(self, generator: DeterministicGenerator) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesSchemaSQLGenerator only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings

    @classmethod
    def for_profile(cls, profile: str) -> "SalesSchemaSQLGenerator":
        """Create a Sales schema generator from validated profile config."""

        return cls(DeterministicGenerator(settings_for_profile(profile)))

    def generate_sql(self) -> str:
        """Return the canonical Sales DDL without transformation."""

        return self.settings.schema_source.read_text(encoding="utf-8")

    def write_release_schema(self) -> Path:
        """Atomically publish byte-identical schema.sql to an unsealed release."""

        if not self.settings.is_release_profile:
            raise ValueError(
                "Sales release schema generation requires the full profile; "
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
            raise OSError("Temporary Sales schema does not match canonical DDL bytes")
        temporary_path.replace(output_path)
        return output_path


__all__ = ["SalesSchemaSQLGenerator"]
