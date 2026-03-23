#!/usr/bin/env python3
"""
Tests for LMI Candidate Finder — validates scoring, disqualifiers, and savings
estimates using mock CloudWatch data. No AWS credentials required.

Usage:
    python test_lmi_candidate_finder.py
"""

import sys
import os
import math
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

# Import the module under test
sys.path.insert(0, os.path.dirname(__file__))
import lmi_candidate_finder as lmi

# ---------------------------------------------------------------------------
# Fake pricing (us-east-1 representative values)
# ---------------------------------------------------------------------------
MOCK_PRICING = {
    "ec2": {
        "c7g.xlarge": 0.1450, "c7g.2xlarge": 0.2900,
        "m7g.xlarge": 0.1632, "m7g.2xlarge": 0.3264,
        "r7g.xlarge": 0.2134,
    },
    "lambda": {
        "gbsec_x86": 0.0000166667,
        "gbsec_arm": 0.0000133333,
        "request": 0.0000002,
    },
    "region": "us-east-1",
    "location": "US East (N. Virginia)",
}


def make_datapoints(metric, stat, value, days, period=86400):
    """Generate CloudWatch-style datapoints spanning `days` at `period` intervals."""
    now = datetime.now(timezone.utc)
    count = days if period == 86400 else days * 24
    return [{"Timestamp": now - timedelta(seconds=period * i), stat: value / count}
            for i in range(count)]


def make_hourly_points(active_hours, total_hours, total_sum):
    """Generate hourly invocation datapoints with `active_hours` having traffic."""
    now = datetime.now(timezone.utc)
    per_hour = total_sum / active_hours if active_hours > 0 else 0
    points = []
    for i in range(total_hours):
        if i < active_hours:
            points.append({"Timestamp": now - timedelta(hours=i), "Sum": per_hour})
        # inactive hours: no datapoint (CloudWatch omits zero-value periods)
    return points


class MockCW:
    """Mock CloudWatch client that returns pre-configured metric data."""

    def __init__(self, invocations=0, avg_duration_ms=0, peak_concurrency=0,
                 throttles=0, days=14, active_hours_pct=1.0):
        self.days = days
        total_hours = days * 24
        active_hours = int(total_hours * active_hours_pct)
        self._data = {
            ("Invocations", "Sum", 86400): make_datapoints("Invocations", "Sum", invocations, days),
            ("Invocations", "Sum", 3600): make_hourly_points(active_hours, total_hours, invocations),
            ("Duration", "Average", 86400): [{"Timestamp": datetime.now(timezone.utc), "Average": avg_duration_ms}] * days,
            ("ConcurrentExecutions", "Maximum", 86400): [{"Timestamp": datetime.now(timezone.utc), "Maximum": peak_concurrency}] * days,
            ("Throttles", "Sum", 86400): make_datapoints("Throttles", "Sum", throttles, days),
        }

    def get_metric_statistics(self, **kwargs):
        metric = kwargs["MetricName"]
        stat = kwargs["Statistics"][0]
        period = kwargs["Period"]
        return {"Datapoints": self._data.get((metric, stat, period), [])}


class MockLambda:
    """Mock Lambda client."""

    def __init__(self, has_provisioned=False):
        self.has_provisioned = has_provisioned

    def list_provisioned_concurrency_configs(self, **kwargs):
        if self.has_provisioned:
            return {"ProvisionedConcurrencyConfigs": [{"FunctionArn": "arn:fake"}]}
        return {"ProvisionedConcurrencyConfigs": []}


def make_func(name, runtime="python3.13", memory=512, arch="arm64", timeout=30):
    """Create a Lambda function configuration dict."""
    return {
        "FunctionName": name,
        "Runtime": runtime,
        "MemorySize": memory,
        "Architectures": [arch],
        "Timeout": timeout,
    }


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


# ===========================================================================
# TEST SUITE 1: Strong LMI candidate (should score 60+)
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 1: STRONG LMI CANDIDATE")
print("  High-volume Java API, 50M inv/month, 2s avg duration,")
print("  100 peak concurrency, 1024 MB, steady 24/7 traffic, arm64")
print("=" * 70)

