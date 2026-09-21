"""Controlled imperfection injection for distributed Sales data."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from generators.core.base import DeterministicGenerator
from generators.core.imperfections import count_from_pct
from generators.core.imperfections import inject_boundary_values
from generators.core.imperfections import inject_fixed_scale_outliers
from generators.core.imperfections import inject_nulls
from generators.core.progress import ProgressReporter
from generators.sales.config import load_sales_config
from generators.sales.config import settings_for_profile
from generators.sales.distributions import SalesDistributionApplier
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.distributed_tables import (
    validate_sales_distributed_tables,
)
from generators.sales.validators.imperfect_tables import (
    validate_sales_imperfect_tables,
)


class SalesImperfectionInjector:
    """Inject deterministic defects into otherwise valid Sales tables.

    The stage begins with distributed Sales data and introduces the four
    controlled imperfection classes required by the dataset contract:

    - missing catalog prices in ``products``;
    - near-duplicate quotation lines with new primary keys;
    - fixed-scale deal-amount outliers;
    - fixed boundary timestamps in ``products.created_at``.

    Physical foreign keys and required identifiers are never nullified. Quote
    unit prices remain captured values even when their product's current list
    price becomes missing.
    """

    def __init__(
        self,
        generator: DeterministicGenerator,
        progress: ProgressReporter | None = None,
    ) -> None:
        if generator.settings.domain != "sales":
            raise ValueError("SalesImperfectionInjector only supports sales")
        validate_sales_config()
        self.generator = generator
        self.settings = generator.settings
        self.config = self.settings.imperfections
        self.sales_config = load_sales_config()
        self.progress = progress

    @classmethod
    def for_profile(
        cls,
        profile: str,
        progress: ProgressReporter | None = None,
    ) -> "SalesImperfectionInjector":
        """Create an injector from the validated Sales profile config."""

        return cls(
            DeterministicGenerator(settings_for_profile(profile)),
            progress=progress,
        )

    def generate_imperfect_tables(self) -> dict[str, Any]:
        """Regenerate distributed Sales tables and inject all imperfections."""

        distributed = SalesDistributionApplier(
            self.generator,
            progress=self.progress,
        ).generate_distributed_tables()
        return self.apply_to_tables(distributed)

    def apply_to_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Return imperfect deep copies of valid distributed Sales tables."""

        self._report("Injecting controlled Sales imperfections")
        validate_sales_distributed_tables(self.generator, tables)
        imperfect = {
            table_name: table.copy(deep=True) for table_name, table in tables.items()
        }

        imperfect["products"] = inject_nulls(
            imperfect["products"],
            "list_price",
            self.generator.rng_for("imperfections:sales:products:list_price_nulls"),
            float(self.config["null_pct"]),
        )
        self._report("Injected missing product list prices")

        imperfect["quotations"] = self._inject_near_duplicate_quotations(
            imperfect["quotations"]
        )
        self._report("Injected near-duplicate quotation lines")

        outlier_target = self.sales_config["imperfection_targets"][
            "deal_amount_outliers"
        ]
        imperfect["deals"] = inject_fixed_scale_outliers(
            imperfect["deals"],
            "deal_amount",
            self.generator.rng_for("imperfections:sales:deals:amount_outliers"),
            float(self.config["outlier_pct"]),
            min_outlier=int(outlier_target["minimum_value"]),
            max_outlier=int(outlier_target["maximum_value"]),
            scale=int(outlier_target["scale"]),
        )
        self._report("Injected deal-amount outliers")

        boundary_target = self.sales_config["imperfection_targets"][
            "product_created_boundary_timestamps"
        ]
        boundary_time = str(boundary_target["timestamp_time"])
        boundary_timestamps = [
            f"{value}T{boundary_time}" for value in self.config["boundary_dates"]
        ]
        imperfect["products"] = inject_boundary_values(
            imperfect["products"],
            "created_at",
            boundary_timestamps,
        )
        self._report("Injected product boundary timestamps")
        self._report("Validating imperfect Sales tables")
        validate_sales_imperfect_tables(
            self.generator,
            imperfect,
            source_tables=tables,
        )
        self._report("Sales imperfection injection complete")
        return imperfect

    def _inject_near_duplicate_quotations(self, quotations: Any) -> Any:
        """Append quote-line near-duplicates with fresh primary keys.

        Each appended row retains the source deal, product, and quote number,
        but receives a small deterministic quantity and discount variation.
        This preserves the logical duplicate while avoiding byte-identical
        copies that would provide little value to aggregation tests.
        """

        duplicate_count = count_from_pct(
            len(quotations), float(self.config["duplicate_pct"])
        )
        result = quotations.copy(deep=True)
        if duplicate_count == 0:
            return result

        rng = self.generator.rng_for("imperfections:sales:quotations:duplicates")
        sampled_positions = rng.choice(
            quotations.index.to_numpy(),
            size=duplicate_count,
            replace=False,
        )
        duplicates = (
            quotations.loc[sampled_positions].copy(deep=True).reset_index(drop=True)
        )
        start_id = int(quotations["quotation_id"].max()) + 1
        duplicates["quotation_id"] = range(start_id, start_id + duplicate_count)

        rules = self.sales_config["generation_rules"]["quotations"]
        quantity_minimum = int(rules["quantity"]["minimum"])
        quantity_maximum = int(rules["quantity"]["maximum"])
        duplicates["quantity"] = [
            _near_duplicate_integer(value, quantity_minimum, quantity_maximum)
            for value in duplicates["quantity"]
        ]
        discount = rules["discount_pct"]
        duplicates["discount_pct"] = [
            _near_duplicate_decimal(
                value,
                Decimal(str(discount["minimum"])),
                Decimal(str(discount["maximum"])),
                int(discount["scale"]),
            )
            for value in duplicates["discount_pct"]
        ]

        pd = _require_pandas()
        return pd.concat([result, duplicates], ignore_index=True)

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress.report(message)


def _near_duplicate_integer(value: Any, minimum: int, maximum: int) -> int:
    """Move an integer by one while retaining its configured range."""

    current = int(value)
    return current - 1 if current >= maximum else max(minimum, current + 1)


def _near_duplicate_decimal(
    value: Any,
    minimum: Decimal,
    maximum: Decimal,
    scale: int,
) -> str:
    """Move a fixed-scale decimal by one minor unit within its range."""

    current = Decimal(str(value))
    unit = Decimal(1).scaleb(-scale)
    changed = current - unit if current >= maximum else current + unit
    changed = min(max(changed, minimum), maximum)
    return f"{changed:.{scale}f}"


def _require_pandas() -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required for Sales imperfections") from exc
    return pd


__all__ = ["SalesImperfectionInjector"]
