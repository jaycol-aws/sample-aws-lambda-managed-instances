# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for cost reporting: savings calculation and reports.

Uses unittest.mock for the Price List API pricing lookups.
"""

from unittest.mock import MagicMock, patch

from scheduler.reporting.cost import (
    DISCLAIMER,
    LMI_MANAGEMENT_FEE_MULTIPLIER,
    calculate_savings,
    generate_aggregate_report,
    generate_cp_report,
)


class TestCalculateSavings:
    """Tests for the standalone calculate_savings function using fleet snapshot format."""

    @patch("scheduler.reporting.cost.get_on_demand_price")
    def test_single_instance_type(self, mock_price):
        mock_price.return_value = 0.192  # m5.xlarge
        baseline = {"instance_types": {"m5.xlarge": 2}}

        result = calculate_savings(baseline, duration_hours=10.0)

        expected_raw = 2 * 0.192 * 10.0
        expected_total = round(expected_raw * LMI_MANAGEMENT_FEE_MULTIPLIER, 2)
        assert result["estimated_savings_usd"] == expected_total
        assert result["duration_hours"] == 10.0
        assert "m5.xlarge" in result["breakdown"]
        assert result["breakdown"]["m5.xlarge"]["instance_count"] == 2
        assert result["breakdown"]["m5.xlarge"]["hourly_rate"] == 0.192
        assert result["disclaimer"] == DISCLAIMER

    @patch("scheduler.reporting.cost.get_on_demand_price")
    def test_multiple_instance_types(self, mock_price):
        mock_price.side_effect = lambda t: {"m5.xlarge": 0.192, "c6i.large": 0.085}.get(t)
        baseline = {"instance_types": {"m5.xlarge": 3, "c6i.large": 2}}

        result = calculate_savings(baseline, duration_hours=8.0)

        assert len(result["breakdown"]) == 2
        assert result["estimated_savings_usd"] > 0
        assert result["unknown_instance_types"] == []

    @patch("scheduler.reporting.cost.get_on_demand_price")
    def test_unknown_instance_type_excluded(self, mock_price):
        mock_price.return_value = None
        baseline = {"instance_types": {"z99.mega": 2}}

        result = calculate_savings(baseline, duration_hours=10.0)

        assert result["estimated_savings_usd"] == 0
        assert "z99.mega" in result["unknown_instance_types"]
        assert result["breakdown"] == {}

    @patch("scheduler.reporting.cost.get_on_demand_price")
    def test_mixed_known_and_unknown(self, mock_price):
        mock_price.side_effect = lambda t: 0.192 if t == "m5.xlarge" else None
        baseline = {"instance_types": {"m5.xlarge": 2, "z99.mega": 1}}

        result = calculate_savings(baseline, duration_hours=5.0)

        assert result["estimated_savings_usd"] > 0
        assert "z99.mega" in result["unknown_instance_types"]
        assert "m5.xlarge" in result["breakdown"]

    @patch("scheduler.reporting.cost.get_on_demand_price")
    def test_zero_duration(self, mock_price):
        mock_price.return_value = 0.192
        baseline = {"instance_types": {"m5.xlarge": 2}}

        result = calculate_savings(baseline, duration_hours=0.0)

        assert result["estimated_savings_usd"] == 0

    @patch("scheduler.reporting.cost.get_on_demand_price")
    def test_empty_instance_types(self, mock_price):
        baseline = {"instance_types": {}}

        result = calculate_savings(baseline, duration_hours=10.0)

        assert result["estimated_savings_usd"] == 0
        assert result["breakdown"] == {}
        mock_price.assert_not_called()

    @patch("scheduler.reporting.cost.get_on_demand_price")
    def test_breakdown_contains_expected_fields(self, mock_price):
        mock_price.return_value = 0.170
        baseline = {"instance_types": {"c6i.xlarge": 4}}

        result = calculate_savings(baseline, duration_hours=1.0)

        entry = result["breakdown"]["c6i.xlarge"]
        assert "instance_count" in entry
        assert "hourly_rate" in entry
        assert "raw_savings_usd" in entry
        assert "with_lmi_fee_usd" in entry
        assert entry["instance_count"] == 4


class TestCpReport:
    """Tests for per-capacity-provider report generation."""

    def test_aggregates_cost_events(self):
        repo = MagicMock()
        repo.query_cost_events.return_value = [
            {"estimated_savings_usd": "10.50", "duration_hours": "8"},
            {"estimated_savings_usd": "5.25", "duration_hours": "4"},
        ]

        result = generate_cp_report(repo, "test-cp")

        assert result["total_estimated_savings_usd"] == 15.75
        assert result["total_hours_down"] == 12.0
        assert result["event_count"] == 2
        assert result["disclaimer"] == DISCLAIMER

    def test_empty_events(self):
        repo = MagicMock()
        repo.query_cost_events.return_value = []

        result = generate_cp_report(repo, "test-cp")

        assert result["total_estimated_savings_usd"] == 0
        assert result["event_count"] == 0

    def test_passes_date_range_to_repo(self):
        repo = MagicMock()
        repo.query_cost_events.return_value = []

        generate_cp_report(repo, "test-cp", "2026-01-01", "2026-02-01")

        repo.query_cost_events.assert_called_once_with("test-cp", "2026-01-01", "2026-02-01")


class TestAggregateReport:
    """Tests for aggregate report generation across all CPs."""

    def test_aggregates_across_cps(self):
        repo = MagicMock()
        repo.list_registrations.return_value = [
            {"capacity_provider_name": "cp-1"},
            {"capacity_provider_name": "cp-2"},
        ]
        repo.query_cost_events.side_effect = [
            [{"estimated_savings_usd": "10.00", "duration_hours": "8"}],
            [{"estimated_savings_usd": "20.00", "duration_hours": "12"}],
        ]

        result = generate_aggregate_report(repo)

        assert result["total_estimated_savings_usd"] == 30.0
        assert result["total_hours_down"] == 20.0
        assert len(result["capacity_providers"]) == 2
        assert result["disclaimer"] == DISCLAIMER

    def test_empty_registrations(self):
        repo = MagicMock()
        repo.list_registrations.return_value = []

        result = generate_aggregate_report(repo)

        assert result["total_estimated_savings_usd"] == 0
        assert result["capacity_providers"] == []

    def test_passes_date_range(self):
        repo = MagicMock()
        repo.list_registrations.return_value = [{"capacity_provider_name": "cp-1"}]
        repo.query_cost_events.return_value = []

        generate_aggregate_report(repo, "2026-01-01", "2026-02-01")

        repo.query_cost_events.assert_called_once_with("cp-1", "2026-01-01", "2026-02-01")
