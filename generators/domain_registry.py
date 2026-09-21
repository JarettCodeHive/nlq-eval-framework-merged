"""Runtime registry for dataset-generation domains exposed by the root CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from generators.crm.config import settings_for_profile as crm_settings_for_profile
from generators.crm.data_dictionary import CRMDataDictionaryGenerator
from generators.crm.distributions import CRMDistributionApplier
from generators.crm.export import CRMCSVExporter
from generators.crm.generator import CRMBaseEntityGenerator
from generators.crm.hashes import CRMHashComputer
from generators.crm.imperfections import CRMImperfectionInjector
from generators.crm.manifest import CRMManifestGenerator
from generators.crm.schema_sql import CRMSchemaSQLGenerator
from generators.crm.summary import (
    print_distribution_summary as crm_distribution_summary,
)
from generators.crm.summary import (
    print_imperfection_summary as crm_imperfection_summary,
)
from generators.crm.summary import (
    print_relationship_summary as crm_relationship_summary,
)
from generators.crm.validators.config import validate_crm_config
from generators.crm.validators.fk_integrity import CRMDuckDBFKValidator
from generators.crm.validators.imperfection_rates import CRMImperfectionRateValidator
from generators.crm.validators.join_paths import CRMJoinPathValidator
from generators.crm.validators.relational import CRMRelationalValidator
from generators.crm.validators.reproducibility import CRMReproducibilityValidator
from generators.crm.validators.row_caps import CRMRowCapValidator
from generators.finance.config import settings_for_profile as finance_settings_for_profile
from generators.finance.data_dictionary import FinanceDataDictionaryGenerator
from generators.finance.distributions import FinanceDistributionApplier
from generators.finance.export import FinanceCSVExporter
from generators.finance.generator import FinanceBaseEntityGenerator
from generators.finance.hashes import FinanceHashComputer
from generators.finance.imperfections import FinanceImperfectionInjector
from generators.finance.manifest import FinanceManifestGenerator
from generators.finance.schema_sql import FinanceSchemaSQLGenerator
from generators.finance.summary import (
    print_distribution_summary as finance_distribution_summary,
)
from generators.finance.summary import (
    print_imperfection_summary as finance_imperfection_summary,
)
from generators.finance.summary import (
    print_relationship_summary as finance_relationship_summary,
)
from generators.finance.validators.config import validate_finance_config
from generators.finance.validators.fk_integrity import FinanceDuckDBFKValidator
from generators.finance.validators.imperfection_rates import (
    FinanceImperfectionRateValidator,
)
from generators.finance.validators.join_paths import FinanceJoinPathValidator
from generators.finance.validators.relational import FinanceRelationalValidator
from generators.finance.validators.reproducibility import (
    FinanceReproducibilityValidator,
)
from generators.finance.validators.row_caps import FinanceRowCapValidator
from generators.sales.config import settings_for_profile as sales_settings_for_profile
from generators.sales.data_dictionary import SalesDataDictionaryGenerator
from generators.sales.distributions import SalesDistributionApplier
from generators.sales.export import SalesCSVExporter
from generators.sales.generator import SalesBaseEntityGenerator
from generators.sales.hashes import SalesHashComputer
from generators.sales.imperfections import SalesImperfectionInjector
from generators.sales.manifest import SalesManifestGenerator
from generators.sales.schema_sql import SalesSchemaSQLGenerator
from generators.sales.summary import (
    print_distribution_summary as sales_distribution_summary,
)
from generators.sales.summary import (
    print_imperfection_summary as sales_imperfection_summary,
)
from generators.sales.summary import (
    print_relationship_summary as sales_relationship_summary,
)
from generators.sales.validators.config import validate_sales_config
from generators.sales.validators.fk_integrity import SalesDuckDBFKValidator
from generators.sales.validators.imperfection_rates import (
    SalesImperfectionRateValidator,
)
from generators.sales.validators.join_paths import SalesJoinPathValidator
from generators.sales.validators.relational import SalesRelationalValidator
from generators.sales.validators.reproducibility import SalesReproducibilityValidator
from generators.sales.validators.row_caps import SalesRowCapValidator


@dataclass(frozen=True)
class DatasetDomainRuntime:
    """Factories and presentation hooks for one implemented dataset domain."""

    name: str
    display_name: str
    validate_config: Callable[[], None]
    settings_for_profile: Callable[[str], Any]
    base_generator: type
    distribution_applier: type
    imperfection_injector: type
    relational_validator: type
    csv_exporter: type
    row_cap_validator: type
    fk_validator: type
    join_path_validator: type
    imperfection_rate_validator: type
    hash_computer: type
    data_dictionary_generator: type
    schema_sql_generator: type
    reproducibility_validator: type
    manifest_generator: type
    print_relationship_summary: Callable[[dict[str, Any]], None]
    print_distribution_summary: Callable[[dict[str, Any]], None]
    print_imperfection_summary: Callable[[dict[str, Any], Any], None]


DATASET_DOMAINS = {
    "crm": DatasetDomainRuntime(
        name="crm",
        display_name="CRM",
        validate_config=validate_crm_config,
        settings_for_profile=crm_settings_for_profile,
        base_generator=CRMBaseEntityGenerator,
        distribution_applier=CRMDistributionApplier,
        imperfection_injector=CRMImperfectionInjector,
        relational_validator=CRMRelationalValidator,
        csv_exporter=CRMCSVExporter,
        row_cap_validator=CRMRowCapValidator,
        fk_validator=CRMDuckDBFKValidator,
        join_path_validator=CRMJoinPathValidator,
        imperfection_rate_validator=CRMImperfectionRateValidator,
        hash_computer=CRMHashComputer,
        data_dictionary_generator=CRMDataDictionaryGenerator,
        schema_sql_generator=CRMSchemaSQLGenerator,
        reproducibility_validator=CRMReproducibilityValidator,
        manifest_generator=CRMManifestGenerator,
        print_relationship_summary=crm_relationship_summary,
        print_distribution_summary=crm_distribution_summary,
        print_imperfection_summary=crm_imperfection_summary,
    ),
    "sales": DatasetDomainRuntime(
        name="sales",
        display_name="Sales",
        validate_config=validate_sales_config,
        settings_for_profile=sales_settings_for_profile,
        base_generator=SalesBaseEntityGenerator,
        distribution_applier=SalesDistributionApplier,
        imperfection_injector=SalesImperfectionInjector,
        relational_validator=SalesRelationalValidator,
        csv_exporter=SalesCSVExporter,
        row_cap_validator=SalesRowCapValidator,
        fk_validator=SalesDuckDBFKValidator,
        join_path_validator=SalesJoinPathValidator,
        imperfection_rate_validator=SalesImperfectionRateValidator,
        hash_computer=SalesHashComputer,
        data_dictionary_generator=SalesDataDictionaryGenerator,
        schema_sql_generator=SalesSchemaSQLGenerator,
        reproducibility_validator=SalesReproducibilityValidator,
        manifest_generator=SalesManifestGenerator,
        print_relationship_summary=sales_relationship_summary,
        print_distribution_summary=sales_distribution_summary,
        print_imperfection_summary=sales_imperfection_summary,
    ),
    "finance": DatasetDomainRuntime(
        name="finance",
        display_name="Finance",
        validate_config=validate_finance_config,
        settings_for_profile=finance_settings_for_profile,
        base_generator=FinanceBaseEntityGenerator,
        distribution_applier=FinanceDistributionApplier,
        imperfection_injector=FinanceImperfectionInjector,
        relational_validator=FinanceRelationalValidator,
        csv_exporter=FinanceCSVExporter,
        row_cap_validator=FinanceRowCapValidator,
        fk_validator=FinanceDuckDBFKValidator,
        join_path_validator=FinanceJoinPathValidator,
        imperfection_rate_validator=FinanceImperfectionRateValidator,
        hash_computer=FinanceHashComputer,
        data_dictionary_generator=FinanceDataDictionaryGenerator,
        schema_sql_generator=FinanceSchemaSQLGenerator,
        reproducibility_validator=FinanceReproducibilityValidator,
        manifest_generator=FinanceManifestGenerator,
        print_relationship_summary=finance_relationship_summary,
        print_distribution_summary=finance_distribution_summary,
        print_imperfection_summary=finance_imperfection_summary,
    ),
}


def dataset_domain(name: str) -> DatasetDomainRuntime:
    """Return one implemented dataset runtime or raise a clear CLI error."""

    try:
        return DATASET_DOMAINS[name]
    except KeyError as exc:
        supported = ", ".join(sorted(DATASET_DOMAINS))
        raise NotImplementedError(
            f"Dataset generation is not implemented for {name}; "
            f"supported domains: {supported}"
        ) from exc


__all__ = ["DATASET_DOMAINS", "DatasetDomainRuntime", "dataset_domain"]
