# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the UI-related Management API endpoints.

Tests _list_capacity_providers and _trigger_action handlers added for the
scheduler UI. Follows the same mocking pattern as test_management.py.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

import scheduler.management as mgmt


def _lambda_context():
    ctx = MagicMock()
    ctx.function_name = "lmi-scheduler-management-test"
    ctx.memory_limit_in_mb = 256
    ctx.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:management"
    ctx.aws_request_id = "test-request-id"
    return ctx


@pytest.fixture(autouse=True)
def inject_mocks():
    """Inject mocked dependencies into the management module for every test."""
    mock_repo = MagicMock()
    mock_lambda = MagicMock()
    mock_scheduler = MagicMock()

    mgmt._repo = mock_repo
    mgmt._lambda_client = mock_lambda
    mgmt._scheduler_client = mock_scheduler
    mgmt._schedule_group = "test-group"
    mgmt._executor_fn_arn = "arn:aws:lambda:us-east-1:123456789:function:executor"
    mgmt._scheduler_role_arn = "arn:aws:iam::123456789:role/scheduler-role"
    mgmt._dlq_arn = "arn:aws:sqs:us-east-1:123456789:dlq"

    yield {
        "repo": mock_repo,
        "lambda_client": mock_lambda,
        "scheduler_client": mock_scheduler,
    }

    mgmt._repo = None
    mgmt._lambda_client = None
    mgmt._scheduler_client = None


def _api_event(method, path, body=None, path_params=None, query_params=None):
    event = {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "pathParameters": path_params,
        "queryStringParameters": query_params,
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


class TestListCapacityProviders:
    """Tests for the _list_capacity_providers handler."""

    def test_returns_capacity_providers(self, inject_mocks):
        """Return a list of CPs from a single-page response."""
        # Arrange
        inject_mocks["lambda_client"].list_capacity_providers.return_value = {
            "CapacityProviders": [
                {"CapacityProviderArn": "arn:aws:lambda:us-east-1:123:capacity-provider/cp-1", "State": "Active"},
                {"CapacityProviderArn": "arn:aws:lambda:us-east-1:123:capacity-provider/cp-2", "State": "Active"},
            ]
        }

        # Act
        result = mgmt.handler(
            _api_event("GET", "/capacity-providers"),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert len(body["capacityProviders"]) == 2
        assert body["capacityProviders"][0] == {"name": "cp-1", "arn": "arn:aws:lambda:us-east-1:123:capacity-provider/cp-1"}
        assert body["capacityProviders"][1] == {"name": "cp-2", "arn": "arn:aws:lambda:us-east-1:123:capacity-provider/cp-2"}

    def test_handles_pagination(self, inject_mocks):
        """Paginate through multiple pages of CPs."""
        # Arrange
        inject_mocks["lambda_client"].list_capacity_providers.side_effect = [
            {
                "CapacityProviders": [
                    {"CapacityProviderArn": "arn:aws:lambda:us-east-1:123:capacity-provider/cp-1", "State": "Active"},
                ],
                "NextMarker": "token-1",
            },
            {
                "CapacityProviders": [
                    {"CapacityProviderArn": "arn:aws:lambda:us-east-1:123:capacity-provider/cp-2", "State": "Active"},
                ],
            },
        ]

        # Act
        result = mgmt.handler(
            _api_event("GET", "/capacity-providers"),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert len(body["capacityProviders"]) == 2
        assert body["capacityProviders"][0]["name"] == "cp-1"
        assert body["capacityProviders"][1]["name"] == "cp-2"
        assert inject_mocks["lambda_client"].list_capacity_providers.call_count == 2

    def test_returns_empty_list_when_no_cps(self, inject_mocks):
        """Return an empty list when no capacity providers exist."""
        # Arrange
        inject_mocks["lambda_client"].list_capacity_providers.return_value = {
            "CapacityProviders": []
        }

        # Act
        result = mgmt.handler(
            _api_event("GET", "/capacity-providers"),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["capacityProviders"] == []


class TestTriggerAction:
    """Tests for the _trigger_action handler."""

    def test_successful_scale_down(self, inject_mocks):
        """Invoke executor for scale-down when registration exists."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "capacity_provider_name": "test-cp",
            "state": "active",
        }
        executor_response = {"status": "scaled-down", "capacity_provider": "test-cp"}
        inject_mocks["lambda_client"].invoke.return_value = {
            "Payload": io.BytesIO(json.dumps(executor_response).encode()),
        }

        # Act
        with patch.dict("os.environ", {"EXECUTOR_FUNCTION_ARN": "arn:executor"}):
            result = mgmt.handler(
                _api_event(
                    "POST",
                    "/registrations/test-cp/actions/scale-down",
                    path_params={"cpName": "test-cp"},
                ),
                _lambda_context(),
            )

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "scaled-down"
        inject_mocks["lambda_client"].invoke.assert_called_once_with(
            FunctionName="arn:executor",
            InvocationType="RequestResponse",
            Payload=json.dumps({"capacityProviderName": "test-cp", "action": "scale-down"}),
        )

    def test_successful_scale_up(self, inject_mocks):
        """Invoke executor for scale-up when registration exists."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "capacity_provider_name": "test-cp",
            "state": "scaled-down",
        }
        executor_response = {"status": "scaled-up", "capacity_provider": "test-cp"}
        inject_mocks["lambda_client"].invoke.return_value = {
            "Payload": io.BytesIO(json.dumps(executor_response).encode()),
        }

        # Act
        with patch.dict("os.environ", {"EXECUTOR_FUNCTION_ARN": "arn:executor"}):
            result = mgmt.handler(
                _api_event(
                    "POST",
                    "/registrations/test-cp/actions/scale-up",
                    path_params={"cpName": "test-cp"},
                ),
                _lambda_context(),
            )

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "scaled-up"
        inject_mocks["lambda_client"].invoke.assert_called_once_with(
            FunctionName="arn:executor",
            InvocationType="RequestResponse",
            Payload=json.dumps({"capacityProviderName": "test-cp", "action": "scale-up"}),
        )

    def test_returns_404_when_registration_not_found(self, inject_mocks):
        """Return 404 when the capacity provider is not registered."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = None

        # Act
        result = mgmt.handler(
            _api_event(
                "POST",
                "/registrations/unknown-cp/actions/scale-down",
                path_params={"cpName": "unknown-cp"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 404
        body = json.loads(result["body"])
        assert "not found" in body["error"].lower()
        inject_mocks["lambda_client"].invoke.assert_not_called()

    def test_returns_executor_error(self, inject_mocks):
        """Return the executor's error response when it fails."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "capacity_provider_name": "test-cp",
            "state": "active",
        }
        executor_response = {"error": "No registration found", "errorType": "ValueError"}
        inject_mocks["lambda_client"].invoke.return_value = {
            "Payload": io.BytesIO(json.dumps(executor_response).encode()),
        }

        # Act
        with patch.dict("os.environ", {"EXECUTOR_FUNCTION_ARN": "arn:executor"}):
            result = mgmt.handler(
                _api_event(
                    "POST",
                    "/registrations/test-cp/actions/scale-down",
                    path_params={"cpName": "test-cp"},
                ),
                _lambda_context(),
            )

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "error" in body
        assert body["errorType"] == "ValueError"
