# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Management API Lambda handler for the LMI scheduler.

Handles CRUD operations for capacity provider registrations and cost reports.
Invoked by API Gateway HTTP API. Routes requests based on HTTP method and path
to the appropriate handler function.

Routes:
    POST   /registrations           - Register a capacity provider with the scheduler
    GET    /registrations           - List all registered capacity providers
    GET    /registrations/{cpName}  - Get a specific registration
    PUT    /registrations/{cpName}  - Update schedule expressions or strategy
    DELETE /registrations/{cpName}  - Unregister a capacity provider and delete its schedules
    GET    /reports/{cpName}        - Get cost savings report for a capacity provider
    GET    /reports                 - Get aggregate cost savings report across all CPs
"""

import json
import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer

from scheduler.reporting.cost import generate_aggregate_report, generate_cp_report
from scheduler.state.repository import RegistrationExistsError, StateRepository

logger = Logger()
tracer = Tracer()

VALID_STRATEGIES = ("shape", "pause")

_repo = None
_lambda_client = None
_scheduler_client = None
_schedule_group = None
_executor_fn_arn = None
_scheduler_role_arn = None
_dlq_arn = None


def _init():
    """Lazily initialise module-level AWS clients and configuration.

    Reads environment variables set by the SAM template and creates
    boto3 clients. Separated from module scope so that unit tests can
    import the module without triggering real AWS connections.
    """
    global _repo, _lambda_client, _scheduler_client
    global _schedule_group, _executor_fn_arn, _scheduler_role_arn, _dlq_arn

    if _repo is not None:
        return

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ.get("STATE_TABLE_NAME", "lmi-scheduler"))
    _repo = StateRepository(table)

    _lambda_client = boto3.client("lambda")
    _scheduler_client = boto3.client("scheduler")

    _schedule_group = os.environ.get("SCHEDULE_GROUP_NAME", "lmi-scheduler")
    _executor_fn_arn = os.environ.get("EXECUTOR_FUNCTION_ARN", "")
    _scheduler_role_arn = os.environ.get("SCHEDULER_ROLE_ARN", "")
    _dlq_arn = os.environ.get("DLQ_ARN", "")


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def handler(event, context):
    """API Gateway Lambda handler. Routes requests to the appropriate function.

    Args:
        event: API Gateway HTTP API v2 event payload.
        context: Lambda context object.

    Returns:
        dict: API Gateway response with statusCode, headers, and JSON body.
    """
    _init()

    http_method = event.get("requestContext", {}).get("http", {}).get("method", "")
    raw_path = event.get("rawPath", "")
    stage = event.get("requestContext", {}).get("stage", "")
    path = raw_path.removeprefix(f"/{stage}") if stage and stage != "$default" else raw_path
    path_params = event.get("pathParameters") or {}

    try:
        if path == "/capacity-providers" and http_method == "GET":
            return _list_capacity_providers()
        elif "/actions/scale-down" in path and http_method == "POST":
            return _trigger_action(path_params.get("cpName"), "scale-down")
        elif "/actions/scale-up" in path and http_method == "POST":
            return _trigger_action(path_params.get("cpName"), "scale-up")
        elif path == "/registrations" and http_method == "POST":
            return _create_registration(event)
        elif path == "/registrations" and http_method == "GET":
            return _list_registrations()
        elif path.startswith("/registrations/") and http_method == "GET":
            return _get_registration(path_params.get("cpName"))
        elif path.startswith("/registrations/") and http_method == "PUT":
            return _update_registration(path_params.get("cpName"), event)
        elif path.startswith("/registrations/") and http_method == "DELETE":
            return _delete_registration(path_params.get("cpName"))
        elif path.startswith("/reports/") and http_method == "GET":
            return _get_report(path_params.get("cpName"), event)
        elif path == "/reports" and http_method == "GET":
            return _get_aggregate_report(event)
        else:
            return _response(404, {"error": "Not found"})
    except Exception as e:
        logger.exception("Unhandled error")
        return _response(500, {"error": str(e)})


@tracer.capture_method
def _list_capacity_providers():
    """List all Lambda capacity providers via the Lambda API.

    Paginates through all capacity providers and returns their names and ARNs.

    Returns:
        dict: 200 with {"capacityProviders": [{"name": "...", "arn": "..."}]}.
    """
    capacity_providers = []
    kwargs = {}

    while True:
        response = _lambda_client.list_capacity_providers(**kwargs)
        for cp in response.get("CapacityProviders", []):
            arn = cp["CapacityProviderArn"]
            # ARN format: arn:aws:lambda:<region>:<account>:capacity-provider:<name>
            name = arn.rsplit(":", 1)[-1]
            capacity_providers.append({
                "name": name,
                "arn": arn,
            })
        next_marker = response.get("NextMarker")
        if not next_marker:
            break
        kwargs["Marker"] = next_marker

    return _response(200, {"capacityProviders": capacity_providers})


@tracer.capture_method
def _trigger_action(cp_name, action):
    """Trigger a manual scale-down or scale-up action for a capacity provider.

    Verifies the registration exists, then synchronously invokes the Executor
    Lambda and returns its response.

    Args:
        cp_name: Name of the capacity provider.
        action: 'scale-down' or 'scale-up'.

    Returns:
        dict: 200 with executor response, 404 if registration not found.
    """
    if not cp_name:
        return _response(400, {"error": "Missing capacity provider name"})

    registration = _repo.get_registration(cp_name)
    if not registration:
        return _response(404, {"error": f"Registration not found for '{cp_name}'"})

    response = _lambda_client.invoke(
        FunctionName=os.environ["EXECUTOR_FUNCTION_ARN"],
        InvocationType="RequestResponse",
        Payload=json.dumps({"capacityProviderName": cp_name, "action": action}),
    )

    payload = json.loads(response["Payload"].read())
    return _response(200, payload)


@tracer.capture_method
def _create_registration(event):
    """Register a capacity provider with the scheduler.

    Validates the request body, verifies the CP exists via the Lambda API,
    creates scale-down and scale-up EventBridge Scheduler schedules, and
    stores the registration in DynamoDB.

    Expected body:
        {
            "capacityProviderName": "my-cp",
            "strategy": "shape" | "pause",
            "scaleDownSchedule": "cron(0 22 ? * MON-FRI *)",
            "scaleUpSchedule": "cron(0 6 ? * MON-FRI *)",
            "minExecutionEnvironments": 2,   // optional, shape strategy only
            "maxExecutionEnvironments": 10   // optional, shape strategy only
        }

    Args:
        event: API Gateway event with JSON body.

    Returns:
        dict: 201 with registration details, 400 on validation failure,
              409 if already registered.
    """
    body = _parse_body(event)
    if not body:
        return _response(400, {"error": "Invalid or missing JSON body"})

    cp_name = body.get("capacityProviderName")
    strategy = body.get("strategy")
    scale_down_cron = body.get("scaleDownSchedule")
    scale_up_cron = body.get("scaleUpSchedule")

    errors = _validate_registration_input(cp_name, strategy, scale_down_cron, scale_up_cron)
    if errors:
        return _response(400, {"errors": errors})

    try:
        _lambda_client.get_capacity_provider(CapacityProviderName=cp_name)
    except _lambda_client.exceptions.ResourceNotFoundException:
        return _response(404, {"error": f"Capacity provider '{cp_name}' not found"})
    except Exception as e:
        logger.error("Failed to verify capacity provider '%s': %s", cp_name, e)
        return _response(502, {"error": f"Failed to verify capacity provider: {e}"})

    down_schedule_arn = _create_schedule(
        cp_name, "down", scale_down_cron, "scale-down"
    )
    up_schedule_arn = _create_schedule(
        cp_name, "up", scale_up_cron, "scale-up"
    )

    registration = {
        "capacity_provider_name": cp_name,
        "strategy": strategy,
        "scale_down_cron": scale_down_cron,
        "scale_up_cron": scale_up_cron,
        "scale_down_schedule_arn": down_schedule_arn,
        "scale_up_schedule_arn": up_schedule_arn,
    }

    if strategy == "shape":
        registration["min_execution_environments"] = body.get("minExecutionEnvironments", 1)
        registration["max_execution_environments"] = body.get("maxExecutionEnvironments", 1)

    try:
        _repo.create_registration(registration)
    except RegistrationExistsError:
        _delete_schedule(cp_name, "down")
        _delete_schedule(cp_name, "up")
        return _response(409, {"error": f"Capacity provider '{cp_name}' is already registered"})

    logger.info("Created registration for '%s' with strategy '%s'", cp_name, strategy)
    return _response(201, registration)


def _list_registrations():
    """List all capacity provider registrations.

    Returns:
        dict: 200 with list of registration records.
    """
    registrations = _repo.list_registrations()
    return _response(200, {"registrations": registrations})


def _get_registration(cp_name):
    """Get a specific capacity provider registration.

    Args:
        cp_name: Name of the capacity provider.

    Returns:
        dict: 200 with registration, or 404 if not found.
    """
    if not cp_name:
        return _response(400, {"error": "Missing capacity provider name"})

    registration = _repo.get_registration(cp_name)
    if not registration:
        return _response(404, {"error": f"Registration not found for '{cp_name}'"})

    return _response(200, registration)


@tracer.capture_method
def _update_registration(cp_name, event):
    """Update an existing registration's schedule expressions or strategy.

    Rejects updates if the CP is currently in 'scaled-down' state to
    prevent schedule changes while the CP is modified.

    Allowed fields: strategy, scaleDownSchedule, scaleUpSchedule.

    Args:
        cp_name: Name of the capacity provider.
        event: API Gateway event with JSON body.

    Returns:
        dict: 200 with updated registration, 400/404/409 on error.
    """
    if not cp_name:
        return _response(400, {"error": "Missing capacity provider name"})

    registration = _repo.get_registration(cp_name)
    if not registration:
        return _response(404, {"error": f"Registration not found for '{cp_name}'"})

    if registration["state"] == "scaled-down":
        return _response(
            409, {"error": "Cannot update registration while capacity provider is scaled down"}
        )

    body = _parse_body(event)
    if not body:
        return _response(400, {"error": "Invalid or missing JSON body"})

    updates = {}

    if "strategy" in body:
        if body["strategy"] not in VALID_STRATEGIES:
            return _response(400, {"error": f"Invalid strategy. Must be one of: {VALID_STRATEGIES}"})
        updates["strategy"] = body["strategy"]

    if "scaleDownSchedule" in body:
        _update_schedule(cp_name, "down", body["scaleDownSchedule"], "scale-down")
        updates["scale_down_cron"] = body["scaleDownSchedule"]

    if "scaleUpSchedule" in body:
        _update_schedule(cp_name, "up", body["scaleUpSchedule"], "scale-up")
        updates["scale_up_cron"] = body["scaleUpSchedule"]

    if not updates:
        return _response(400, {"error": "No valid fields to update"})

    updated = _repo.update_registration(cp_name, updates)
    if not updated:
        return _response(404, {"error": f"Registration not found for '{cp_name}'"})

    return _response(200, updated)


@tracer.capture_method
def _delete_registration(cp_name):
    """Unregister a capacity provider and delete its EventBridge schedules.

    If the CP is currently scaled down, performs a scale-up first by invoking
    the executor's scale-up logic to restore original configuration.

    Args:
        cp_name: Name of the capacity provider.

    Returns:
        dict: 204 on success, 404 if not found.
    """
    if not cp_name:
        return _response(400, {"error": "Missing capacity provider name"})

    registration = _repo.get_registration(cp_name)
    if not registration:
        return _response(404, {"error": f"Registration not found for '{cp_name}'"})

    if registration["state"] == "scaled-down":
        logger.info("CP '%s' is scaled-down, performing scale-up before deletion", cp_name)
        try:
            from scheduler.executor import _scale_up
            _scale_up(cp_name)
        except Exception:
            logger.warning(
                "Scale-up failed during deletion of '%s', proceeding with cleanup",
                cp_name,
                exc_info=True,
            )

    _delete_schedule(cp_name, "down")
    _delete_schedule(cp_name, "up")

    _repo.delete_registration(cp_name)

    logger.info("Deleted registration for '%s'", cp_name)
    return _response(204, None)


def _get_report(cp_name, event):
    """Get cost savings report for a specific capacity provider.

    Args:
        cp_name: Name of the capacity provider.
        event: API Gateway event (query string params: start, end).

    Returns:
        dict: 200 with cost report.
    """
    if not cp_name:
        return _response(400, {"error": "Missing capacity provider name"})

    params = event.get("queryStringParameters") or {}
    start_date = params.get("start")
    end_date = params.get("end")

    report = generate_cp_report(_repo, cp_name, start_date, end_date)
    return _response(200, report)


def _get_aggregate_report(event):
    """Get aggregate cost savings report across all registered capacity providers.

    Args:
        event: API Gateway event (query string params: start, end).

    Returns:
        dict: 200 with aggregate report.
    """
    params = event.get("queryStringParameters") or {}
    start_date = params.get("start")
    end_date = params.get("end")

    report = generate_aggregate_report(_repo, start_date, end_date)
    return _response(200, report)


def _validate_registration_input(cp_name, strategy, scale_down_cron, scale_up_cron):
    """Validate registration request fields.

    Args:
        cp_name: Capacity provider name.
        strategy: Scaling strategy name.
        scale_down_cron: Scale-down cron expression.
        scale_up_cron: Scale-up cron expression.

    Returns:
        list of error message strings, empty if valid.
    """
    errors = []
    if not cp_name:
        errors.append("capacityProviderName is required")
    if strategy not in VALID_STRATEGIES:
        errors.append(f"strategy must be one of: {VALID_STRATEGIES}")
    if not scale_down_cron:
        errors.append("scaleDownSchedule is required")
    if not scale_up_cron:
        errors.append("scaleUpSchedule is required")
    return errors


def _create_schedule(cp_name: str, suffix: str, cron_expr: str, action: str) -> str:
    """Create an EventBridge Scheduler schedule.

    Args:
        cp_name: Capacity provider name (used in schedule name).
        suffix: 'down' or 'up'.
        cron_expr: Cron or rate expression for the schedule.
        action: 'scale-down' or 'scale-up' (passed to executor).

    Returns:
        str: The ARN of the created schedule.
    """
    schedule_name = f"lmi-scheduler-{cp_name}-{suffix}"
    payload = json.dumps({"capacityProviderName": cp_name, "action": action})

    response = _scheduler_client.create_schedule(
        Name=schedule_name,
        GroupName=_schedule_group,
        ScheduleExpression=cron_expr,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": _executor_fn_arn,
            "RoleArn": _scheduler_role_arn,
            "Input": payload,
            "DeadLetterConfig": {"Arn": _dlq_arn},
            "RetryPolicy": {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 900},
        },
        State="ENABLED",
    )

    arn = response["ScheduleArn"]
    logger.info("Created schedule '%s' -> %s", schedule_name, arn)
    return arn


def _update_schedule(cp_name: str, suffix: str, cron_expr: str, action: str) -> None:
    """Update an existing EventBridge Scheduler schedule's cron expression.

    Args:
        cp_name: Capacity provider name.
        suffix: 'down' or 'up'.
        cron_expr: New cron expression.
        action: 'scale-down' or 'scale-up'.
    """
    schedule_name = f"lmi-scheduler-{cp_name}-{suffix}"
    payload = json.dumps({"capacityProviderName": cp_name, "action": action})

    _scheduler_client.update_schedule(
        Name=schedule_name,
        GroupName=_schedule_group,
        ScheduleExpression=cron_expr,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": _executor_fn_arn,
            "RoleArn": _scheduler_role_arn,
            "Input": payload,
            "DeadLetterConfig": {"Arn": _dlq_arn},
            "RetryPolicy": {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 900},
        },
        State="ENABLED",
    )

    logger.info("Updated schedule '%s' with new expression '%s'", schedule_name, cron_expr)


def _delete_schedule(cp_name: str, suffix: str) -> None:
    """Delete an EventBridge Scheduler schedule.

    Silently succeeds if the schedule doesn't exist.

    Args:
        cp_name: Capacity provider name.
        suffix: 'down' or 'up'.
    """
    schedule_name = f"lmi-scheduler-{cp_name}-{suffix}"
    try:
        _scheduler_client.delete_schedule(Name=schedule_name, GroupName=_schedule_group)
        logger.info("Deleted schedule '%s'", schedule_name)
    except _scheduler_client.exceptions.ResourceNotFoundException:
        logger.info("Schedule '%s' already deleted", schedule_name)
    except Exception:
        logger.warning("Failed to delete schedule '%s'", schedule_name, exc_info=True)


def _parse_body(event) -> dict[str, Any] | None:
    """Parse the JSON body from an API Gateway event.

    Args:
        event: API Gateway HTTP API v2 event.

    Returns:
        Parsed dict, or None if body is missing or invalid JSON.
    """
    raw = event.get("body")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _response(status_code, body):
    """Build a standard API Gateway response.

    Args:
        status_code: HTTP status code.
        body: Response body dict (will be JSON-serialized), or None for 204.

    Returns:
        dict: API Gateway-compatible response.
    """
    resp = {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
    }
    if body is not None:
        resp["body"] = json.dumps(body, default=str)
    return resp