func_strong = make_func("payment-api", runtime="java21", memory=1024, arch="arm64")
cw_strong = MockCW(
    invocations=50_000_000 * (14 / 30),  # scale to 14-day window
    avg_duration_ms=2000,
    peak_concurrency=100,
    throttles=500,
    days=14,
    active_hours_pct=0.95,  # 95% of hours have traffic
)
lambda_strong = MockLambda(has_provisioned=True)

result_strong = lmi.analyze_function(
    lambda_strong, cw_strong, func_strong, days=14,
    pricing=MOCK_PRICING, workload_type="balanced",
)

check("Not skipped", not result_strong.get("skipped"),
      f"Got skip_reasons: {result_strong.get('skip_reasons')}")
check("Score >= 60 (STRONG)", result_strong["lmi_score"] >= 60,
      f"Score was {result_strong['lmi_score']}")

# Verify individual scoring components
score = result_strong["lmi_score"]
reasons = " | ".join(result_strong["reasons"])
check("High volume detected", "High volume" in reasons, reasons)
check("Long duration detected", "Long duration" in reasons, reasons)
check("High concurrency detected", "High concurrency" in reasons, reasons)
check("Good memory fit detected", "Good memory fit" in reasons, reasons)
check("Provisioned concurrency detected", "provisioned concurrency" in reasons.lower(), reasons)
check("Throttle detected", "Throttled" in reasons, reasons)
check("Runtime ready (java21)", result_strong["runtime_status"] == "ready")

# Verify savings estimate exists and has all tiers
sav = result_strong["savings"]
check("Savings estimate present", sav is not None)
check("Has all 4 tiers", len(sav["tiers"]) == 4, f"Got {len(sav['tiers'])}")
check("Instance type selected", sav["instance_type"] in lmi.INSTANCE_SPECS)
check("Instances >= 3 (AZ min)", sav["instances"] >= 3)
check("Workload type = Balanced", "Balanced" in sav["workload_type"])

# With 50M inv/month at 2s avg, standard Lambda should be expensive
od = sav["tiers"]["on_demand"]
check("Standard Lambda cost > $0", od["standard_lambda"] > 0,
      f"Std Lambda = ${od['standard_lambda']}")

# Verify the score breakdown adds up correctly:
# 30 (high vol) + 25 (long dur) + 20 (high conc) + 10 (memory) + 15 (provisioned) + 5 (throttle) = 105 → capped at 100
check("Score capped at 100", score == 100, f"Score was {score}")

print(f"\n  Score: {score} | Rating: {lmi.classify(score)}")
print(f"  LMI on {sav['instance_type']}: {sav['instances']} instances, "
      f"{sav['envs_per_instance']} envs/inst, {sav['conc_per_env']} conc/env")
print(f"  On-Demand: Std ${od['standard_lambda']:,.2f} → LMI ${od['lmi_cost']:,.2f} "
      f"({od['savings_pct']:+.1f}%)")


# ===========================================================================
# TEST SUITE 2: Borderline weak/moderate candidate (score ~25-40)
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 2: BORDERLINE WEAK/MODERATE CANDIDATE")
print("  Python function, 2M inv/month, 150ms avg duration,")
print("  8 peak concurrency, 256 MB, steady traffic, needs runtime upgrade")
print("=" * 70)

func_border = make_func("data-enricher", runtime="python3.12", memory=256, arch="x86_64")
cw_border = MockCW(
    invocations=2_000_000 * (14 / 30),
    avg_duration_ms=150,
    peak_concurrency=8,
    throttles=0,
    days=14,
    active_hours_pct=0.60,  # 60% — passes regularity threshold (25%)
)
lambda_border = MockLambda(has_provisioned=False)

result_border = lmi.analyze_function(
    lambda_border, cw_border, func_border, days=14,
    pricing=MOCK_PRICING, workload_type="io-heavy",
)

