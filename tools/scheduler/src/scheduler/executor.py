# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Executor Lambda handler for the LMI scheduler.

Invoked by EventBridge Scheduler on a cron schedule. Performs the actual
scale-down or scale-up operation for a specific capacity provider based on
the registered strategy (Shape or Pause).

Expected event payload (set when the EventBridge schedule is created):
    {
        "capacityProviderName": "my-cp",
        "action": "scale-down" | "scale-up"
    }

Orchestration flow:
    scale-down: read registration → check state → select strategy →
        snapshot fleet via DescribeInstances → execute scale-down →
        save snapshot + fleet baseline → transition state
    scale-up:   read registration → check state → read snapshot →
        select strategy → execute scale-up → calculate savings →
        save cost event → delete snapshot → transition state
"""

import json
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer

from scheduler.reporting.pricing import get_on_demand_price
from scheduler.state.repository import StateRepository
from scheduler.strategies.pause import PauseStrategy
from scheduler.strategies.shape import ShapeStrategy

logger = Logger()
tracer = Tracer()

LMI_MANAGEMENT_FEE_MULTIPLIER = 1.15

CP_TAG_KEY = "aws:lambda:capacity-provider"

_repo = None
_lambda_client = None
_ec2_client = None


def _init():
    """Lazily initialise module-level AWS clients and dependencies.

    Called once on first handler invocation. Separated from module scope
    so that unit tests can import the module without triggering real
    boto3 connections or requiring environment variables.
    """
    global _repo, _lambda_client, _ec2_client

    if _repo is not None:
        return

    _lambda_client = boto3.client("lambda")
    _ec2_client = boto3.client("ec2")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ.get("STATE_TABLE_NAME", "lmi-scheduler"))

    _repo = StateRepository(table)


def _get_strategy(strategy_name: str, registration: dict[str, Any] | None = None):
    """Instantiate the appropriate scaling strategy.

    Args:
        strategy_name: 'shape' or 'pause'.
        registration: Optional registration record, used to pass target
            min/max execution environments to the shape strategy.

    Returns:
        ScalingStrategy: The strategy instance.

    Raises:
        ValueError: If the strategy name is not recognised.
    """
    if strategy_name == "shape":
        target_min = int(registration.get("min_execution_environments", 1)) if registration else 1
        target_max = int(registration.get("max_execution_environments", 1)) if registration else 1
        return ShapeStrategy(_lambda_client, target_min=target_min, target_max=target_max)
    elif strategy_name == "pause":
        return PauseStrategy(_lambda_client)
    else:
        raise ValueError(f"Unknown strategy: '{strategy_name}'")


def _get_cp_arn(cp_name: str) -> str | None:
    """Look up the full capacity provider ARN from its name.

    Calls lambda:GetCapacityProvider to resolve the name to an ARN,
    which is needed for the EC2 tag filter.

    Args:
        cp_name: Short name of the capacity provider.

    Returns:
        The full ARN string, or None if the lookup fails.
    """
    try:
        response = _lambda_client.get_capacity_provider(CapacityProviderName=cp_name)
        return response.get("CapacityProvider", {}).get("CapacityProviderArn")
    except Exception:
        logger.warning("Failed to get ARN for capacity provider '%s'", cp_name, exc_info=True)
        return None


def _snapshot_fleet(cp_name: str) -> dict[str, Any] | None:
    """Snapshot the running EC2 fleet for a capacity provider.

    Calls ec2:DescribeInstances filtered by the aws:lambda:capacity-provider
    tag and instance-state-name=running to capture the fleet composition.

    Args:
        cp_name: Name of the capacity provider.

    Returns:
        Dict with instance_types (type→count), total_instances, and
        captured_at. Returns None if the lookup fails.
    """
    cp_arn = _get_cp_arn(cp_name)
    if not cp_arn:
        logger.warning("Cannot snapshot fleet without CP ARN for '%s'", cp_name)
        return None

    try:
        instance_types = Counter()
        paginator = _ec2_client.get_paginator("describe_instances")
        for page in paginator.paginate(
            Filters=[
                {"Name": f"tag:{CP_TAG_KEY}", "Values": [cp_arn]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ],
        ):
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    itype = instance.get("InstanceType", "unknown")
                    instance_types[itype] += 1

        total = sum(instance_types.values())
        snapshot = {
            "instance_types": dict(instance_types),
            "total_instances": total,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info("Fleet snapshot for '%s': %d instances %s", cp_name, total, dict(instance_types))
        return snapshot

    except Exception:
        logger.warning("Failed to snapshot fleet for '%s'", cp_name, exc_info=True)
        return None


def _calculate_and_store_savings(cp_name: str, registration: dict[str, Any]) -> dict[str, Any] | None:
    """Calculate cost savings for the scale-down period and store the event.

    Reads the fleet snapshot from scale-down time, calculates duration,
    looks up pricing via the Price List API, and stores a COST event.

    Args:
        cp_name: Name of the capacity provider.
        registration: The registration record from DynamoDB.

    Returns:
        dict with savings details, or None if calculation failed/unavailable.
    """
    try:
        scale_down_time = registration.get("updated_at")
        if not scale_down_time:
            logger.warning("No updated_at on registration for '%s', skipping savings", cp_name)
            return None

        now = datetime.now(timezone.utc)
        scale_down_dt = datetime.fromisoformat(scale_down_time)
        duration_hours = (now - scale_down_dt).total_seconds() / 3600

        baseline = _repo.get_cost_baseline(cp_name)
        if not baseline:
            logger.info("No fleet snapshot for '%s', skipping savings", cp_name)
            return None

        instance_types = baseline.get("instance_types", {})
        if not instance_types:
            logger.info("Empty fleet snapshot for '%s', skipping savings", cp_name)
            return None

        breakdown = {}
        total_savings = 0.0
        unknown_types = []

        for instance_type, count in instance_types.items():
            hourly_rate = get_on_demand_price(instance_type)
            if hourly_rate is None:
                logger.warning("No pricing for '%s' — excluded from savings", instance_type)
                unknown_types.append(instance_type)
                continue

            raw_cost = int(count) * hourly_rate * duration_hours
            with_fee = raw_cost * LMI_MANAGEMENT_FEE_MULTIPLIER

            breakdown[instance_type] = {
                "instance_count": count,
                "hourly_rate": hourly_rate,
                "raw_savings_usd": round(raw_cost, 4),
                "with_lmi_fee_usd": round(with_fee, 4),
            }
            total_savings += with_fee

        strategy_name = registration.get("strategy", "unknown")
        savings = {
            "estimated_savings_usd": round(total_savings, 2),
            "duration_hours": round(duration_hours, 2),
            "breakdown": breakdown,
            "unknown_instance_types": unknown_types,
        }

        cost_event = {
            "scale_down_time": scale_down_time,
            "scale_up_time": now.isoformat(),
            "duration_hours": str(round(duration_hours, 2)),
            "fleet_snapshot": baseline,
            "estimated_savings_usd": str(round(total_savings, 2)),
            "source": "fleet-snapshot",
            "strategy": strategy_name,
        }
        if strategy_name == "shape":
            cost_event["caveat"] = (
                "Shape strategy adjusts execution environment bounds rather than "
                "fully stopping instances. Some instances may have remained running. "
                "Actual savings may be lower than estimated."
            )

        _repo.save_cost_event(cp_name, cost_event)
        logger.info("Savings for '%s': $%.2f over %.2f hours", cp_name, total_savings, duration_hours)

        return savings

    except Exception:
        logger.warning("Failed to calculate savings for '%s'", cp_name, exc_info=True)
        return None


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def handler(event, context):
    """EventBridge Scheduler target handler.

    Validates the incoming payload and delegates to the appropriate
    scale-down or scale-up function. Exceptions are caught and returned
    as a plain JSON body (error, errorType) so the Lambda response does
    not include a stack trace.

    Args:
        event: EventBridge Scheduler payload with capacityProviderName and action.
        context: Lambda context object.

    Returns:
        dict: Result of the scaling operation, or {"error": str, "errorType": str} on failure.
    """
    try:
        cp_name = event.get("capacityProviderName")
        action = event.get("action")

        if not cp_name or action not in ("scale-down", "scale-up"):
            return {
                "error": f"Invalid payload: capacityProviderName={cp_name}, action={action}",
                "errorType": "ValueError",
            }

        _init()

        logger.info("Executing %s for capacity provider %s", action, cp_name)

        if action == "scale-down":
            return _scale_down(cp_name)
        return _scale_up(cp_name)

    except Exception as e:
        logger.exception("Executor failed")
        return {"error": str(e), "errorType": type(e).__name__}


@tracer.capture_method
def _scale_down(cp_name: str) -> dict[str, Any]:
    """Execute the scale-down operation for a capacity provider.

    Flow:
        1. Read registration from DynamoDB
        2. Check state is 'active' (idempotent: return early if already scaled-down)
        3. Select strategy based on registration
        4. Snapshot running fleet via DescribeInstances
        5. Execute strategy.scale_down() to get snapshot of original values
        6. Save snapshot and fleet baseline to DynamoDB
        7. Transition registration state to 'scaled-down' (conditional write)

    Args:
        cp_name: Name of the capacity provider to scale down.

    Returns:
        dict: Summary including status, strategy, and whether fleet was captured.
    """
    registration = _repo.get_registration(cp_name)
    if not registration:
        logger.error("No registration found for '%s'", cp_name)
        raise ValueError(f"No registration found for capacity provider '{cp_name}'")

    if registration["state"] == "scaled-down":
        logger.info("Already scaled-down for '%s', skipping", cp_name)
        return {"status": "already-scaled-down", "capacity_provider": cp_name}

    strategy_name = registration["strategy"]
    strategy = _get_strategy(strategy_name, registration)

    fleet = _snapshot_fleet(cp_name)

    logger.info("Executing %s scale-down for '%s'", strategy_name, cp_name)
    snapshot = strategy.scale_down(cp_name)

    _repo.save_snapshot(cp_name, snapshot)
    logger.info("Snapshot saved for '%s'", cp_name)

    if fleet:
        try:
            _repo.save_cost_baseline(cp_name, fleet)
            logger.info("Fleet snapshot stored for '%s'", cp_name)
        except Exception:
            logger.warning("Failed to store fleet snapshot for '%s'", cp_name, exc_info=True)

    transitioned = _repo.update_registration_state(cp_name, "active", "scaled-down")

    if not transitioned:
        logger.info("Concurrent scale-down won for '%s', this invocation is a no-op", cp_name)
        return {"status": "concurrent-noop", "capacity_provider": cp_name}

    return {
        "status": "scaled-down",
        "capacity_provider": cp_name,
        "strategy": strategy_name,
        "baseline_captured": fleet is not None,
        "scale_down_time": datetime.now(timezone.utc).isoformat(),
    }


@tracer.capture_method
def _scale_up(cp_name: str) -> dict[str, Any]:
    """Execute the scale-up operation for a capacity provider.

    Flow:
        1. Read registration from DynamoDB
        2. Check state is 'scaled-down' (idempotent: return early if already active)
        3. Read pre-scale-down snapshot from DynamoDB
        4. Select strategy based on registration
        5. Execute strategy.scale_up() to restore original values
        6. Calculate cost savings from fleet snapshot and Price List API
        7. Delete snapshot
        8. Transition registration state to 'active' (conditional write)

    Args:
        cp_name: Name of the capacity provider to scale up.

    Returns:
        dict: Summary including status, strategy, and estimated savings.
    """
    registration = _repo.get_registration(cp_name)
    if not registration:
        logger.error("No registration found for '%s'", cp_name)
        raise ValueError(f"No registration found for capacity provider '{cp_name}'")

    if registration["state"] == "active":
        logger.info("Already active for '%s', skipping", cp_name)
        return {"status": "already-active", "capacity_provider": cp_name}

    snapshot = _repo.get_snapshot(cp_name)
    if not snapshot:
        logger.error("No snapshot found for '%s', cannot restore", cp_name)
        raise ValueError(f"No snapshot found for capacity provider '{cp_name}'")

    strategy_name = registration["strategy"]
    strategy = _get_strategy(strategy_name)

    logger.info("Executing %s scale-up for '%s'", strategy_name, cp_name)
    strategy.scale_up(cp_name, snapshot)

    savings = _calculate_and_store_savings(cp_name, registration)

    _repo.delete_snapshot(cp_name)

    transitioned = _repo.update_registration_state(cp_name, "scaled-down", "active")

    if not transitioned:
        logger.info("Concurrent scale-up won for '%s', this invocation is a no-op", cp_name)
        return {"status": "concurrent-noop", "capacity_provider": cp_name}

    return {
        "status": "scaled-up",
        "capacity_provider": cp_name,
        "strategy": strategy_name,
        "estimated_savings": savings,
        "scale_up_time": datetime.now(timezone.utc).isoformat(),
    }
