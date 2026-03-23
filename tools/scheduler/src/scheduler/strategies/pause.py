# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Pause scaling strategy.

Sets MinExecutionEnvironments and MaxExecutionEnvironments to 0 for every
function associated with a capacity provider via PutFunctionScalingConfig.
This prevents the CP from provisioning any execution environments, which
should cause all EC2 instances to scale in.

On scale-up, restores each function's original execution environment counts
from the snapshot.

Note: This strategy discovers functions dynamically via
ListFunctionVersionsByCapacityProvider, so it handles functions added or
removed between scheduling and execution.

Important: The Qualifier for LMI function scaling config is "$LATEST.PUBLISHED",
not "$LATEST" or a numeric version.
"""

from typing import Any

from aws_lambda_powertools import Logger

from scheduler.strategies.base import ScalingStrategy

logger = Logger(child=True)

LMI_QUALIFIER = "$LATEST.PUBLISHED"


def _base_function_arn(fn_arn: str) -> str:
    """Return the function name or ARN without qualifier for API calls.

    ListFunctionVersionsByCapacityProvider returns ARNs that may include
    a qualifier (e.g. :$LATEST.PUBLISHED). GetFunctionScalingConfig and
    PutFunctionScalingConfig expect FunctionName without the qualifier;
    Qualifier is passed separately.

    Args:
        fn_arn: Full function ARN possibly including qualifier.

    Returns:
        str: Base ARN or name suitable for FunctionName parameter.
    """
    if fn_arn.endswith(":$LATEST.PUBLISHED"):
        return fn_arn[: -len(":$LATEST.PUBLISHED")]
    if ":" in fn_arn:
        rest, last = fn_arn.rsplit(":", 1)
        if last.isdigit() or last == "$LATEST":
            return rest
    return fn_arn


class PauseStrategy(ScalingStrategy):
    """Scale down by zeroing out per-function execution environments."""

    def __init__(self, lambda_client):
        """Initialise with a boto3 Lambda client.

        Args:
            lambda_client: boto3 client for the Lambda service, used to call
                ListFunctionVersionsByCapacityProvider, GetFunctionScalingConfig,
                and PutFunctionScalingConfig.
        """
        self._lambda = lambda_client

    def scale_down(self, cp_name: str) -> dict[str, Any]:
        """Zero out execution environments for all functions on the CP.

        Lists all functions via ListFunctionVersionsByCapacityProvider, reads
        each function's current scaling config via GetFunctionScalingConfig
        (using the $LATEST.PUBLISHED qualifier), then sets both
        MinExecutionEnvironments and MaxExecutionEnvironments to 0 via
        PutFunctionScalingConfig.

        Args:
            cp_name: Name of the capacity provider.

        Returns:
            dict: Snapshot containing per-function original values:
                {"functions": {<fn_arn>: {"min": N, "max": N}, ...}}
        """
        function_arns = self._list_functions(cp_name)

        if not function_arns:
            logger.info("Pause scale-down: no functions found on '%s'", cp_name)
            return {"functions": {}}

        logger.info(
            "Pause scale-down: found %d function(s) on '%s'",
            len(function_arns), cp_name,
        )

        functions_snapshot = {}

        for fn_arn in function_arns:
            scaling = self._get_scaling_config(fn_arn)
            min_ee, max_ee = scaling["min"], scaling["max"]
            if min_ee > max_ee:
                logger.warning(
                    "Pause scale-down: '%s' had min=%s > max=%s from API, storing as min<=max for snapshot",
                    fn_arn, min_ee, max_ee,
                )
                min_ee, max_ee = max_ee, min_ee
            functions_snapshot[fn_arn] = {"min": min_ee, "max": max_ee}

            if min_ee == 0 and max_ee == 0:
                logger.info(
                    "Pause scale-down: '%s' already at 0/0, skipping", fn_arn,
                )
                continue

            logger.info(
                "Pause scale-down: setting '%s' from min=%s/max=%s to 0/0",
                fn_arn, min_ee, max_ee,
            )

            self._lambda.put_function_scaling_config(
                FunctionName=_base_function_arn(fn_arn),
                Qualifier=LMI_QUALIFIER,
                FunctionScalingConfig={
                    "MinExecutionEnvironments": 0,
                    "MaxExecutionEnvironments": 0,
                },
            )

        return {"functions": functions_snapshot}

    def scale_up(self, cp_name: str, snapshot: dict[str, Any]) -> None:
        """Restore per-function execution environment counts from the snapshot.

        Iterates over the snapshot's function entries and calls
        PutFunctionScalingConfig with the original min/max values.

        Functions that were in the snapshot but no longer exist are logged
        and skipped (they may have been deleted between scale-down and scale-up).

        Args:
            cp_name: Name of the capacity provider.
            snapshot: Dict with key "functions" mapping function ARNs to
                {"min": N, "max": N} dicts.
        """
        functions = snapshot.get("functions", {})

        if not functions:
            logger.info("Pause scale-up: no functions in snapshot for '%s'", cp_name)
            return

        logger.info(
            "Pause scale-up: restoring %d function(s) for '%s'",
            len(functions), cp_name,
        )

        for fn_arn, config in functions.items():
            min_ee = int(config["min"])
            max_ee = int(config["max"])
            if min_ee > max_ee:
                logger.warning(
                    "Pause scale-up: snapshot had min=%d > max=%d for '%s', swapping for valid config",
                    min_ee, max_ee, fn_arn,
                )
                min_ee, max_ee = max_ee, min_ee
            if min_ee == 0 and max_ee > 0:
                logger.warning(
                    "Pause scale-up: restoring '%s' with min=0 would leave function deactivated; setting min=1 so capacity is provisioned",
                    fn_arn,
                )
                min_ee = 1
            if min_ee == 0 and max_ee == 0:
                logger.warning(
                    "Pause scale-up: snapshot had 0/0 for '%s', setting min=1 max=1 so function becomes active",
                    fn_arn,
                )
                min_ee, max_ee = 1, 1
            assert min_ee > 0 and max_ee > 0, "scale-up must set non-zero execution environments"
            logger.info(
                "Pause scale-up: restoring '%s' to min=%d/max=%d",
                fn_arn, min_ee, max_ee,
            )

            try:
                self._lambda.put_function_scaling_config(
                    FunctionName=_base_function_arn(fn_arn),
                    Qualifier=LMI_QUALIFIER,
                    FunctionScalingConfig={
                        "MinExecutionEnvironments": min_ee,
                        "MaxExecutionEnvironments": max_ee,
                    },
                )
            except self._lambda.exceptions.ResourceNotFoundException:
                logger.warning(
                    "Pause scale-up: function '%s' no longer exists, skipping", fn_arn,
                )

    def _list_functions(self, cp_name: str) -> list[str]:
        """List all function ARNs associated with a capacity provider.

        Handles pagination via the NextMarker token.

        Args:
            cp_name: Name of the capacity provider.

        Returns:
            list: Function ARNs (strings).
        """
        function_arns = []
        kwargs = {"CapacityProviderName": cp_name, "MaxItems": 50}

        while True:
            response = self._lambda.list_function_versions_by_capacity_provider(**kwargs)

            for fn_version in response.get("FunctionVersions", []):
                function_arns.append(fn_version["FunctionArn"])

            next_marker = response.get("NextMarker")
            if not next_marker:
                break
            kwargs["Marker"] = next_marker

        return function_arns

    def _get_scaling_config(self, fn_arn: str) -> dict[str, int]:
        """Read the current scaling config for a function.

        Uses the $LATEST.PUBLISHED qualifier which is required for LMI functions.
        Reads from AppliedFunctionScalingConfig (the actual in-effect values)
        rather than RequestedFunctionScalingConfig.

        Args:
            fn_arn: Function ARN.

        Returns:
            dict: {"min": N, "max": N} with current execution environment counts.
        """
        response = self._lambda.get_function_scaling_config(
            FunctionName=_base_function_arn(fn_arn),
            Qualifier=LMI_QUALIFIER,
        )

        applied = response.get("AppliedFunctionScalingConfig", {})

        return {
            "min": applied.get("MinExecutionEnvironments", 0),
            "max": applied.get("MaxExecutionEnvironments", 0),
        }