check("Not skipped", not result_border.get("skipped"),
      f"Got skip_reasons: {result_border.get('skip_reasons')}")

score_b = result_border["lmi_score"]
# Expected: 15 (moderate vol) + 12 (moderate dur) + 10 (moderate conc) + 5 (256 MB) - 5 (upgrade) = 37
check("Score in 25-45 range", 25 <= score_b <= 45,
      f"Score was {score_b}")
check("Rating is MODERATE or WEAK",
      lmi.classify(score_b) in ("MODERATE", "WEAK"),
      f"Rating: {lmi.classify(score_b)}")

reasons_b = " | ".join(result_border["reasons"])
check("Moderate volume detected", "Moderate volume" in reasons_b, reasons_b)
check("Moderate duration detected", "Moderate duration" in reasons_b, reasons_b)
check("Moderate concurrency detected", "Moderate concurrency" in reasons_b, reasons_b)
check("Runtime upgrade needed", "upgrade needed" in result_border["runtime_status"])

sav_b = result_border["savings"]
check("Savings estimate present", sav_b is not None)
# At 2M inv/month with 150ms, standard Lambda is cheap — LMI likely more expensive
od_b = sav_b["tiers"]["on_demand"]
check("LMI more expensive at on-demand (low volume)",
      od_b["lmi_cost"] > od_b["standard_lambda"],
      f"Std ${od_b['standard_lambda']:,.2f} vs LMI ${od_b['lmi_cost']:,.2f}")

print(f"\n  Score: {score_b} | Rating: {lmi.classify(score_b)}")
print(f"  LMI on {sav_b['instance_type']}: {sav_b['instances']} instances")
print(f"  On-Demand: Std ${od_b['standard_lambda']:,.2f} → LMI ${od_b['lmi_cost']:,.2f} "
      f"({od_b['savings_pct']:+.1f}%)")


# ===========================================================================
# TEST SUITE 3: Disqualifier — low concurrency
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 3: DISQUALIFIED — LOW CONCURRENCY")
print("  High volume but peak concurrency = 1, no provisioned concurrency")
print("=" * 70)

func_lowconc = make_func("cron-job", runtime="python3.13", memory=512)
cw_lowconc = MockCW(
    invocations=5_000_000 * (14 / 30),
    avg_duration_ms=500,
    peak_concurrency=1,
    days=14,
    active_hours_pct=0.80,
)
lambda_lowconc = MockLambda(has_provisioned=False)

result_lowconc = lmi.analyze_function(
    lambda_lowconc, cw_lowconc, func_lowconc, days=14,
    pricing=MOCK_PRICING,
)

check("Skipped", result_lowconc.get("skipped") is True)
check("Skip reason mentions concurrency",
      any("concurrency" in r.lower() for r in result_lowconc.get("skip_reasons", [])),
      str(result_lowconc.get("skip_reasons")))

print(f"  Skip reasons: {result_lowconc.get('skip_reasons')}")


# ===========================================================================
# TEST SUITE 4: Disqualifier — irregular traffic
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 4: DISQUALIFIED — IRREGULAR TRAFFIC")
print("  Good concurrency but only active 10% of hours (bursty)")
print("=" * 70)

func_bursty = make_func("batch-processor", runtime="python3.13", memory=1024)
cw_bursty = MockCW(
    invocations=10_000_000 * (14 / 30),
    avg_duration_ms=3000,
    peak_concurrency=50,
    days=14,
    active_hours_pct=0.10,  # only 10% — below 25% threshold
)
lambda_bursty = MockLambda(has_provisioned=False)

result_bursty = lmi.analyze_function(
    lambda_bursty, cw_bursty, func_bursty, days=14,
    pricing=MOCK_PRICING,
)

check("Skipped", result_bursty.get("skipped") is True)
check("Skip reason mentions irregular traffic",
      any("irregular" in r.lower() for r in result_bursty.get("skip_reasons", [])),
      str(result_bursty.get("skip_reasons")))

