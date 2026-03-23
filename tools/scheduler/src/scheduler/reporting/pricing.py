# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""EC2 on-demand pricing lookup for LMI cost estimation.

Queries the AWS Price List API for live on-demand rates, with a static
fallback table for when the API is unavailable or the instance type is
not found.

The Price List API is only available in us-east-1 and ap-south-1. This
module creates a pricing client targeting us-east-1 regardless of the
Lambda's deployment region.

This is a best-effort estimate — actual costs vary by region, discount
programme (Savings Plans, Reserved Instances, Spot), and account-specific
pricing agreements. The scheduler includes a disclaimer on all reports.
"""

import json
from typing import Any

import boto3
from aws_lambda_powertools import Logger

logger = Logger(child=True)

# In-memory cache for pricing lookups (populated per Lambda invocation)
_price_cache: dict[str, float] = {}

# Lazily initialised pricing client
_pricing_client = None


def _get_pricing_client():
    """Lazily initialise the AWS Pricing client targeting us-east-1."""
    global _pricing_client
    if _pricing_client is None:
        _pricing_client = boto3.client("pricing", region_name="us-east-1")
    return _pricing_client


def get_on_demand_price(instance_type: str, region: str = "ap-southeast-2") -> float | None:
    """Look up the on-demand hourly rate for an EC2 instance type.

    Tries the AWS Price List API first, falls back to the static table.
    Results are cached in-memory for the Lambda invocation.

    Args:
        instance_type: EC2 instance type string, e.g. 'm8g.xlarge'.
        region: AWS region for pricing lookup. Defaults to ap-southeast-2.

    Returns:
        Hourly on-demand rate in USD, or None if not found.
    """
    cache_key = f"{instance_type}:{region}"
    if cache_key in _price_cache:
        return _price_cache[cache_key]

    rate = _query_price_list_api(instance_type, region)

    if rate is None:
        static = EC2_ON_DEMAND.get(instance_type)
        if static:
            rate = static["rate"]
            logger.info("Using static fallback price for '%s': $%.4f/hr", instance_type, rate)

    if rate is not None:
        _price_cache[cache_key] = rate

    return rate


def _query_price_list_api(instance_type: str, region: str) -> float | None:
    """Query the AWS Price List API for on-demand EC2 pricing.

    Filters for Linux, Shared tenancy, no pre-installed software,
    on-demand pricing in the specified region.

    Args:
        instance_type: EC2 instance type.
        region: AWS region code (mapped to Price List location name).

    Returns:
        Hourly rate in USD, or None if the query fails or returns no results.
    """
    location = _region_to_location(region)
    if not location:
        logger.warning("No Price List location mapping for region '%s'", region)
        return None

    try:
        client = _get_pricing_client()
        response = client.get_products(
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ],
            MaxResults=1,
        )

        price_list = response.get("PriceList", [])
        if not price_list:
            logger.info("No Price List results for '%s' in '%s'", instance_type, region)
            return None

        product = json.loads(price_list[0]) if isinstance(price_list[0], str) else price_list[0]
        terms = product.get("terms", {}).get("OnDemand", {})

        for term in terms.values():
            for dimension in term.get("priceDimensions", {}).values():
                price_str = dimension.get("pricePerUnit", {}).get("USD")
                if price_str:
                    rate = float(price_str)
                    if rate > 0:
                        logger.info("Price List API: '%s' in '%s' = $%.4f/hr", instance_type, region, rate)
                        return rate

        return None

    except Exception:
        logger.warning("Price List API query failed for '%s'", instance_type, exc_info=True)
        return None


def _region_to_location(region: str) -> str | None:
    """Map an AWS region code to the Price List API location name.

    The Price List API uses human-readable location names rather than
    region codes.

    Args:
        region: AWS region code, e.g. 'ap-southeast-2'.

    Returns:
        Location name string, or None if the region is not mapped.
    """
    mapping = {
        "us-east-1": "US East (N. Virginia)",
        "us-east-2": "US East (Ohio)",
        "us-west-1": "US West (N. California)",
        "us-west-2": "US West (Oregon)",
        "ap-southeast-1": "Asia Pacific (Singapore)",
        "ap-southeast-2": "Asia Pacific (Sydney)",
        "ap-northeast-1": "Asia Pacific (Tokyo)",
        "ap-northeast-2": "Asia Pacific (Seoul)",
        "ap-south-1": "Asia Pacific (Mumbai)",
        "eu-west-1": "EU (Ireland)",
        "eu-west-2": "EU (London)",
        "eu-west-3": "EU (Paris)",
        "eu-central-1": "EU (Frankfurt)",
        "eu-north-1": "EU (Stockholm)",
        "sa-east-1": "South America (Sao Paulo)",
        "ca-central-1": "Canada (Central)",
    }
    return mapping.get(region)


def get_instance_info(instance_type: str) -> dict | None:
    """Look up pricing and vCPU info for an instance type (static table).

    Retained for backward compatibility with existing cost reporting code.

    Args:
        instance_type: EC2 instance type string, e.g. 'm5.xlarge'.

    Returns:
        dict with 'vcpus' and 'rate' keys, or None if the instance type
        is not in the static pricing table.
    """
    return EC2_ON_DEMAND.get(instance_type)


# Static fallback pricing table (us-east-1 on-demand rates).
# Used when the Price List API is unavailable.
EC2_ON_DEMAND: dict[str, dict] = {
    "m5.large": {"vcpus": 2, "rate": 0.096},
    "m5.xlarge": {"vcpus": 4, "rate": 0.192},
    "m5.2xlarge": {"vcpus": 8, "rate": 0.384},
    "m5.4xlarge": {"vcpus": 16, "rate": 0.768},
    "m5.8xlarge": {"vcpus": 32, "rate": 1.536},
    "m5.12xlarge": {"vcpus": 48, "rate": 2.304},
    "m5.16xlarge": {"vcpus": 64, "rate": 3.072},
    "m5.24xlarge": {"vcpus": 96, "rate": 4.608},
    "m6i.large": {"vcpus": 2, "rate": 0.096},
    "m6i.xlarge": {"vcpus": 4, "rate": 0.192},
    "m6i.2xlarge": {"vcpus": 8, "rate": 0.384},
    "m6i.4xlarge": {"vcpus": 16, "rate": 0.768},
    "m6i.8xlarge": {"vcpus": 32, "rate": 1.536},
    "m6i.12xlarge": {"vcpus": 48, "rate": 2.304},
    "m6i.16xlarge": {"vcpus": 64, "rate": 3.072},
    "m6i.24xlarge": {"vcpus": 96, "rate": 4.608},
    "m7i.large": {"vcpus": 2, "rate": 0.1008},
    "m7i.xlarge": {"vcpus": 4, "rate": 0.2016},
    "m7i.2xlarge": {"vcpus": 8, "rate": 0.4032},
    "m7i.4xlarge": {"vcpus": 16, "rate": 0.8064},
    "m7i.8xlarge": {"vcpus": 32, "rate": 1.6128},
    "m7i.12xlarge": {"vcpus": 48, "rate": 2.4192},
    "m7i.16xlarge": {"vcpus": 64, "rate": 3.2256},
    "m7i.24xlarge": {"vcpus": 96, "rate": 4.8384},
    "c5.large": {"vcpus": 2, "rate": 0.085},
    "c5.xlarge": {"vcpus": 4, "rate": 0.170},
    "c5.2xlarge": {"vcpus": 8, "rate": 0.340},
    "c5.4xlarge": {"vcpus": 16, "rate": 0.680},
    "c5.9xlarge": {"vcpus": 36, "rate": 1.530},
    "c5.12xlarge": {"vcpus": 48, "rate": 2.040},
    "c5.18xlarge": {"vcpus": 72, "rate": 3.060},
    "c5.24xlarge": {"vcpus": 96, "rate": 4.080},
    "c6i.large": {"vcpus": 2, "rate": 0.085},
    "c6i.xlarge": {"vcpus": 4, "rate": 0.170},
    "c6i.2xlarge": {"vcpus": 8, "rate": 0.340},
    "c6i.4xlarge": {"vcpus": 16, "rate": 0.680},
    "c6i.8xlarge": {"vcpus": 32, "rate": 1.360},
    "c6i.12xlarge": {"vcpus": 48, "rate": 2.040},
    "c6i.16xlarge": {"vcpus": 64, "rate": 2.720},
    "c6i.24xlarge": {"vcpus": 96, "rate": 4.080},
    "c7i.large": {"vcpus": 2, "rate": 0.0892},
    "c7i.xlarge": {"vcpus": 4, "rate": 0.1785},
    "c7i.2xlarge": {"vcpus": 8, "rate": 0.357},
    "c7i.4xlarge": {"vcpus": 16, "rate": 0.714},
    "c7i.8xlarge": {"vcpus": 32, "rate": 1.428},
    "c7i.12xlarge": {"vcpus": 48, "rate": 2.142},
    "c7i.16xlarge": {"vcpus": 64, "rate": 2.856},
    "c7i.24xlarge": {"vcpus": 96, "rate": 4.284},
    "r5.large": {"vcpus": 2, "rate": 0.126},
    "r5.xlarge": {"vcpus": 4, "rate": 0.252},
    "r5.2xlarge": {"vcpus": 8, "rate": 0.504},
    "r5.4xlarge": {"vcpus": 16, "rate": 1.008},
    "r5.8xlarge": {"vcpus": 32, "rate": 2.016},
    "r5.12xlarge": {"vcpus": 48, "rate": 3.024},
    "r5.16xlarge": {"vcpus": 64, "rate": 4.032},
    "r5.24xlarge": {"vcpus": 96, "rate": 6.048},
    "r6i.large": {"vcpus": 2, "rate": 0.126},
    "r6i.xlarge": {"vcpus": 4, "rate": 0.252},
    "r6i.2xlarge": {"vcpus": 8, "rate": 0.504},
    "r6i.4xlarge": {"vcpus": 16, "rate": 1.008},
    "r6i.8xlarge": {"vcpus": 32, "rate": 2.016},
    "r6i.12xlarge": {"vcpus": 48, "rate": 3.024},
    "r6i.16xlarge": {"vcpus": 64, "rate": 4.032},
    "r6i.24xlarge": {"vcpus": 96, "rate": 6.048},
    "r7i.large": {"vcpus": 2, "rate": 0.1323},
    "r7i.xlarge": {"vcpus": 4, "rate": 0.2646},
    "r7i.2xlarge": {"vcpus": 8, "rate": 0.5292},
    "r7i.4xlarge": {"vcpus": 16, "rate": 1.0584},
    "r7i.8xlarge": {"vcpus": 32, "rate": 2.1168},
    "r7i.12xlarge": {"vcpus": 48, "rate": 3.1752},
    "r7i.16xlarge": {"vcpus": 64, "rate": 4.2336},
    "r7i.24xlarge": {"vcpus": 96, "rate": 6.3504},
}
