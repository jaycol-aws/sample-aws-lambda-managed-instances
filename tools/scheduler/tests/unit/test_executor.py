# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the Executor Lambda handler.

Uses unittest.mock to inject mock dependencies into the executor module's
globals (_repo, _lambda_client, _ec2_client) so each test runs in
isolation without real AWS connections.
"""

from unittest.mock import MagicMock, patch

import pytest

import scheduler.executor as executor_mod

MODULE = "scheduler.executor"


def _lambda_context():
    ctx = MagicMock()
    ctx.function_name = "lmi-scheduler-executor-test"
    ctx.memory_limit_in_mb = 256
    ctx.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:executor"
    ctx.aws_request_id = "test-request-id"
    return ctx


@pytest.fixture(autouse=True)
def inject_mocks():
    """Inject mocked dependencies into the executor module for every test."""
    mock_repo = MagicMock()
    mock_lambda_client = MagicMock()
    mock_ec2_client = MagicMock()

    executor_mod._repo = mock_repo
    executor_mod._lambda_client = mock_lambda_client
    executor_mod._ec2_client = mock_ec2_client

    yield {
        "repo": mock_repo,
        "lambda_client": mock_lambda_client,
        "ec2_client": mock_ec2_client,
    }

    executor_mod._repo = None
    executor_mod._lambda_client = None
    executor_mod._ec2_client = None


def _registration(state="active", strategy="shape"):
    return {
        "cp_name": "test-cp",
        "strategy": strategy,
        "state": state,
        "updated_at": "2026-02-13T02:00:00+00:00",
    }


class TestHandlerValidation:
    """Tests for payload validation in the handler entry point."""

    def test_missing_capacity_provider_raises(self):
        result = executor_mod.handler({"action": "scale-down"}, _lambda_context())
        assert "error" in result and "Invalid payload" in result["error"]
        assert result["errorType"] == "ValueError"

    def test_invalid_action_raises(self):
        result = executor_mod.handler(
            {"capacityProviderName": "cp", "action": "restart"}, _lambda_context()
        )
        assert "error" in result and "Invalid payload" in result["error"]
        assert result["errorType"] == "ValueError"

    def test_valid_scale_down_delegates(self, inject_mocks):
        repo = inject_mocks["repo"]
        repo.get_registration.return_value = _registration()
        repo.update_registration_state.return_value = True

        with patch(f"{MODULE}._snapshot_fleet", return_value=None), \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            strategy.scale_down.return_value = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
            mock_get.return_value = strategy

            result = executor_mod.handler(
                {"capacityProviderName": "test-cp", "action": "scale-down"}, _lambda_context()
            )

        assert result["status"] == "scaled-down"

    def test_valid_scale_up_delegates(self, inject_mocks):
        repo = inject_mocks["repo"]
        repo.get_registration.return_value = _registration(state="scaled-down")
        repo.get_snapshot.return_value = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
        repo.update_registration_state.return_value = True

        with patch(f"{MODULE}._calculate_and_store_savings", return_value=None), \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            mock_get.return_value = strategy

            result = executor_mod.handler(
                {"capacityProviderName": "test-cp", "action": "scale-up"}, _lambda_context()
            )

        assert result["status"] == "scaled-up"


class TestScaleDown:
    """Tests for the scale-down orchestration flow."""

    def test_no_registration_raises(self, inject_mocks):
        inject_mocks["repo"].get_registration.return_value = None

        with pytest.raises(ValueError, match="No registration found"):
            executor_mod._scale_down("unknown-cp")

    def test_already_scaled_down_returns_noop(self, inject_mocks):
        inject_mocks["repo"].get_registration.return_value = _registration(state="scaled-down")

        result = executor_mod._scale_down("test-cp")

        assert result["status"] == "already-scaled-down"
        inject_mocks["repo"].save_snapshot.assert_not_called()

    def test_shape_scale_down_full_flow(self, inject_mocks):
        """Fleet snapshot captured and stored for shape strategy."""
        repo = inject_mocks["repo"]
        repo.get_registration.return_value = _registration()
        repo.update_registration_state.return_value = True

        fleet = {"instance_types": {"m5.xlarge": 2}, "total_instances": 2, "captured_at": "2026-02-13T20:00:00+00:00"}
        fn_snapshot = {"functions": {"arn:fn1": {"min": 10, "max": 50}}}

        with patch(f"{MODULE}._snapshot_fleet", return_value=fleet), \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            strategy.scale_down.return_value = fn_snapshot
            mock_get.return_value = strategy

            result = executor_mod._scale_down("test-cp")

        assert result["status"] == "scaled-down"
        assert result["strategy"] == "shape"
        assert result["baseline_captured"] is True
        strategy.scale_down.assert_called_once_with("test-cp")
        repo.save_snapshot.assert_called_once_with("test-cp", fn_snapshot)
        repo.save_cost_baseline.assert_called_once_with("test-cp", fleet)
        repo.update_registration_state.assert_called_once_with("test-cp", "active", "scaled-down")

    def test_pause_scale_down_full_flow(self, inject_mocks):
        """Fleet snapshot captured and stored for pause strategy (same path as shape)."""
        repo = inject_mocks["repo"]
        snapshot = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
        repo.get_registration.return_value = _registration(strategy="pause")
        repo.update_registration_state.return_value = True

        fleet = {"instance_types": {"m6i.xlarge": 3}, "total_instances": 3, "captured_at": "2026-02-13T20:00:00+00:00"}

        with patch(f"{MODULE}._snapshot_fleet", return_value=fleet), \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            strategy.scale_down.return_value = snapshot
            mock_get.return_value = strategy

            result = executor_mod._scale_down("test-cp")

        assert result["status"] == "scaled-down"
        assert result["strategy"] == "pause"
        assert result["baseline_captured"] is True
        repo.save_snapshot.assert_called_once_with("test-cp", snapshot)
        repo.save_cost_baseline.assert_called_once_with("test-cp", fleet)

    def test_fleet_snapshot_failure_does_not_block(self, inject_mocks):
        """Scale-down succeeds even when fleet snapshot fails."""
        repo = inject_mocks["repo"]
        repo.get_registration.return_value = _registration()
        repo.update_registration_state.return_value = True

        with patch(f"{MODULE}._snapshot_fleet", return_value=None), \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            strategy.scale_down.return_value = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
            mock_get.return_value = strategy

            result = executor_mod._scale_down("test-cp")

        assert result["status"] == "scaled-down"
        assert result["baseline_captured"] is False
        repo.save_cost_baseline.assert_not_called()

    def test_concurrent_scale_down_returns_noop(self, inject_mocks):
        repo = inject_mocks["repo"]
        repo.get_registration.return_value = _registration()
        repo.update_registration_state.return_value = False

        with patch(f"{MODULE}._snapshot_fleet", return_value=None), \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            strategy.scale_down.return_value = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
            mock_get.return_value = strategy

            result = executor_mod._scale_down("test-cp")

        assert result["status"] == "concurrent-noop"

    def test_unknown_strategy_raises(self, inject_mocks):
        reg = _registration()
        reg["strategy"] = "destroy"
        inject_mocks["repo"].get_registration.return_value = reg

        with pytest.raises(ValueError, match="Unknown strategy"):
            executor_mod._scale_down("test-cp")


class TestScaleUp:
    """Tests for the scale-up orchestration flow."""

    def test_no_registration_raises(self, inject_mocks):
        inject_mocks["repo"].get_registration.return_value = None

        with pytest.raises(ValueError, match="No registration found"):
            executor_mod._scale_up("unknown-cp")

    def test_already_active_returns_noop(self, inject_mocks):
        inject_mocks["repo"].get_registration.return_value = _registration(state="active")

        result = executor_mod._scale_up("test-cp")

        assert result["status"] == "already-active"
        inject_mocks["repo"].get_snapshot.assert_not_called()

    def test_no_snapshot_raises(self, inject_mocks):
        repo = inject_mocks["repo"]
        repo.get_registration.return_value = _registration(state="scaled-down")
        repo.get_snapshot.return_value = None

        with pytest.raises(ValueError, match="No snapshot found"):
            executor_mod._scale_up("test-cp")

    def test_shape_scale_up_full_flow(self, inject_mocks):
        """Scale-up restores config and calculates savings from fleet snapshot."""
        repo = inject_mocks["repo"]
        snapshot = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
        savings = {"estimated_savings_usd": 5.06, "duration_hours": 11.0}

        repo.get_registration.return_value = _registration(state="scaled-down")
        repo.get_snapshot.return_value = snapshot
        repo.update_registration_state.return_value = True

        with patch(f"{MODULE}._calculate_and_store_savings", return_value=savings) as mock_calc, \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            mock_get.return_value = strategy

            result = executor_mod._scale_up("test-cp")

        assert result["status"] == "scaled-up"
        assert result["strategy"] == "shape"
        assert result["estimated_savings"] == savings
        strategy.scale_up.assert_called_once_with("test-cp", snapshot)
        mock_calc.assert_called_once()
        repo.delete_snapshot.assert_called_once_with("test-cp")
        repo.update_registration_state.assert_called_once_with("test-cp", "scaled-down", "active")

    def test_pause_scale_up_full_flow(self, inject_mocks):
        """Pause scale-up also calculates savings (same path as shape)."""
        repo = inject_mocks["repo"]
        snapshot = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
        savings = {"estimated_savings_usd": 12.30, "duration_hours": 11.0}

        repo.get_registration.return_value = _registration(state="scaled-down", strategy="pause")
        repo.get_snapshot.return_value = snapshot
        repo.update_registration_state.return_value = True

        with patch(f"{MODULE}._calculate_and_store_savings", return_value=savings) as mock_calc, \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            mock_get.return_value = strategy

            result = executor_mod._scale_up("test-cp")

        assert result["status"] == "scaled-up"
        assert result["strategy"] == "pause"
        assert result["estimated_savings"] == savings
        strategy.scale_up.assert_called_once_with("test-cp", snapshot)
        mock_calc.assert_called_once()

    def test_savings_failure_does_not_block(self, inject_mocks):
        """Scale-up succeeds even when savings calculation fails."""
        repo = inject_mocks["repo"]
        repo.get_registration.return_value = _registration(state="scaled-down")
        repo.get_snapshot.return_value = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
        repo.update_registration_state.return_value = True

        with patch(f"{MODULE}._calculate_and_store_savings", return_value=None), \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            mock_get.return_value = strategy

            result = executor_mod._scale_up("test-cp")

        assert result["status"] == "scaled-up"
        assert result["estimated_savings"] is None

    def test_concurrent_scale_up_returns_noop(self, inject_mocks):
        repo = inject_mocks["repo"]
        repo.get_registration.return_value = _registration(state="scaled-down")
        repo.get_snapshot.return_value = {"functions": {"arn:fn1": {"min": 1, "max": 5}}}
        repo.update_registration_state.return_value = False

        with patch(f"{MODULE}._calculate_and_store_savings", return_value=None), \
             patch(f"{MODULE}._get_strategy") as mock_get:
            strategy = MagicMock()
            mock_get.return_value = strategy

            result = executor_mod._scale_up("test-cp")

        assert result["status"] == "concurrent-noop"
        repo.delete_snapshot.assert_called_once()
