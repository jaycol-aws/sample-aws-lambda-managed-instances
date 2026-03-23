# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the Pause scaling strategy.

Uses unittest.mock to stub the boto3 Lambda client since the LMI
capacity provider APIs are too new for moto.
"""

from unittest.mock import MagicMock, call

import pytest

from scheduler.strategies.pause import LMI_QUALIFIER, PauseStrategy, _base_function_arn


@pytest.fixture
def lambda_client():
    """Create a mocked boto3 Lambda client with exception support."""
    client = MagicMock()
    client.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )
    return client


@pytest.fixture
def strategy(lambda_client):
    """Create a PauseStrategy with the mocked client."""
    return PauseStrategy(lambda_client)


def _mock_list_functions(lambda_client, function_arns, paginated=False):
    """Configure the mock to return function ARNs from list API.

    Args:
        lambda_client: Mocked boto3 client.
        function_arns: List of function ARN strings.
        paginated: If True, split across two pages.
    """
    if not paginated:
        lambda_client.list_function_versions_by_capacity_provider.return_value = {
            "FunctionVersions": [
                {"FunctionArn": arn, "State": "Active"} for arn in function_arns
            ],
        }
    else:
        mid = len(function_arns) // 2
        lambda_client.list_function_versions_by_capacity_provider.side_effect = [
            {
                "FunctionVersions": [
                    {"FunctionArn": arn, "State": "Active"} for arn in function_arns[:mid]
                ],
                "NextMarker": "page2",
            },
            {
                "FunctionVersions": [
                    {"FunctionArn": arn, "State": "Active"} for arn in function_arns[mid:]
                ],
            },
        ]


def _mock_scaling_configs(lambda_client, configs):
    """Configure the mock to return scaling configs per function.

    Args:
        lambda_client: Mocked boto3 client.
        configs: Dict mapping function ARN (base) to (min, max) tuples.
    """
    def get_config(FunctionName, Qualifier):
        min_ee, max_ee = configs[FunctionName]
        return {
            "FunctionArn": FunctionName,
            "AppliedFunctionScalingConfig": {
                "MinExecutionEnvironments": min_ee,
                "MaxExecutionEnvironments": max_ee,
            },
        }

    lambda_client.get_function_scaling_config.side_effect = get_config


class TestBaseFunctionArn:

    def test_strips_latest_published_qualifier(self):
        arn = "arn:aws:lambda:us-east-1:123456789012:function:my-fn:$LATEST.PUBLISHED"
        assert _base_function_arn(arn) == "arn:aws:lambda:us-east-1:123456789012:function:my-fn"

    def test_returns_arn_unchanged_when_no_qualifier(self):
        arn = "arn:aws:lambda:us-east-1:123:function:my-fn"
        assert _base_function_arn(arn) == arn


class TestPauseScaleDown:

    def test_zeros_all_functions(self, strategy, lambda_client):
        # Arrange
        fn_a = "arn:aws:lambda:us-east-1:123:function:fn-a"
        fn_b = "arn:aws:lambda:us-east-1:123:function:fn-b"
        _mock_list_functions(lambda_client, [fn_a, fn_b])
        _mock_scaling_configs(lambda_client, {fn_a: (3, 100), fn_b: (5, 50)})

        # Act
        snapshot = strategy.scale_down("test-cp")

        # Assert
        assert snapshot == {
            "functions": {
                fn_a: {"min": 3, "max": 100},
                fn_b: {"min": 5, "max": 50},
            }
        }
        assert lambda_client.put_function_scaling_config.call_count == 2
        lambda_client.put_function_scaling_config.assert_any_call(
            FunctionName=fn_a,
            Qualifier=LMI_QUALIFIER,
            FunctionScalingConfig={"MinExecutionEnvironments": 0, "MaxExecutionEnvironments": 0},
        )
        lambda_client.put_function_scaling_config.assert_any_call(
            FunctionName=fn_b,
            Qualifier=LMI_QUALIFIER,
            FunctionScalingConfig={"MinExecutionEnvironments": 0, "MaxExecutionEnvironments": 0},
        )

    def test_empty_snapshot_when_no_functions(self, strategy, lambda_client):
        # Arrange
        _mock_list_functions(lambda_client, [])

        # Act
        snapshot = strategy.scale_down("test-cp")

        # Assert
        assert snapshot == {"functions": {}}
        lambda_client.put_function_scaling_config.assert_not_called()

    def test_skips_already_zeroed_functions(self, strategy, lambda_client):
        # Arrange
        fn_a = "arn:aws:lambda:us-east-1:123:function:fn-a"
        fn_b = "arn:aws:lambda:us-east-1:123:function:fn-b"
        _mock_list_functions(lambda_client, [fn_a, fn_b])
        _mock_scaling_configs(lambda_client, {fn_a: (0, 0), fn_b: (5, 50)})

        # Act
        snapshot = strategy.scale_down("test-cp")

        # Assert — both are in snapshot, but only fn_b gets the put call
        assert fn_a in snapshot["functions"]
        assert fn_b in snapshot["functions"]
        assert lambda_client.put_function_scaling_config.call_count == 1
        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName=fn_b,
            Qualifier=LMI_QUALIFIER,
            FunctionScalingConfig={"MinExecutionEnvironments": 0, "MaxExecutionEnvironments": 0},
        )

    def test_calls_api_with_base_arn_when_list_returns_qualified_arn(self, strategy, lambda_client):
        full_arn = "arn:aws:lambda:us-east-1:123456789012:function:lmi-example-function:$LATEST.PUBLISHED"
        base_arn = "arn:aws:lambda:us-east-1:123456789012:function:lmi-example-function"
        _mock_list_functions(lambda_client, [full_arn])
        _mock_scaling_configs(lambda_client, {base_arn: (2, 10)})

        strategy.scale_down("test-cp")

        lambda_client.get_function_scaling_config.assert_called_once_with(
            FunctionName=base_arn,
            Qualifier=LMI_QUALIFIER,
        )
        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName=base_arn,
            Qualifier=LMI_QUALIFIER,
            FunctionScalingConfig={"MinExecutionEnvironments": 0, "MaxExecutionEnvironments": 0},
        )

    def test_handles_paginated_function_list(self, strategy, lambda_client):
        # Arrange
        fn_a = "arn:aws:lambda:us-east-1:123:function:fn-a"
        fn_b = "arn:aws:lambda:us-east-1:123:function:fn-b"
        fn_c = "arn:aws:lambda:us-east-1:123:function:fn-c"
        fn_d = "arn:aws:lambda:us-east-1:123:function:fn-d"
        _mock_list_functions(lambda_client, [fn_a, fn_b, fn_c, fn_d], paginated=True)
        _mock_scaling_configs(lambda_client, {
            fn_a: (1, 10), fn_b: (2, 20), fn_c: (3, 30), fn_d: (4, 40),
        })

        # Act
        snapshot = strategy.scale_down("test-cp")

        # Assert
        assert len(snapshot["functions"]) == 4
        assert lambda_client.put_function_scaling_config.call_count == 4


class TestPauseScaleUp:

    def test_restores_all_functions(self, strategy, lambda_client):
        # Arrange
        fn_a = "arn:aws:lambda:us-east-1:123:function:fn-a"
        fn_b = "arn:aws:lambda:us-east-1:123:function:fn-b"
        snapshot = {
            "functions": {
                fn_a: {"min": 3, "max": 100},
                fn_b: {"min": 5, "max": 50},
            }
        }

        # Act
        strategy.scale_up("test-cp", snapshot)

        # Assert
        assert lambda_client.put_function_scaling_config.call_count == 2
        lambda_client.put_function_scaling_config.assert_any_call(
            FunctionName=fn_a,
            Qualifier=LMI_QUALIFIER,
            FunctionScalingConfig={"MinExecutionEnvironments": 3, "MaxExecutionEnvironments": 100},
        )
        lambda_client.put_function_scaling_config.assert_any_call(
            FunctionName=fn_b,
            Qualifier=LMI_QUALIFIER,
            FunctionScalingConfig={"MinExecutionEnvironments": 5, "MaxExecutionEnvironments": 50},
        )

    def test_noop_with_empty_snapshot(self, strategy, lambda_client):
        # Arrange
        snapshot = {"functions": {}}

        # Act
        strategy.scale_up("test-cp", snapshot)

        # Assert
        lambda_client.put_function_scaling_config.assert_not_called()

    def test_skips_deleted_functions(self, strategy, lambda_client):
        # Arrange
        fn_a = "arn:aws:lambda:us-east-1:123:function:fn-a"
        fn_b = "arn:aws:lambda:us-east-1:123:function:fn-b"
        snapshot = {
            "functions": {
                fn_a: {"min": 3, "max": 100},
                fn_b: {"min": 5, "max": 50},
            }
        }
        lambda_client.put_function_scaling_config.side_effect = [
            None,
            lambda_client.exceptions.ResourceNotFoundException("not found"),
        ]

        # Act — should not raise
        strategy.scale_up("test-cp", snapshot)

        # Assert — both were attempted
        assert lambda_client.put_function_scaling_config.call_count == 2

    def test_handles_missing_functions_key(self, strategy, lambda_client):
        # Arrange — malformed snapshot
        snapshot = {}

        # Act — should not raise, just noop
        strategy.scale_up("test-cp", snapshot)

        # Assert
        lambda_client.put_function_scaling_config.assert_not_called()

    def test_scale_up_sets_min_one_when_snapshot_has_zero_min(self, strategy, lambda_client):
        fn = "arn:aws:lambda:us-east-1:123:function:fn-a"
        snapshot = {"functions": {fn: {"min": 0, "max": 5}}}
        strategy.scale_up("test-cp", snapshot)
        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName=fn,
            Qualifier=LMI_QUALIFIER,
            FunctionScalingConfig={"MinExecutionEnvironments": 1, "MaxExecutionEnvironments": 5},
        )

    def test_scale_up_sets_one_one_when_snapshot_has_zero_zero(self, strategy, lambda_client):
        fn = "arn:aws:lambda:us-east-1:123:function:fn-a"
        snapshot = {"functions": {fn: {"min": 0, "max": 0}}}
        strategy.scale_up("test-cp", snapshot)
        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName=fn,
            Qualifier=LMI_QUALIFIER,
            FunctionScalingConfig={"MinExecutionEnvironments": 1, "MaxExecutionEnvironments": 1},
        )