print(f"  Skip reasons: {result_bursty.get('skip_reasons')}")


# ===========================================================================
# TEST SUITE 5: Unsupported runtime (should return None)
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 5: UNSUPPORTED RUNTIME")
print("  Ruby, Go, or custom runtime — not LMI compatible")
print("=" * 70)

for rt in ["ruby3.3", "provided.al2023", "go1.x"]:
    func_unsup = make_func(f"func-{rt}", runtime=rt)
    result_unsup = lmi.analyze_function(
        MockLambda(), MockCW(invocations=10_000_000, avg_duration_ms=1000,
                             peak_concurrency=50, days=14, active_hours_pct=0.9),
        func_unsup, days=14, pricing=MOCK_PRICING,
    )
    check(f"Runtime {rt} returns None", result_unsup is None,
          f"Got: {result_unsup}")


# ===========================================================================
# TEST SUITE 6: Capacity formula validation
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 6: CAPACITY FORMULA VALIDATION")
print("  Cross-check against known examples from lmi_calculator.py")
print("=" * 70)

# Example from capacity formula doc: Java, 1000 concurrency, 60 MB/exec, 2:1 ratio
plan = lmi.lmi_capacity_plan("java", 60, 1000, "c7g.xlarge", "io-heavy")
# conc_per_vcpu: min(32, 2048/60=34, 8) = 8
# function_memory: max(2048, 60*8=480) = 2048
# vcpus_per_env: 2048 // 2048 = 1
# conc_per_env: 8 * 1 = 8
# envs_needed: ceil(1000/8) = 125
# packing: usable_vcpus=3, by_vcpu=3//1=3, by_memory=7168//2048=3, min=3
# instances: max(3, ceil(125/3)) = 42
check("Java 1000-conc: conc_per_env=8", plan["conc_per_env"] == 8,
      f"Got {plan['conc_per_env']}")
check("Java 1000-conc: func_memory=2048", plan["function_memory_mb"] == 2048,
      f"Got {plan['function_memory_mb']}")
check("Java 1000-conc: envs_needed=125", plan["envs_needed"] == 125,
      f"Got {plan['envs_needed']}")
check("Java 1000-conc: envs_per_instance=3", plan["envs_per_instance"] == 3,
      f"Got {plan['envs_per_instance']}")
check("Java 1000-conc: instances=42", plan["instances"] == 42,
      f"Got {plan['instances']}")

# CPU-heavy workload: same setup but cpu-heavy → sustainable_limit=2
plan_cpu = lmi.lmi_capacity_plan("java", 60, 1000, "c7g.xlarge", "cpu-heavy")
# conc_per_vcpu: min(32, 34, 2) = 2
check("CPU-heavy: conc_per_vcpu=2 (limited by workload type)",
      plan_cpu["conc_per_env"] == 2,  # 2 * 1 vcpu/env = 2
      f"Got {plan_cpu['conc_per_env']}")
check("CPU-heavy needs more instances than io-heavy",
      plan_cpu["instances"] > plan["instances"],
      f"CPU: {plan_cpu['instances']} vs IO: {plan['instances']}")

# Minimum 3 instances for AZ resiliency
plan_tiny = lmi.lmi_capacity_plan("python", 100, 1, "c7g.xlarge", "io-heavy")
check("Tiny workload: min 3 instances", plan_tiny["instances"] == 3)


# ===========================================================================
# TEST SUITE 7: Workload type affects scoring path
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 7: WORKLOAD TYPE COMPARISON")
print("  Same function analyzed as io-heavy vs cpu-heavy")
print("=" * 70)

func_wt = make_func("ml-inference", runtime="python3.13", memory=2048, arch="arm64")
cw_wt = MockCW(
    invocations=20_000_000 * (14 / 30),
    avg_duration_ms=1500,
    peak_concurrency=80,
    days=14,
    active_hours_pct=0.90,
)

