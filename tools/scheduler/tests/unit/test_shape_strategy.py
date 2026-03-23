# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the Shape scaling strategy.

Uses unittest.mock to stub the boto3 Lambda client since the LMI
capacity provider APIs are too new for moto.
"""

from unittest.mock import MagicMock, call

import pytest

from scheduler.strategies.shape import ShapeStrategy


@pytest.fixture
def lambda_client():
    """Create a mocked boto3 Lambda client."""
    return MagicMock()


@pytest.fixture
def strategy(lambda_client):
    """Create a ShapeStrategy with the mocked client and default targets (1/1)."""
    return ShapeStrategy(lambda_client, target_min=2, target_max=10)


class TestShapeScaleDown:

    def test_snapshots_original_and_sets_targets(self, strategy, lambda_client):
        lambda_client.list_function_versions_by_capacity_provider.return_value = {
            "FunctionVersions": [{"FunctionArn": "arn:aws:lambda:us-east-1:123:function:fn-a:$LATEST.PUBLISHED"}],
        }
        lambda_client.get_function_scaling_config.return_value = {
            "AppliedFunctionScalingConfig": {
                "MinExecutionEnvironments": 10,
                "MaxExecutionEnvironments": 50,
            }
        }

        snapshot = strategy.scale_down("test-cp")

        assert snapshot == {
            "functions": {
                "arn:aws:lambda:us-east-1:123:function:fn-a:$LATEST.PUBLISHED": {"min": 10, "max": 50}
            }
        }
        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName="arn:aws:lambda:us-east-1:123:function:fn-a",
            Qualifier="$LATEST.PUBLISHED",
            FunctionScalingConfig={
                "MinExecutionEnvironments": 2,
                "MaxExecutionEnvironments": 10,
            },
        )

    def test_snapshots_multiple_functions(self, strategy, lambda_client):
        lambda_client.list_function_versions_by_capacity_provider.return_value = {
            "FunctionVersions": [
                {"FunctionArn": "arn:aws:lambda:us-east-1:123:function:fn-a:$LATEST.PUBLISHED"},
                {"FunctionArn": "arn:aws:lambda:us-east-1:123:function:fn-b:$LATEST.PUBLISHED"},
            ],
        }
        lambda_client.get_function_scaling_config.return_value = {
            "AppliedFunctionScalingConfig": {
                "MinExecutionEnvironments": 5,
                "MaxExecutionEnvironments": 30,
            }
        }

        snapshot = strategy.scale_down("test-cp")

        assert len(snapshot["functions"]) == 2
        assert lambda_client.put_function_scaling_config.call_count == 2

    def test_skips_update_when_already_at_target(self, strategy, lambda_client):
        lambda_client.list_function_versions_by_capacity_provider.return_value = {
            "FunctionVersions": [{"FunctionArn": "arn:aws:lambda:us-east-1:123:function:fn-a:$LATEST.PUBLISHED"}],
        }
        lambda_client.get_function_scaling_config.return_value = {
            "AppliedFunctionScalingConfig": {
                "MinExecutionEnvironments": 2,
                "MaxExecutionEnvironments": 10,
            }
        }

        snapshot = strategy.scale_down("test-cp")

        assert snapshot["functions"]["arn:aws:lambda:us-east-1:123:function:fn-a:$LATEST.PUBLISHED"] == {"min": 2, "max": 10}
        lambda_client.put_function_scaling_config.assert_not_called()

    def test_empty_functions_list(self, strategy, lambda_client):
        lambda_client.list_function_versions_by_capacity_provider.return_value = {
            "FunctionVersions": [],
        }

        snapshot = strategy.scale_down("test-cp")

        assert snapshot == {"functions": {}}
        lambda_client.put_function_scaling_config.assert_not_called()

    def test_swaps_min_max_when_inverted(self, strategy, lambda_client):
        lambda_client.list_function_versions_by_capacity_provider.return_value = {
            "FunctionVersions": [{"FunctionArn": "arn:aws:lambda:us-east-1:123:function:fn-a:$LATEST.PUBLISHED"}],
        }
        lambda_client.get_function_scaling_config.return_value = {
            "AppliedFunctionScalingConfig": {
                "MinExecutionEnvironments": 50,
                "MaxExecutionEnvironments": 10,
            }
        }

        snapshot = strategy.scale_down("test-cp")

        saved = snapshot["functions"]["arn:aws:lambda:us-east-1:123:function:fn-a:$LATEST.PUBLISHED"]
        assert saved["min"] == 10
        assert saved["max"] == 50

    def test_propagates_api_errors(self, strategy, lambda_client):
        lambda_client.list_function_versions_by_capacity_provider.side_effect = Exception("API error")

        with pytest.raises(Exception, match="API error"):
            strategy.scale_down("test-cp")

    def test_custom_target_values(self, lambda_client):
        custom = ShapeStrategy(lambda_client, target_min=5, target_max=20)
        lambda_client.list_function_versions_by_capacity_provider.return_value = {
            "FunctionVersions": [{"FunctionArn": "arn:aws:lambda:us-east-1:123:function:fn-a:$LATEST.PUBLISHED"}],
        }
        lambda_client.get_function_scaling_config.return_value = {
            "AppliedFunctionScalingConfig": {
                "MinExecutionEnvironments": 10,
                "MaxExecutionEnvironments": 50,
            }
        }

        custom.scale_down("test-cp")

        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName="arn:aws:lambda:us-east-1:123:function:fn-a",
            Qualifier="$LATEST.PUBLISHED",
            FunctionScalingConfig={
                "MinExecutionEnvironments": 5,
                "MaxExecutionEnvironments": 20,
            },
        )


class TestShapeScaleUp:

    def test_restores_original_values(self, strategy, lambda_client):
        snapshot = {
            "functions": {
                "arn:aws:lambda:us-east-1:123:function:fn-a": {"min": 10, "max": 50}
            }
        }

        strategy.scale_up("test-cp", snapshot)

        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName="arn:aws:lambda:us-east-1:123:function:fn-a",
            Qualifier="$LATEST.PUBLISHED",
            FunctionScalingConfig={
                "MinExecutionEnvironments": 10,
                "MaxExecutionEnvironments": 50,
            },
        )

    def test_restores_multiple_functions(self, strategy, lambda_client):
        snapshot = {
            "functions": {
                "arn:aws:lambda:us-east-1:123:function:fn-a": {"min": 10, "max": 50},
                "arn:aws:lambda:us-east-1:123:function:fn-b": {"min": 5, "max": 30},
            }
        }

        strategy.scale_up("test-cp", snapshot)

        assert lambda_client.put_function_scaling_config.call_count == 2

    def test_enforces_nonzero_min_when_zero(self, strategy, lambda_client):
        snapshot = {
            "functions": {
                "arn:aws:lambda:us-east-1:123:function:fn-a": {"min": 0, "max": 50}
            }
        }

        strategy.scale_up("test-cp", snapshot)

        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName="arn:aws:lambda:us-east-1:123:function:fn-a",
            Qualifier="$LATEST.PUBLISHED",
            FunctionScalingConfig={
                "MinExecutionEnvironments": 1,
                "MaxExecutionEnvironments": 50,
            },
        )

    def test_enforces_nonzero_when_both_zero(self, strategy, lambda_client):
        snapshot = {
            "functions": {
                "arn:aws:lambda:us-east-1:123:function:fn-a": {"min": 0, "max": 0}
            }
        }

        strategy.scale_up("test-cp", snapshot)

        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName="arn:aws:lambda:us-east-1:123:function:fn-a",
            Qualifier="$LATEST.PUBLISHED",
            FunctionScalingConfig={
                "MinExecutionEnvironments": 1,
                "MaxExecutionEnvironments": 1,
            },
        )

    def test_skips_deleted_functions(self, strategy, lambda_client):
        ResourceNotFound = type("ResourceNotFoundException", (Exception,), {})
        lambda_client.exceptions.ResourceNotFoundException = ResourceNotFound
        lambda_client.put_function_scaling_config.side_effect = ResourceNotFound("gone")

        snapshot = {
            "functions": {
                "arn:aws:lambda:us-east-1:123:function:fn-a": {"min": 10, "max": 50}
            }
        }

        strategy.scale_up("test-cp", snapshot)  # Should not raise

    def test_empty_snapshot(self, strategy, lambda_client):
        snapshot = {"functions": {}}

        strategy.scale_up("test-cp", snapshot)

        lambda_client.put_function_scaling_config.assert_not_called()

    def test_swaps_inverted_min_max(self, strategy, lambda_client):
        snapshot = {
            "functions": {
                "arn:aws:lambda:us-east-1:123:function:fn-a": {"min": 50, "max": 10}
            }
        }

        strategy.scale_up("test-cp", snapshot)

        lambda_client.put_function_scaling_config.assert_called_once_with(
            FunctionName="arn:aws:lambda:us-east-1:123:function:fn-a",
            Qualifier="$LATEST.PUBLISHED",
            FunctionScalingConfig={
                "MinExecutionEnvironments": 10,
                "MaxExecutionEnvironments": 50,
            },
        )
