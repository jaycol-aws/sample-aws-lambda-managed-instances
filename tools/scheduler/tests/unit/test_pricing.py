# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Unit tests for the EC2 on-demand pricing module.

Tests the Price List API integration, static fallback table, and in-memory cache.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import scheduler.reporting.pricing as pricing_mod
from scheduler.reporting.pricing import (
    EC2_ON_DEMAND,
    _region_to_location,
    get_instance_info,
    get_on_demand_price,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the pricing cache and client before each test."""
    pricing_mod._price_cache.clear()
    pricing_mod._pricing_client = None
    yield
    pricing_mod._price_cache.clear()
    pricing_mod._pricing_client = None


def _price_list_response(rate: float) -> dict:
    """Build a minimal Price List API response."""
    product = {
        "terms": {
            "OnDemand": {
                "term1": {
                    "priceDimensions": {
                        "dim1": {
                            "pricePerUnit": {"USD": str(rate)},
                        }
                    }
                }
            }
        }
    }
    return {"PriceList": [json.dumps(product)]}


class TestGetOnDemandPrice:
    """Tests for the get_on_demand_price function."""

    @patch("scheduler.reporting.pricing._get_pricing_client")
    def test_returns_rate_from_price_list_api(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get_products.return_value = _price_list_response(0.192)

        rate = get_on_demand_price("m5.xlarge")

        assert rate == 0.192
        client.get_products.assert_called_once()

    @patch("scheduler.reporting.pricing._get_pricing_client")
    def test_falls_back_to_static_table_on_api_failure(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get_products.side_effect = Exception("throttled")

        rate = get_on_demand_price("m5.xlarge")

        assert rate == EC2_ON_DEMAND["m5.xlarge"]["rate"]

    @patch("scheduler.reporting.pricing._get_pricing_client")
    def test_falls_back_to_static_on_empty_results(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get_products.return_value = {"PriceList": []}

        rate = get_on_demand_price("m5.xlarge")

        assert rate == EC2_ON_DEMAND["m5.xlarge"]["rate"]

    @patch("scheduler.reporting.pricing._get_pricing_client")
    def test_returns_none_for_unknown_instance_type(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get_products.return_value = {"PriceList": []}

        rate = get_on_demand_price("z99.mega")

        assert rate is None

    @patch("scheduler.reporting.pricing._get_pricing_client")
    def test_caches_results_in_memory(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get_products.return_value = _price_list_response(0.192)

        first = get_on_demand_price("m5.xlarge")
        second = get_on_demand_price("m5.xlarge")

        assert first == second == 0.192
        assert client.get_products.call_count == 1

    @patch("scheduler.reporting.pricing._get_pricing_client")
    def test_cache_is_region_specific(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get_products.side_effect = [
            _price_list_response(0.192),
            _price_list_response(0.200),
        ]

        rate_syd = get_on_demand_price("m5.xlarge", region="ap-southeast-2")
        rate_iad = get_on_demand_price("m5.xlarge", region="us-east-1")

        assert rate_syd == 0.192
        assert rate_iad == 0.200
        assert client.get_products.call_count == 2


class TestRegionToLocation:
    """Tests for the region-to-location mapping."""

    def test_known_region(self):
        assert _region_to_location("us-east-1") == "US East (N. Virginia)"
        assert _region_to_location("ap-southeast-2") == "Asia Pacific (Sydney)"

    def test_unknown_region_returns_none(self):
        assert _region_to_location("mars-west-1") is None


class TestGetInstanceInfo:
    """Tests for the static table lookup."""

    def test_known_instance_type(self):
        info = get_instance_info("m5.xlarge")
        assert info is not None
        assert info["vcpus"] == 4
        assert info["rate"] == 0.192

    def test_unknown_instance_type(self):
        assert get_instance_info("z99.mega") is None