result_io = lmi.analyze_function(MockLambda(), cw_wt, func_wt, days=14,
                                  pricing=MOCK_PRICING, workload_type="io-heavy")
result_cpu = lmi.analyze_function(MockLambda(), cw_wt, func_wt, days=14,
                                   pricing=MOCK_PRICING, workload_type="cpu-heavy")

check("Both produce results", result_io is not None and result_cpu is not None)
check("Same score (workload type doesn't affect scoring)",
      result_io["lmi_score"] == result_cpu["lmi_score"])
check("CPU-heavy uses different capacity plan than IO-heavy",
      result_cpu["savings"]["conc_per_env"] <= result_io["savings"]["conc_per_env"],
      f"IO conc/env: {result_io['savings']['conc_per_env']} vs CPU: {result_cpu['savings']['conc_per_env']}")
check("CPU-heavy LMI cost >= IO-heavy LMI cost",
      result_cpu["savings"]["tiers"]["on_demand"]["lmi_cost"] >=
      result_io["savings"]["tiers"]["on_demand"]["lmi_cost"])

print(f"  IO-heavy: {result_io['savings']['instances']} instances, "
      f"LMI ${result_io['savings']['tiers']['on_demand']['lmi_cost']:,.2f}")
print(f"  CPU-heavy: {result_cpu['savings']['instances']} instances, "
      f"LMI ${result_cpu['savings']['tiers']['on_demand']['lmi_cost']:,.2f}")


# ===========================================================================
# TEST SUITE 8: Memory override
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 8: MEMORY OVERRIDE")
print("  Function configured at 2048 MB, actual usage 200 MB")
print("=" * 70)

func_mem = make_func("over-provisioned", runtime="nodejs22.x", memory=2048, arch="arm64")
cw_mem = MockCW(
    invocations=15_000_000 * (14 / 30),
    avg_duration_ms=800,
    peak_concurrency=60,
    days=14,
    active_hours_pct=0.85,
)

result_default = lmi.analyze_function(MockLambda(), cw_mem, func_mem, days=14,
                                       pricing=MOCK_PRICING)
result_override = lmi.analyze_function(MockLambda(), cw_mem, func_mem, days=14,
                                        pricing=MOCK_PRICING, memory_override=200)

check("Default uses configured memory", result_default["memory_per_exec_mb"] == 2048)
check("Override uses provided memory", result_override["memory_per_exec_mb"] == 200)
check("Override produces fewer or equal LMI instances (better packing)",
      result_override["savings"]["instances"] <= result_default["savings"]["instances"],
      f"Default: {result_default['savings']['instances']} vs Override: {result_override['savings']['instances']}")

print(f"  Default (2048 MB): {result_default['savings']['instances']} instances")
print(f"  Override (200 MB): {result_override['savings']['instances']} instances")


# ===========================================================================
# TEST SUITE 9: Provisioned concurrency bypasses concurrency disqualifier
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 9: PROVISIONED CONCURRENCY BYPASS")
print("  Peak concurrency = 1 but has provisioned concurrency → not skipped")
print("=" * 70)

func_pc = make_func("warm-api", runtime="python3.13", memory=512)
cw_pc = MockCW(
    invocations=5_000_000 * (14 / 30),
    avg_duration_ms=500,
    peak_concurrency=1,
    days=14,
    active_hours_pct=0.80,
)
lambda_pc = MockLambda(has_provisioned=True)

result_pc = lmi.analyze_function(lambda_pc, cw_pc, func_pc, days=14, pricing=MOCK_PRICING)

check("Not skipped (provisioned concurrency bypasses low-conc check)",
      not result_pc.get("skipped"),
      f"Got: skipped={result_pc.get('skipped')}, reasons={result_pc.get('skip_reasons')}")
check("Provisioned concurrency in reasons",
      any("provisioned" in r.lower() for r in result_pc.get("reasons", [])))


# ===========================================================================
# RESULTS
# ===========================================================================
print(f"\n{'=' * 70}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'=' * 70}\n")

sys.exit(0 if failed == 0 else 1)
