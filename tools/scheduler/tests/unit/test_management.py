# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the Management API Lambda handler.

Injects mock dependencies into the management module globals to test
each route handler in isolation.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import scheduler.management as mgmt

MODULE = "scheduler.management"


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


def _valid_registration_body(cp_name="test-cp"):
    return {
        "capacityProviderName": cp_name,
        "strategy": "shape",
        "scaleDownSchedule": "cron(0 22 ? * MON-FRI *)",
        "scaleUpSchedule": "cron(0 6 ? * MON-FRI *)",
    }


class TestRouting:
    """Tests for the request router."""

    def test_unknown_route_returns_404(self):
        """Return 404 for unrecognised paths."""
        # Act
        result = mgmt.handler(_api_event("GET", "/unknown"), _lambda_context())

        # Assert
        assert result["statusCode"] == 404

    def test_unhandled_exception_returns_500(self, inject_mocks):
        """Return 500 when an unhandled exception occurs."""
        # Arrange
        inject_mocks["repo"].list_registrations.side_effect = RuntimeError("boom")

        # Act
        result = mgmt.handler(_api_event("GET", "/registrations"), _lambda_context())

        # Assert
        assert result["statusCode"] == 500


class TestCreateRegistration:
    """Tests for POST /registrations."""

    def test_creates_registration_successfully(self, inject_mocks):
        """Happy path: validates CP, creates schedules, stores registration."""
        # Arrange
        inject_mocks["scheduler_client"].create_schedule.return_value = {
            "ScheduleArn": "arn:schedule"
        }

        event = _api_event("POST", "/registrations", body=_valid_registration_body())

        # Act
        result = mgmt.handler(event, _lambda_context())

        # Assert
        assert result["statusCode"] == 201
        body = json.loads(result["body"])
        assert body["capacity_provider_name"] == "test-cp"
        assert body["strategy"] == "shape"
        inject_mocks["lambda_client"].get_capacity_provider.assert_called_once()
        assert inject_mocks["scheduler_client"].create_schedule.call_count == 2
        inject_mocks["repo"].create_registration.assert_called_once()

    def test_rejects_missing_body(self):
        """Return 400 when body is missing."""
        # Act
        result = mgmt.handler(_api_event("POST", "/registrations"), _lambda_context())

        # Assert
        assert result["statusCode"] == 400

    def test_rejects_invalid_strategy(self):
        """Return 400 for unrecognised strategy."""
        # Arrange
        body = _valid_registration_body()
        body["strategy"] = "destroy"

        # Act
        result = mgmt.handler(_api_event("POST", "/registrations", body=body), _lambda_context())

        # Assert
        assert result["statusCode"] == 400

    def test_rejects_missing_fields(self):
        """Return 400 when required fields are missing."""
        # Act
        result = mgmt.handler(
            _api_event("POST", "/registrations", body={"strategy": "shape"}), _lambda_context()
        )

        # Assert
        assert result["statusCode"] == 400

    def test_returns_404_for_nonexistent_cp(self, inject_mocks):
        """Return 404 when GetCapacityProvider raises ResourceNotFoundException."""
        # Arrange
        class ResourceNotFoundException(Exception):
            pass

        inject_mocks["lambda_client"].exceptions.ResourceNotFoundException = ResourceNotFoundException
        inject_mocks["lambda_client"].get_capacity_provider.side_effect = ResourceNotFoundException(
            "not found"
        )

        # Act
        result = mgmt.handler(
            _api_event("POST", "/registrations", body=_valid_registration_body()), _lambda_context()
        )

        # Assert
        assert result["statusCode"] == 404

    def test_returns_409_for_duplicate(self, inject_mocks):
        """Return 409 and clean up schedules when CP is already registered."""
        # Arrange
        inject_mocks["scheduler_client"].create_schedule.return_value = {
            "ScheduleArn": "arn:schedule"
        }
        from scheduler.state.repository import RegistrationExistsError
        inject_mocks["repo"].create_registration.side_effect = RegistrationExistsError("duplicate")

        # Act
        result = mgmt.handler(
            _api_event("POST", "/registrations", body=_valid_registration_body()), _lambda_context()
        )

        # Assert
        assert result["statusCode"] == 409
        assert inject_mocks["scheduler_client"].delete_schedule.call_count == 2


class TestListRegistrations:
    """Tests for GET /registrations."""

    def test_returns_all_registrations(self, inject_mocks):
        """Return list of all registrations."""
        # Arrange
        inject_mocks["repo"].list_registrations.return_value = [
            {"cp_name": "cp-1"}, {"cp_name": "cp-2"}
        ]

        # Act
        result = mgmt.handler(_api_event("GET", "/registrations"), _lambda_context())

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert len(body["registrations"]) == 2


