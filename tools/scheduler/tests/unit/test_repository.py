# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the DynamoDB state repository.

Uses moto to mock DynamoDB. Tests cover all CRUD operations for registrations,
snapshots, and cost events, including conditional writes for concurrency safety.
"""

from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from scheduler.state.repository import (
    RegistrationExistsError,
    StateRepository,
)


@pytest.fixture
def dynamodb_table():
    """Create a mocked DynamoDB table matching the scheduler's schema."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="lmi-scheduler-test",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


@pytest.fixture
def repo(dynamodb_table):
    """Create a StateRepository backed by the mocked table."""
    return StateRepository(dynamodb_table)


def _sample_registration(**overrides):
    """Build a sample registration dict with sensible defaults."""
    defaults = {
        "capacity_provider_name": "test-cp",
        "strategy": "shape",
        "scale_down_cron": "cron(0 20 ? * MON-FRI *)",
        "scale_up_cron": "cron(0 7 ? * MON-FRI *)",
        "scale_down_schedule_arn": "arn:aws:scheduler:us-east-1:123456789012:schedule/lmi-scheduler/test-cp-down",
        "scale_up_schedule_arn": "arn:aws:scheduler:us-east-1:123456789012:schedule/lmi-scheduler/test-cp-up",
    }
    defaults.update(overrides)
    return defaults


class TestCreateRegistration:

    def test_creates_registration_with_active_state(self, repo):
        # Arrange
        registration = _sample_registration()

        # Act
        result = repo.create_registration(registration)

        # Assert
        assert result["capacity_provider_name"] == "test-cp"
        assert result["strategy"] == "shape"
        assert result["state"] == "active"
        assert result["PK"] == "CP#test-cp"
        assert result["SK"] == "REG"
        assert "created_at" in result
        assert "updated_at" in result

    def test_rejects_duplicate_registration(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())

        # Act / Assert
        with pytest.raises(RegistrationExistsError):
            repo.create_registration(_sample_registration())


class TestGetRegistration:

    def test_returns_registration_when_exists(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())

        # Act
        result = repo.get_registration("test-cp")

        # Assert
        assert result is not None
        assert result["capacity_provider_name"] == "test-cp"

    def test_returns_none_when_not_found(self, repo):
        # Act
        result = repo.get_registration("nonexistent")

        # Assert
        assert result is None


class TestListRegistrations:

    def test_returns_all_registrations(self, repo):
        # Arrange
        repo.create_registration(_sample_registration(capacity_provider_name="cp-1"))
        repo.create_registration(_sample_registration(capacity_provider_name="cp-2"))
        repo.create_registration(_sample_registration(capacity_provider_name="cp-3"))

        # Act
        results = repo.list_registrations()

        # Assert
        assert len(results) == 3
        names = {r["capacity_provider_name"] for r in results}
        assert names == {"cp-1", "cp-2", "cp-3"}

    def test_returns_empty_list_when_none_exist(self, repo):
        # Act
        results = repo.list_registrations()

        # Assert
        assert results == []

    def test_excludes_non_registration_items(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())
        repo.save_snapshot("test-cp", {"max_vcpu_count": 100})

        # Act
        results = repo.list_registrations()

        # Assert
        assert len(results) == 1
        assert results[0]["capacity_provider_name"] == "test-cp"


class TestUpdateRegistration:

    def test_updates_allowed_fields(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())

        # Act
        result = repo.update_registration("test-cp", {"strategy": "pause"})

        # Assert
        assert result["strategy"] == "pause"

    def test_returns_none_for_nonexistent_cp(self, repo):
        # Act
        result = repo.update_registration("nonexistent", {"strategy": "pause"})

        # Assert
        assert result is None

    def test_ignores_disallowed_fields(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())

        # Act
        result = repo.update_registration("test-cp", {"state": "hacked", "strategy": "pause"})

        # Assert
        assert result["strategy"] == "pause"
        assert result["state"] == "active"


class TestDeleteRegistration:

    def test_deletes_registration_and_snapshot(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())
        repo.save_snapshot("test-cp", {"max_vcpu_count": 100})

        # Act
        repo.delete_registration("test-cp")

        # Assert
        assert repo.get_registration("test-cp") is None
        assert repo.get_snapshot("test-cp") is None

    def test_succeeds_when_nothing_to_delete(self, repo):
        # Act / Assert — should not raise
        repo.delete_registration("nonexistent")


