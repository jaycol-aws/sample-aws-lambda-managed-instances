# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Cost reporting for the LMI scheduler.

Calculates estimated cost savings at scale-up time using EC2 fleet snapshots
captured at scale-down and on-demand pricing from the AWS Price List API.

Savings calculation:
    For each instance type in the fleet snapshot:
        savings += instance_count * hourly_rate * hours_down
    total_savings = sum(savings) * LMI_MANAGEMENT_FEE_MULTIPLIER

Limitations:
    - Pricing is based on on-demand rates; Savings Plans / RIs are not
      accounted for.
    - Shape strategy may leave some instances running; savings are an
      upper-bound estimate.
"""

from typing import Any

from aws_lambda_powertools import Logger

from scheduler.reporting.pricing import get_on_demand_price

logger = Logger(child=True)

DISCLAIMER = (
    "Estimates are based on EC2 on-demand pricing plus the 15% LMI management fee. "
    "Actual savings may differ for accounts using Savings Plans, Reserved Instances, "
    "or other discount programs."
)

LMI_MANAGEMENT_FEE_MULTIPLIER = 1.15


def calculate_savings(
    baseline: dict[str, Any], duration_hours: float
) -> dict[str, Any]:
    """Calculate estimated cost savings for a completed down period.

    For each instance type in the fleet snapshot, looks up the on-demand
    hourly rate via the Price List API and calculates the savings based on
    the number of instances and duration of the down period.

    Args:
        baseline: Fleet snapshot dict from _snapshot_fleet(), containing
            'instance_types' mapping type to count (e.g. {"m8g.xlarge": 3}).
        duration_hours: Number of hours the CP was in scaled-down state.

    Returns:
        dict with estimated_savings_usd, duration_hours, per-instance-type
        breakdown, unknown_instance_types list, and disclaimer.
    """
    instance_types = baseline.get("instance_types", {})
    breakdown = {}
    total_savings = 0.0
    unknown_types = []

    for instance_type, count in instance_types.items():
        count = int(count)
        hourly_rate = get_on_demand_price(instance_type)

        if hourly_rate is None:
            logger.warning(
                "Unknown instance type '%s' — excluded from savings", instance_type
            )
            unknown_types.append(instance_type)
            continue

        raw_cost = count * hourly_rate * duration_hours
        with_fee = raw_cost * LMI_MANAGEMENT_FEE_MULTIPLIER

        breakdown[instance_type] = {
            "instance_count": count,
            "hourly_rate": hourly_rate,
            "raw_savings_usd": round(raw_cost, 4),
            "with_lmi_fee_usd": round(with_fee, 4),
        }
        total_savings += with_fee

    return {
        "estimated_savings_usd": round(total_savings, 2),
        "duration_hours": round(duration_hours, 2),
        "breakdown": breakdown,
        "unknown_instance_types": unknown_types,
        "disclaimer": DISCLAIMER,
    }


def generate_cp_report(
    repo, cp_name: str, start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Generate a cost savings report for a single capacity provider.

    Aggregates all cost events stored for the CP within the given date
    range and sums the estimated savings.

    Args:
        repo: StateRepository instance.
        cp_name: Name of the capacity provider.
        start_date: ISO 8601 date string for range start (inclusive).
        end_date: ISO 8601 date string for range end (inclusive).

    Returns:
        dict with capacity_provider, total_estimated_savings_usd, event_count,
        events list, and disclaimer.
    """
    events = repo.query_cost_events(cp_name, start_date, end_date)

    total_savings = 0.0
    total_hours = 0.0
    for event in events:
        total_savings += float(event.get("estimated_savings_usd", 0))
        total_hours += float(event.get("duration_hours", 0))

    return {
        "capacity_provider": cp_name,
        "total_estimated_savings_usd": round(total_savings, 2),
        "total_hours_down": round(total_hours, 2),
        "event_count": len(events),
        "events": events,
        "disclaimer": DISCLAIMER,
    }


def generate_aggregate_report(
    repo, start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Generate an aggregate cost savings report across all capacity providers.

    Iterates over all registrations and queries cost events for each.

    Args:
        repo: StateRepository instance.
        start_date: ISO 8601 date string for range start (inclusive).
        end_date: ISO 8601 date string for range end (inclusive).

    Returns:
        dict with total_estimated_savings_usd, capacity_providers breakdown,
        and disclaimer.
    """
    registrations = repo.list_registrations()

    total_savings = 0.0
    total_hours = 0.0
    cp_summaries = []

    for reg in registrations:
        cp_name = reg["capacity_provider_name"]
        cp_report = generate_cp_report(repo, cp_name, start_date, end_date)

        total_savings += cp_report["total_estimated_savings_usd"]
        total_hours += cp_report["total_hours_down"]

        cp_summaries.append({
            "capacity_provider": cp_name,
            "estimated_savings_usd": cp_report["total_estimated_savings_usd"],
            "hours_down": cp_report["total_hours_down"],
            "event_count": cp_report["event_count"],
        })

    return {
        "total_estimated_savings_usd": round(total_savings, 2),
        "total_hours_down": round(total_hours, 2),
        "capacity_providers": cp_summaries,
        "disclaimer": DISCLAIMER,
    }