class TestGetRegistration:
    """Tests for GET /registrations/{cpName}."""

    def test_returns_registration(self, inject_mocks):
        """Return registration when found."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {"cp_name": "test-cp"}

        # Act
        result = mgmt.handler(
            _api_event("GET", "/registrations/test-cp", path_params={"cpName": "test-cp"}),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 200

    def test_returns_404_when_not_found(self, inject_mocks):
        """Return 404 when registration doesn't exist."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = None

        # Act
        result = mgmt.handler(
            _api_event("GET", "/registrations/unknown", path_params={"cpName": "unknown"}),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 404


class TestUpdateRegistration:
    """Tests for PUT /registrations/{cpName}."""

    def test_updates_strategy(self, inject_mocks):
        """Update strategy on an active registration."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "cp_name": "test-cp", "state": "active"
        }
        inject_mocks["repo"].update_registration.return_value = {
            "cp_name": "test-cp", "strategy": "pause"
        }

        # Act
        result = mgmt.handler(
            _api_event(
                "PUT", "/registrations/test-cp",
                body={"strategy": "pause"},
                path_params={"cpName": "test-cp"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 200

    def test_updates_schedule_expressions(self, inject_mocks):
        """Update cron expressions and EventBridge schedules."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "cp_name": "test-cp", "state": "active"
        }
        inject_mocks["repo"].update_registration.return_value = {"cp_name": "test-cp"}

        body = {
            "scaleDownSchedule": "cron(0 23 ? * * *)",
            "scaleUpSchedule": "cron(0 7 ? * * *)",
        }

        # Act
        result = mgmt.handler(
            _api_event(
                "PUT", "/registrations/test-cp",
                body=body,
                path_params={"cpName": "test-cp"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 200
        assert inject_mocks["scheduler_client"].update_schedule.call_count == 2

    def test_rejects_update_while_scaled_down(self, inject_mocks):
        """Return 409 when trying to update a scaled-down registration."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "cp_name": "test-cp", "state": "scaled-down"
        }

        # Act
        result = mgmt.handler(
            _api_event(
                "PUT", "/registrations/test-cp",
                body={"strategy": "pause"},
                path_params={"cpName": "test-cp"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 409

    def test_rejects_invalid_strategy(self, inject_mocks):
        """Return 400 for invalid strategy value."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "cp_name": "test-cp", "state": "active"
        }

        # Act
        result = mgmt.handler(
            _api_event(
                "PUT", "/registrations/test-cp",
                body={"strategy": "nuke"},
                path_params={"cpName": "test-cp"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 400

    def test_rejects_empty_update(self, inject_mocks):
        """Return 400 when no valid update fields are provided."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "cp_name": "test-cp", "state": "active"
        }

        # Act
        result = mgmt.handler(
            _api_event(
                "PUT", "/registrations/test-cp",
                body={"foo": "bar"},
                path_params={"cpName": "test-cp"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 400


class TestDeleteRegistration:
    """Tests for DELETE /registrations/{cpName}."""

    def test_deletes_active_registration(self, inject_mocks):
        """Delete an active registration and its schedules."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "cp_name": "test-cp", "state": "active"
        }

        # Act
        result = mgmt.handler(
            _api_event(
                "DELETE", "/registrations/test-cp",
                path_params={"cpName": "test-cp"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 204
        assert inject_mocks["scheduler_client"].delete_schedule.call_count == 2
        inject_mocks["repo"].delete_registration.assert_called_once_with("test-cp")

    def test_scales_up_before_deleting_scaled_down(self, inject_mocks):
        """Perform scale-up before deletion when CP is scaled down."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = {
            "cp_name": "test-cp", "state": "scaled-down"
        }

        with patch("scheduler.executor._scale_up") as mock_scale_up:
            # Act
            result = mgmt.handler(
                _api_event(
                    "DELETE", "/registrations/test-cp",
                    path_params={"cpName": "test-cp"},
                ),
                _lambda_context(),
            )

        # Assert
        assert result["statusCode"] == 204
        mock_scale_up.assert_called_once_with("test-cp")
        inject_mocks["repo"].delete_registration.assert_called_once()

    def test_returns_404_when_not_found(self, inject_mocks):
        """Return 404 when registration doesn't exist."""
        # Arrange
        inject_mocks["repo"].get_registration.return_value = None

        # Act
        result = mgmt.handler(
            _api_event(
                "DELETE", "/registrations/unknown",
                path_params={"cpName": "unknown"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 404


class TestReports:
    """Tests for GET /reports and GET /reports/{cpName}."""

    def test_cp_report(self, inject_mocks):
        """Return cost report for a specific CP."""
        # Arrange
        inject_mocks["repo"].query_cost_events.return_value = [
            {"estimated_savings_usd": "10.00", "duration_hours": "8"}
        ]

        # Act
        result = mgmt.handler(
            _api_event(
                "GET", "/reports/test-cp",
                path_params={"cpName": "test-cp"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total_estimated_savings_usd"] == 10.0

    def test_cp_report_with_date_range(self, inject_mocks):
        """Forward date range params to the report generator."""
        # Arrange
        inject_mocks["repo"].query_cost_events.return_value = []

        # Act
        result = mgmt.handler(
            _api_event(
                "GET", "/reports/test-cp",
                path_params={"cpName": "test-cp"},
                query_params={"start": "2026-01-01", "end": "2026-02-01"},
            ),
            _lambda_context(),
        )

        # Assert
        assert result["statusCode"] == 200
        inject_mocks["repo"].query_cost_events.assert_called_once_with(
            "test-cp", "2026-01-01", "2026-02-01"
        )

    def test_aggregate_report(self, inject_mocks):
        """Return aggregate report across all CPs."""
        # Arrange
        inject_mocks["repo"].list_registrations.return_value = [{"capacity_provider_name": "cp-1"}]
        inject_mocks["repo"].query_cost_events.return_value = [
            {"estimated_savings_usd": "25.00", "duration_hours": "12"}
        ]

        # Act
        result = mgmt.handler(_api_event("GET", "/reports"), _lambda_context())

        # Assert
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total_estimated_savings_usd"] == 25.0