class TestUpdateRegistrationState:

    def test_transitions_active_to_scaled_down(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())

        # Act
        success = repo.update_registration_state("test-cp", "active", "scaled-down")

        # Assert
        assert success is True
        reg = repo.get_registration("test-cp")
        assert reg["state"] == "scaled-down"

    def test_transitions_scaled_down_to_active(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())
        repo.update_registration_state("test-cp", "active", "scaled-down")

        # Act
        success = repo.update_registration_state("test-cp", "scaled-down", "active")

        # Assert
        assert success is True
        reg = repo.get_registration("test-cp")
        assert reg["state"] == "active"

    def test_rejects_stale_state_transition(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())
        repo.update_registration_state("test-cp", "active", "scaled-down")

        # Act — try to transition from 'active' again (already 'scaled-down')
        success = repo.update_registration_state("test-cp", "active", "scaled-down")

        # Assert
        assert success is False

    def test_concurrent_transitions_one_wins(self, repo):
        # Arrange
        repo.create_registration(_sample_registration())

        # Act — simulate two concurrent scale-downs
        first = repo.update_registration_state("test-cp", "active", "scaled-down")
        second = repo.update_registration_state("test-cp", "active", "scaled-down")

        # Assert — first succeeds, second is a no-op
        assert first is True
        assert second is False
        assert repo.get_registration("test-cp")["state"] == "scaled-down"


class TestSnapshot:

    def test_save_and_get_shape_snapshot(self, repo):
        # Arrange
        snapshot = {"functions": {"arn:fn1": {"min": 5, "max": 50}}}

        # Act
        repo.save_snapshot("test-cp", snapshot)
        result = repo.get_snapshot("test-cp")

        # Assert
        assert result == {"functions": {"arn:fn1": {"min": 5, "max": 50}}}

    def test_save_and_get_pause_snapshot(self, repo):
        # Arrange
        snapshot = {
            "functions": {
                "arn:aws:lambda:us-east-1:123:function:fn-a": {"min": 3, "max": 100},
                "arn:aws:lambda:us-east-1:123:function:fn-b": {"min": 5, "max": 50},
            }
        }

        # Act
        repo.save_snapshot("test-cp", snapshot)
        result = repo.get_snapshot("test-cp")

        # Assert
        assert len(result["functions"]) == 2
        assert result["functions"]["arn:aws:lambda:us-east-1:123:function:fn-a"]["min"] == 3

    def test_overwrites_existing_snapshot(self, repo):
        # Arrange
        repo.save_snapshot("test-cp", {"max_vcpu_count": 100})

        # Act
        repo.save_snapshot("test-cp", {"max_vcpu_count": 200})
        result = repo.get_snapshot("test-cp")

        # Assert
        assert result["max_vcpu_count"] == 200

    def test_returns_none_when_no_snapshot(self, repo):
        # Act
        result = repo.get_snapshot("nonexistent")

        # Assert
        assert result is None

    def test_delete_snapshot(self, repo):
        # Arrange
        repo.save_snapshot("test-cp", {"max_vcpu_count": 100})

        # Act
        repo.delete_snapshot("test-cp")

        # Assert
        assert repo.get_snapshot("test-cp") is None

    def test_delete_nonexistent_snapshot_is_noop(self, repo):
        # Act / Assert — should not raise
        repo.delete_snapshot("nonexistent")


class TestCostBaseline:

    def test_save_and_get_baseline(self, repo):
        # Arrange — snapshot must exist first
        repo.save_snapshot("test-cp", {"max_vcpu_count": 100})
        baseline = {"total_vcpus": 8, "instance_types": {"m5.xlarge": 4}, "captured_at": "2026-02-13T20:00:00+00:00"}

        # Act
        repo.save_cost_baseline("test-cp", baseline)
        result = repo.get_cost_baseline("test-cp")

        # Assert
        assert result is not None
        assert result["total_vcpus"] == 8
        assert result["instance_types"]["m5.xlarge"] == 4

    def test_returns_none_when_no_snapshot(self, repo):
        # Act
        result = repo.get_cost_baseline("nonexistent")

        # Assert
        assert result is None

    def test_returns_none_when_no_baseline_stored(self, repo):
        # Arrange — snapshot exists but no baseline was saved
        repo.save_snapshot("test-cp", {"max_vcpu_count": 100})

        # Act
        result = repo.get_cost_baseline("test-cp")

        # Assert
        assert result is None

    def test_baseline_deleted_with_snapshot(self, repo):
        # Arrange
        repo.save_snapshot("test-cp", {"max_vcpu_count": 100})
        repo.save_cost_baseline("test-cp", {"total_vcpus": 8, "instance_types": {}})

        # Act
        repo.delete_snapshot("test-cp")

        # Assert
        assert repo.get_cost_baseline("test-cp") is None


