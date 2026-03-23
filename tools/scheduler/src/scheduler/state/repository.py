# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""DynamoDB state repository for the LMI scheduler.

Uses a single-table design with composite keys to store three record types:

    PK                  | SK              | Description
    --------------------|-----------------|-------------------------------------------
    CP#<cp-name>        | REG             | Registration: strategy, crons, schedule
                        |                 | ARNs, state (active/scaled-down)
    CP#<cp-name>        | SNAPSHOT        | Pre-scale-down snapshot: original scaling
                        |                 | values to restore on scale-up
    CP#<cp-name>        | COST#<ts>       | Cost event: baseline metrics, duration,
                        |                 | and estimated savings for one down period

All writes are idempotent. State transitions (active <-> scaled-down) use
DynamoDB ConditionExpression to prevent race conditions from concurrent
EventBridge Scheduler invocations.
"""

from datetime import datetime, timezone
from typing import Any

from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Attr, Key

logger = Logger(child=True)

SK_REG = "REG"
SK_SNAPSHOT = "SNAPSHOT"
SK_COST_PREFIX = "COST#"


def _pk(cp_name: str) -> str:
    """Build the partition key for a capacity provider.

    Args:
        cp_name: Name of the capacity provider.

    Returns:
        str: Partition key in the format CP#<cp-name>.
    """
    return f"CP#{cp_name}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Returns:
        str: Current timestamp, e.g. '2026-02-13T10:30:00+00:00'.
    """
    return datetime.now(timezone.utc).isoformat()


class RegistrationExistsError(Exception):
    """Raised when attempting to register a capacity provider that is already registered."""

    pass


