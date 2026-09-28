from __future__ import annotations

import pytest

from generators.logistics.export import LogisticsCSVExporter
from generators.logistics.imperfections import LogisticsImperfectionInjector
from generators.logistics.summary import print_distribution_summary
from generators.logistics.summary import print_imperfection_summary
from generators.logistics.summary import print_relationship_summary


@pytest.fixture(scope="module")
def stages() -> dict:
    return LogisticsCSVExporter.for_profile("dev").generate_validated_stages()


def test_relationship_summary_reports_fk_and_many_to_many_coverage(
    stages: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_relationship_summary(stages["base"])

    output = capsys.readouterr().out
    assert "shipments.order_id valid: True" in output
    assert "shipments.carrier_id valid: True" in output
    assert "inventory.warehouse_id valid: True" in output
    assert "orders_with_multiple_carriers:" in output
    assert "carriers_with_multiple_orders:" in output


def test_distribution_and_imperfection_summaries(
    stages: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_distribution_summary(stages["distributed"])
    distribution_output = capsys.readouterr().out
    assert "order_total_mean:" in distribution_output
    assert "shipments_per_shipped_order_mean:" in distribution_output
    assert "distinct_order_dates:" in distribution_output

    injector = LogisticsImperfectionInjector.for_profile("dev")
    print_imperfection_summary(stages["imperfect"], injector)
    imperfection_output = capsys.readouterr().out
    assert "shipment_near_duplicates: 15" in imperfection_output
    assert "orders.warehouse_id_empty: 25" in imperfection_output
    assert "orders.warehouse_id_declared_orphans: 10" in imperfection_output
    assert "order_boundary_dates: 4" in imperfection_output