class TestCostEvents:

    def test_save_and_query_cost_event(self, repo):
        # Arrange
        event = {
            "scale_down_time": "2026-02-13T20:00:00+00:00",
            "scale_up_time": "2026-02-14T07:00:00+00:00",
            "duration_hours": Decimal("11.0"),
            "baseline": {"total_vcpus": 8, "instance_types": {"m5.xlarge": 4}},
            "estimated_savings_usd": Decimal("5.06"),
        }

        # Act
        repo.save_cost_event("test-cp", event)
        results = repo.query_cost_events("test-cp")

        # Assert
        assert len(results) == 1
        assert results[0]["duration_hours"] == Decimal("11.0")
        assert results[0]["estimated_savings_usd"] == Decimal("5.06")

    def test_query_with_date_range(self, repo):
        # Arrange
        repo.save_cost_event("test-cp", {
            "scale_down_time": "2026-02-10T20:00:00+00:00",
            "scale_up_time": "2026-02-11T07:00:00+00:00",
            "duration_hours": Decimal("11"),
            "estimated_savings_usd": Decimal("5"),
        })
        repo.save_cost_event("test-cp", {
            "scale_down_time": "2026-02-13T20:00:00+00:00",
            "scale_up_time": "2026-02-14T07:00:00+00:00",
            "duration_hours": Decimal("11"),
            "estimated_savings_usd": Decimal("5"),
        })

        # Act — query only February 13+
        results = repo.query_cost_events("test-cp", start_date="2026-02-13")

        # Assert
        assert len(results) == 1
        assert results[0]["scale_up_time"] == "2026-02-14T07:00:00+00:00"

    def test_query_returns_empty_when_none_exist(self, repo):
        # Act
        results = repo.query_cost_events("test-cp")

        # Assert
        assert results == []

    def test_multiple_events_sorted_by_time(self, repo):
        # Arrange
        for day in ["10", "11", "12", "13"]:
            repo.save_cost_event("test-cp", {
                "scale_down_time": f"2026-02-{day}T20:00:00+00:00",
                "scale_up_time": f"2026-02-{day}T23:00:00+00:00",
                "duration_hours": Decimal("3"),
                "estimated_savings_usd": Decimal("1.5"),
            })

        # Act
        results = repo.query_cost_events("test-cp")

        # Assert
        assert len(results) == 4
        times = [r["scale_up_time"] for r in results]
        assert times == sorted(times)

    def test_query_with_end_date_only(self, repo):
        # Arrange
        repo.save_cost_event("test-cp", {
            "scale_down_time": "2026-02-10T20:00:00+00:00",
            "scale_up_time": "2026-02-11T07:00:00+00:00",
            "duration_hours": Decimal("11"),
            "estimated_savings_usd": Decimal("5"),
        })
        repo.save_cost_event("test-cp", {
            "scale_down_time": "2026-02-13T20:00:00+00:00",
            "scale_up_time": "2026-02-14T07:00:00+00:00",
            "duration_hours": Decimal("11"),
            "estimated_savings_usd": Decimal("5"),
        })

        # Act — query up to February 12
        results = repo.query_cost_events("test-cp", end_date="2026-02-12")

        # Assert
        assert len(results) == 1
        assert results[0]["scale_up_time"] == "2026-02-11T07:00:00+00:00"

    def test_events_isolated_per_capacity_provider(self, repo):
        # Arrange
        repo.save_cost_event("cp-1", {
            "scale_up_time": "2026-02-13T07:00:00+00:00",
            "duration_hours": Decimal("11"),
            "estimated_savings_usd": Decimal("5"),
        })
        repo.save_cost_event("cp-2", {
            "scale_up_time": "2026-02-13T07:00:00+00:00",
            "duration_hours": Decimal("11"),
            "estimated_savings_usd": Decimal("8"),
        })

        # Act
        results_1 = repo.query_cost_events("cp-1")
        results_2 = repo.query_cost_events("cp-2")

        # Assert
        assert len(results_1) == 1
        assert results_1[0]["estimated_savings_usd"] == Decimal("5")
        assert len(results_2) == 1
        assert results_2[0]["estimated_savings_usd"] == Decimal("8")