class StateRepository:
    """Encapsulates all DynamoDB operations for the scheduler's state table."""

    def __init__(self, table_resource):
        """Initialise with a boto3 DynamoDB Table resource.

        Args:
            table_resource: boto3 DynamoDB Table resource (e.g.
                boto3.resource('dynamodb').Table('my-table')).
        """
        self._table = table_resource

    def create_registration(self, registration: dict[str, Any]) -> dict[str, Any]:
        """Store a new registration record.

        Creates an item with PK=CP#<cp-name>, SK=REG. Fails if a registration
        already exists for this capacity provider.

        Args:
            registration: Dict containing:
                - capacity_provider_name (str)
                - strategy (str): 'shape' or 'pause'
                - scale_down_cron (str): EventBridge cron expression
                - scale_up_cron (str): EventBridge cron expression
                - scale_down_schedule_arn (str)
                - scale_up_schedule_arn (str)

        Returns:
            dict: The stored registration item including generated fields.

        Raises:
            RegistrationExistsError: If a registration already exists for this CP.
        """
        cp_name = registration["capacity_provider_name"]
        now = _now_iso()

        item = {
            "PK": _pk(cp_name),
            "SK": SK_REG,
            "capacity_provider_name": cp_name,
            "strategy": registration["strategy"],
            "scale_down_cron": registration["scale_down_cron"],
            "scale_up_cron": registration["scale_up_cron"],
            "scale_down_schedule_arn": registration["scale_down_schedule_arn"],
            "scale_up_schedule_arn": registration["scale_up_schedule_arn"],
            "state": "active",
            "created_at": now,
            "updated_at": now,
        }

        try:
            self._table.put_item(
                Item=item,
                ConditionExpression=Attr("PK").not_exists(),
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            raise RegistrationExistsError(
                f"Registration already exists for capacity provider '{cp_name}'"
            )

        logger.info("Created registration for capacity provider '%s'", cp_name)
        return item

    def get_registration(self, cp_name: str) -> dict[str, Any] | None:
        """Retrieve a registration record by capacity provider name.

        Args:
            cp_name: Name of the capacity provider.

        Returns:
            dict if the registration exists, None otherwise.
        """
        response = self._table.get_item(
            Key={"PK": _pk(cp_name), "SK": SK_REG},
        )
        return response.get("Item")

    def list_registrations(self) -> list[dict[str, Any]]:
        """List all registration records.

        Scans for all items where SK=REG. For the expected number of
        registrations (tens to low hundreds), a scan with filter is acceptable.

        Returns:
            list: All registration records.
        """
        items = []
        scan_kwargs = {
            "FilterExpression": Key("SK").eq(SK_REG),
        }

        while True:
            response = self._table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))

            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        return items

    def update_registration(self, cp_name: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update mutable fields on a registration record.

        Only updates fields present in the updates dict. Allowed fields:
        strategy, scale_down_cron, scale_up_cron, scale_down_schedule_arn,
        scale_up_schedule_arn.

        Args:
            cp_name: Name of the capacity provider.
            updates: Dict of field names to new values.

        Returns:
            dict: The updated registration item, or None if not found.
        """
        allowed_fields = {
            "strategy", "scale_down_cron", "scale_up_cron",
            "scale_down_schedule_arn", "scale_up_schedule_arn",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_fields}
        if not filtered:
            return self.get_registration(cp_name)

        filtered["updated_at"] = _now_iso()

        update_parts = []
        names = {}
        values = {}
        for i, (field, value) in enumerate(filtered.items()):
            placeholder_name = f"#f{i}"
            placeholder_value = f":v{i}"
            update_parts.append(f"{placeholder_name} = {placeholder_value}")
            names[placeholder_name] = field
            values[placeholder_value] = value

        try:
            response = self._table.update_item(
                Key={"PK": _pk(cp_name), "SK": SK_REG},
                UpdateExpression="SET " + ", ".join(update_parts),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression=Attr("PK").exists(),
                ReturnValues="ALL_NEW",
            )
            return response.get("Attributes")
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return None

    def delete_registration(self, cp_name: str) -> None:
        """Delete a registration record and its associated snapshot.

        Removes both the REG and SNAPSHOT items for the capacity provider.
        Silently succeeds if the items don't exist (idempotent).

        Args:
            cp_name: Name of the capacity provider.
        """
        pk = _pk(cp_name)
        self._table.delete_item(Key={"PK": pk, "SK": SK_REG})
        self._table.delete_item(Key={"PK": pk, "SK": SK_SNAPSHOT})
        logger.info("Deleted registration and snapshot for capacity provider '%s'", cp_name)

    def update_registration_state(self, cp_name: str, from_state: str, to_state: str) -> bool:
        """Atomically transition a registration's state.

        Uses a ConditionExpression to ensure the current state matches
        from_state before updating to to_state. This prevents race conditions
        when multiple EventBridge Scheduler invocations fire concurrently.

        Args:
            cp_name: Name of the capacity provider.
            from_state: Expected current state ('active' or 'scaled-down').
            to_state: Desired new state ('active' or 'scaled-down').

        Returns:
            True if the transition succeeded, False if the condition check
            failed (meaning the state was already transitioned by another
            invocation).
        """
        try:
            self._table.update_item(
                Key={"PK": _pk(cp_name), "SK": SK_REG},
                UpdateExpression="SET #state = :to_state, #updated = :now",
                ConditionExpression="#state = :from_state",
                ExpressionAttributeNames={
                    "#state": "state",
                    "#updated": "updated_at",
                },
                ExpressionAttributeValues={
                    ":from_state": from_state,
                    ":to_state": to_state,
                    ":now": _now_iso(),
                },
            )
            logger.info(
                "State transition '%s' -> '%s' for capacity provider '%s'",
                from_state, to_state, cp_name,
            )
            return True
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            logger.info(
                "State transition '%s' -> '%s' skipped for '%s' (already transitioned)",
                from_state, to_state, cp_name,
            )
            return False

    def save_snapshot(self, cp_name: str, snapshot: dict[str, Any]) -> None:
        """Store the pre-scale-down snapshot of scaling configuration.

        Overwrites any existing snapshot for this CP. The snapshot format
        depends on the strategy:
            - Shape: {"functions": {<fn_arn>: {"min": N, "max": N}, ...}}
            - Pause: {"functions": {<fn_arn>: {"min": N, "max": N}, ...}}

        Args:
            cp_name: Name of the capacity provider.
            snapshot: Strategy-specific snapshot of original scaling values.
        """
        item = {
            "PK": _pk(cp_name),
            "SK": SK_SNAPSHOT,
            "snapshot": snapshot,
            "created_at": _now_iso(),
        }
        self._table.put_item(Item=item)
        logger.info("Saved snapshot for capacity provider '%s'", cp_name)

    def get_snapshot(self, cp_name: str) -> dict[str, Any] | None:
        """Retrieve the pre-scale-down snapshot for a capacity provider.

        Args:
            cp_name: Name of the capacity provider.

        Returns:
            The snapshot dict if it exists, None otherwise. Returns just the
            'snapshot' field value, not the full DynamoDB item.
        """
        response = self._table.get_item(
            Key={"PK": _pk(cp_name), "SK": SK_SNAPSHOT},
        )
        item = response.get("Item")
        if item:
            return item.get("snapshot")
        return None

    def save_cost_baseline(self, cp_name: str, baseline: dict[str, Any]) -> None:
        """Store the pre-scale-down cost baseline alongside the snapshot.

        Updates the existing SNAPSHOT item with the baseline captured from
        CloudWatch at scale-down time. This baseline is read back at scale-up
        time to calculate savings — capturing it fresh at scale-up would be
        unreliable because CloudWatch metrics lag behind the restored state.

        Args:
            cp_name: Name of the capacity provider.
            baseline: Fleet snapshot dict from _snapshot_fleet() in the executor.
        """
        self._table.update_item(
            Key={"PK": _pk(cp_name), "SK": SK_SNAPSHOT},
            UpdateExpression="SET cost_baseline = :b",
            ExpressionAttributeValues={":b": baseline},
        )
        logger.info("Saved cost baseline for capacity provider '%s'", cp_name)

    def get_cost_baseline(self, cp_name: str) -> dict[str, Any] | None:
        """Retrieve the pre-scale-down cost baseline for a capacity provider.

        Reads the cost_baseline field from the SNAPSHOT item. Returns None
        if no snapshot exists or no baseline was captured.

        Args:
            cp_name: Name of the capacity provider.

        Returns:
            The baseline dict if it exists, None otherwise.
        """
        response = self._table.get_item(
            Key={"PK": _pk(cp_name), "SK": SK_SNAPSHOT},
        )
        item = response.get("Item")
        if item:
            return item.get("cost_baseline")
        return None

    def delete_snapshot(self, cp_name: str) -> None:
        """Delete the snapshot for a capacity provider (after scale-up).

        Silently succeeds if the snapshot doesn't exist (idempotent).

        Args:
            cp_name: Name of the capacity provider.
        """
        self._table.delete_item(
            Key={"PK": _pk(cp_name), "SK": SK_SNAPSHOT},
        )
        logger.info("Deleted snapshot for capacity provider '%s'", cp_name)

    def save_cost_event(self, cp_name: str, cost_event: dict[str, Any]) -> None:
        """Record a cost event for a completed scale-down/scale-up cycle.

        Stores an item with PK=CP#<cp-name>, SK=COST#<timestamp>. The
        timestamp in the sort key is the scale-up time (when savings are
        finalised). Each event captures the baseline vCPU allocation,
        duration of the down period, and estimated dollar savings.

        Args:
            cp_name: Name of the capacity provider.
            cost_event: Dict containing:
                - scale_down_time (str): ISO 8601 timestamp
                - scale_up_time (str): ISO 8601 timestamp
                - duration_hours (float)
                - baseline (dict): Instance type distribution from CloudWatch
                - estimated_savings_usd (float)
        """
        timestamp = cost_event.get("scale_up_time", _now_iso())
        item = {
            "PK": _pk(cp_name),
            "SK": f"{SK_COST_PREFIX}{timestamp}",
            **cost_event,
        }
        self._table.put_item(Item=item)
        logger.info("Saved cost event for capacity provider '%s' at %s", cp_name, timestamp)

    def query_cost_events(
        self, cp_name: str, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict[str, Any]]:
        """Query cost events for a capacity provider within a date range.

        Uses a sort key range query on COST#<start> to COST#<end>. If no
        date range is provided, returns all cost events for the CP.

        Args:
            cp_name: Name of the capacity provider.
            start_date: ISO 8601 date/datetime string for the range start.
                If None, queries from the beginning of time.
            end_date: ISO 8601 date/datetime string for the range end.
                If None, queries to the end of time.

        Returns:
            list: Cost event records within the specified date range,
                sorted by timestamp ascending.
        """
        pk = _pk(cp_name)

        if start_date and end_date:
            key_condition = Key("PK").eq(pk) & Key("SK").between(
                f"{SK_COST_PREFIX}{start_date}",
                f"{SK_COST_PREFIX}{end_date}",
            )
        elif start_date:
            key_condition = Key("PK").eq(pk) & Key("SK").gte(
                f"{SK_COST_PREFIX}{start_date}"
            )
        elif end_date:
            key_condition = Key("PK").eq(pk) & Key("SK").between(
                SK_COST_PREFIX,
                f"{SK_COST_PREFIX}{end_date}",
            )
        else:
            key_condition = Key("PK").eq(pk) & Key("SK").begins_with(SK_COST_PREFIX)

        items = []
        query_kwargs = {"KeyConditionExpression": key_condition}

        while True:
            response = self._table.query(**query_kwargs)
            items.extend(response.get("Items", []))

            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key

        return items
