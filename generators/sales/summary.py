"""Human-readable Sales generation summaries for the root CLI."""

from __future__ import annotations

from typing import Any

from generators.sales.imperfections import SalesImperfectionInjector


def print_relationship_summary(tables: dict[str, Any]) -> None:
    """Print Sales physical and analytical relationship coverage."""

    leads = tables["leads"]
    deals = tables["deals"]
    products = tables["products"]
    quotations = tables["quotations"]
    targets = tables["targets"]
    lead_ids = set(leads["lead_id"])
    deal_ids = set(deals["deal_id"])
    product_ids = set(products["product_id"])
    multi_product_deals = int(
        quotations.groupby("deal_id")["product_id"].nunique().ge(2).sum()
    )
    multi_deal_products = int(
        quotations.groupby("product_id")["deal_id"].nunique().ge(2).sum()
    )
    unmatched_leads = len(lead_ids - set(deals["lead_id"]))
    target_representatives = set(targets["rep_name"])

    print("relationship_checks:")
    print(f"  deals.lead_id valid: {set(deals['lead_id']).issubset(lead_ids)}")
    print(
        "  quotations.deal_id valid: "
        f"{set(quotations['deal_id']).issubset(deal_ids)}"
    )
    print(
        "  quotations.product_id valid: "
        f"{set(quotations['product_id']).issubset(product_ids)}"
    )
    print(f"  unmatched_leads: {unmatched_leads}")
    print(f"  deals_with_multiple_products: {multi_product_deals}")
    print(f"  products_in_multiple_deals: {multi_deal_products}")
    print(
        "  deal representatives covered by targets: "
        f"{set(deals['rep_name']).issubset(target_representatives)}"
    )


def print_distribution_summary(tables: dict[str, Any]) -> None:
    """Print concise Sales distribution measurements."""

    deals = tables["deals"]
    quotations = tables["quotations"]
    targets = tables["targets"]
    deal_amounts = deals["deal_amount"].astype(float)
    quota_amounts = targets["quota_amount"].astype(float)
    quotation_counts = quotations.groupby("deal_id").size()

    print("distribution_checks:")
    print(f"  deal_amount_min: {deal_amounts.min():.2f}")
    print(f"  deal_amount_max: {deal_amounts.max():.2f}")
    print(f"  deal_amount_mean: {deal_amounts.mean():.2f}")
    print(f"  quotation_lines_per_deal_mean: {quotation_counts.mean():.2f}")
    print(f"  quotation_lines_per_deal_max: {quotation_counts.max()}")
    print(f"  quota_amount_min: {quota_amounts.min():.2f}")
    print(f"  quota_amount_max: {quota_amounts.max():.2f}")
    print(f"  distinct_deal_close_dates: {deals['close_date'].nunique()}")


def print_imperfection_summary(
    tables: dict[str, Any],
    injector: SalesImperfectionInjector,
) -> None:
    """Print concise Sales imperfection measurements."""

    quotations = tables["quotations"]
    products = tables["products"]
    deals = tables["deals"]
    duplicate_rows = len(quotations) - injector.generator.row_count("quotations")
    outlier_target = injector.sales_config["imperfection_targets"][
        "deal_amount_outliers"
    ]
    outlier_minimum = int(outlier_target["minimum_value"])
    boundary_target = injector.sales_config["imperfection_targets"][
        "product_created_boundary_timestamps"
    ]
    expected_boundaries = {
        f"{value}T{boundary_target['timestamp_time']}"
        for value in injector.config["boundary_dates"]
    }

    print("imperfection_checks:")
    print(f"  quotation_near_duplicates: {duplicate_rows}")
    print(
        "  products.list_price_empty: "
        f"{int(products['list_price'].astype(str).eq('').sum())}"
    )
    outliers = int((deals["deal_amount"].astype(float) >= outlier_minimum).sum())
    print(f"  deal_amount_outliers_ge_{outlier_minimum}: {outliers}")
    boundary_count = int(
        products["created_at"].astype(str).isin(expected_boundaries).sum()
    )
    print(f"  product_created_boundary_timestamps: {boundary_count}")


__all__ = [
    "print_distribution_summary",
    "print_imperfection_summary",
    "print_relationship_summary",
]
