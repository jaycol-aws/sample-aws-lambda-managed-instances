#!/usr/bin/env python3
"""
Tests for LMI Candidate Finder — validates scoring, disqualifiers, savings
estimates, free tier, and capacity formula using mock data.
No AWS credentials required.

Usage:
    python test_lmi_candidate_finder.py
"""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import lmi_candidate_finder as lmi

# ---------------------------------------------------------------------------
# Mock pricing (mirrors live API structure with per-instance OD/SP/RI rates)
# ---------------------------------------------------------------------------
MOCK_PRICING = {
    "ec2": {
        "c7g.xlarge":  {"od": 0.1450, "compute_sp_1yr": 0.1040, "ec2_sp_1yr": 0.0955, "ri_3yr": 0.0554},
        "c7g.2xlarge": {"od": 0.2900, "compute_sp_1yr": 0.2080, "ec2_sp_1yr": 0.1910, "ri_3yr": 0.1108},
        "m7g.xlarge":  {"od": 0.1632, "compute_sp_1yr": 0.1170, "ec2_sp_1yr": 0.1075, "ri_3yr": 0.0623},
        "m7g.2xlarge": {"od": 0.3264, "compute_sp_1yr": 0.2340, "ec2_sp_1yr": 0.2150, "ri_3yr": 0.1246},
        "r7g.xlarge":  {"od": 0.2134, "compute_sp_1yr": 0.1530, "ec2_sp_1yr": 0.1406, "ri_3yr": 0.0815},
        "c7i.xlarge":  {"od": 0.1785, "compute_sp_1yr": 0.1280, "ec2_sp_1yr": 0.1176, "ri_3yr": 0.0682},
        "c7i.2xlarge": {"od": 0.3570, "compute_sp_1yr": 0.2560, "ec2_sp_1yr": 0.2352, "ri_3yr": 0.1364},
        "m7i.xlarge":  {"od": 0.2016, "compute_sp_1yr": 0.1445, "ec2_sp_1yr": 0.1328, "ri_3yr": 0.0770},
        "m7i.2xlarge": {"od": 0.4032, "compute_sp_1yr": 0.2890, "ec2_sp_1yr": 0.2656, "ri_3yr": 0.1540},
    },
    "lambda": {
        "gbsec_x86": 0.0000166667,
        "gbsec_arm": 0.0000133333,
        "request": 0.0000002,
        "sp_gbsec": 0.0000147,  # Compute SP rate for Lambda
    },
    "region": "us-east-1",
    "location": "US East (N. Virginia)",
}


def make_hourly_points(active_hours, total_hours, total_sum):
    now = datetime.now(timezone.utc)
    per_hour = total_sum / active_hours if active_hours > 0 else 0
    return [{"Timestamp": now - timedelta(hours=i), "Sum": per_hour}
            for i in range(active_hours)]


class MockCW:
    def __init__(self, invocations=0, avg_duration_ms=0, peak_concurrency=0,
                 throttles=0, days=14, active_hours_pct=1.0):
        self.days = days
        total_hours = days * 24
        active_hours = int(total_hours * active_hours_pct)
        self._data = {
            ("Invocations", "Sum", 3600): make_hourly_points(active_hours, total_hours, invocations),
            ("Duration", "Average", 86400): [{"Timestamp": datetime.now(timezone.utc), "Average": avg_duration_ms}] * days,
            ("ConcurrentExecutions", "Maximum", 86400): [{"Timestamp": datetime.now(timezone.utc), "Maximum": peak_concurrency}] * days,
            ("Throttles", "Sum", 86400): [{"Timestamp": datetime.now(timezone.utc), "Sum": throttles}],
        }

    def get_metric_statistics(self, **kwargs):
        metric = kwargs["MetricName"]
        stat = kwargs["Statistics"][0]
        period = kwargs["Period"]
        return {"Datapoints": self._data.get((metric, stat, period), [])}


class MockLambda:
    def __init__(self, has_provisioned=False):
        self.has_provisioned = has_provisioned

    def list_provisioned_concurrency_configs(self, **kwargs):
        if self.has_provisioned:
            return {"ProvisionedConcurrencyConfigs": [{"FunctionArn": "arn:fake"}]}
        return {"ProvisionedConcurrencyConfigs": []}


def make_func(name, runtime="python3.13", memory=512, arch="arm64", timeout=30):
    return {"FunctionName": name, "Runtime": runtime, "MemorySize": memory,
            "Architectures": [arch], "Timeout": timeout}


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
# TEST 1: Strong LMI candidate (score 100)
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 1: STRONG LMI CANDIDATE")
print("=" * 70)

result = lmi.analyze_function(
    MockLambda(has_provisioned=True),
    MockCW(invocations=50_000_000*(14/30), avg_duration_ms=2000,
           peak_concurrency=100, throttles=500, days=14, active_hours_pct=0.95),
    make_func("payment-api", runtime="java21", memory=1024, arch="arm64"),
    days=14, pricing=MOCK_PRICING, workload_type="balanced",
)

check("Not skipped", not result.get("skipped"))
check("Score = 100 (capped)", result["lmi_score"] == 100)
check("Has all 4 tiers", len(result["savings"]["tiers"]) == 4)
check("Tier labels include discount %", "%" in result["savings"]["tiers"]["compute_sp"]["label"])
check("RI tier present", "reserved_3yr" in result["savings"]["tiers"])
check("Graviton instance selected (arm64)", "g." in result["savings"]["instance_type"])

od = result["savings"]["tiers"]["on_demand"]
ri = result["savings"]["tiers"]["reserved_3yr"]
check("RI cheaper than OD for LMI", ri["lmi_cost"] < od["lmi_cost"],
      f"RI ${ri['lmi_cost']} vs OD ${od['lmi_cost']}")
check("Standard Lambda cost > $0", od["standard_lambda"] > 0)

print(f"\n  OD: Std ${od['standard_lambda']:,.2f} → LMI ${od['lmi_cost']:,.2f}")
print(f"  RI: Std ${ri['standard_lambda']:,.2f} → LMI ${ri['lmi_cost']:,.2f}")


# ===========================================================================
# TEST 2: Borderline weak/moderate (score ~37)
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 2: BORDERLINE WEAK/MODERATE CANDIDATE")
print("=" * 70)

result_b = lmi.analyze_function(
    MockLambda(),
    MockCW(invocations=2_000_000*(14/30), avg_duration_ms=150,
           peak_concurrency=8, days=14, active_hours_pct=0.60),
    make_func("data-enricher", runtime="python3.12", memory=256, arch="x86_64"),
    days=14, pricing=MOCK_PRICING, workload_type="io-heavy",
)

check("Not skipped", not result_b.get("skipped"))
score_b = result_b["lmi_score"]
check("Score in 25-45 range", 25 <= score_b <= 45, f"Score was {score_b}")
check("x86 instance selected", "i." in result_b["savings"]["instance_type"],
      f"Got {result_b['savings']['instance_type']}")
check("LMI more expensive at OD",
      result_b["savings"]["tiers"]["on_demand"]["lmi_cost"] > result_b["savings"]["tiers"]["on_demand"]["standard_lambda"])

print(f"  Score: {score_b} | Instance: {result_b['savings']['instance_type']}")


# ===========================================================================
# TEST 3: Disqualifier — low concurrency
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 3: DISQUALIFIED — LOW CONCURRENCY")
print("=" * 70)

result_lc = lmi.analyze_function(
    MockLambda(),
    MockCW(invocations=5_000_000*(14/30), avg_duration_ms=500,
           peak_concurrency=1, days=14, active_hours_pct=0.80),
    make_func("cron-job"), days=14, pricing=MOCK_PRICING,
)
check("Skipped", result_lc.get("skipped") is True)
check("Reason: concurrency", any("concurrency" in r.lower() for r in result_lc.get("skip_reasons", [])))


# ===========================================================================
# TEST 4: Disqualifier — irregular traffic
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 4: DISQUALIFIED — IRREGULAR TRAFFIC")
print("=" * 70)

result_ir = lmi.analyze_function(
    MockLambda(),
    MockCW(invocations=10_000_000*(14/30), avg_duration_ms=3000,
           peak_concurrency=50, days=14, active_hours_pct=0.10),
    make_func("batch-processor", memory=1024), days=14, pricing=MOCK_PRICING,
)
check("Skipped", result_ir.get("skipped") is True)
check("Reason: irregular", any("irregular" in r.lower() for r in result_ir.get("skip_reasons", [])))


# ===========================================================================
# TEST 5: Unsupported runtimes
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 5: UNSUPPORTED RUNTIMES")
print("=" * 70)

for rt in ["ruby3.3", "provided.al2023", "go1.x"]:
    r = lmi.analyze_function(
        MockLambda(), MockCW(invocations=10_000_000, avg_duration_ms=1000,
                             peak_concurrency=50, days=14, active_hours_pct=0.9),
        make_func(f"func-{rt}", runtime=rt), days=14, pricing=MOCK_PRICING)
    check(f"Runtime {rt} returns None", r is None)


# ===========================================================================
# TEST 6: Capacity formula validation
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 6: CAPACITY FORMULA VALIDATION")
print("=" * 70)

plan = lmi.lmi_capacity_plan("java", 60, 1000, "c7g.xlarge", "io-heavy")
check("conc_per_env=8", plan["conc_per_env"] == 8)
check("func_memory=2048", plan["function_memory_mb"] == 2048)
check("envs_needed=125", plan["envs_needed"] == 125)
check("envs_per_instance=3", plan["envs_per_instance"] == 3)
check("instances=42", plan["instances"] == 42)

plan_cpu = lmi.lmi_capacity_plan("java", 60, 1000, "c7g.xlarge", "cpu-heavy")
check("CPU-heavy: conc_per_env=2", plan_cpu["conc_per_env"] == 2)
check("CPU-heavy: more instances", plan_cpu["instances"] > plan["instances"])

plan_tiny = lmi.lmi_capacity_plan("python", 100, 1, "c7g.xlarge", "io-heavy")
check("Tiny workload: min 3 instances", plan_tiny["instances"] == 3)


# ===========================================================================
# TEST 7: Workload type comparison
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 7: WORKLOAD TYPE COMPARISON")
print("=" * 70)

cw_wt = MockCW(invocations=20_000_000*(14/30), avg_duration_ms=1500,
               peak_concurrency=80, days=14, active_hours_pct=0.90)
func_wt = make_func("ml-inference", memory=2048, arch="arm64")

r_io = lmi.analyze_function(MockLambda(), cw_wt, func_wt, days=14, pricing=MOCK_PRICING, workload_type="io-heavy")
r_cpu = lmi.analyze_function(MockLambda(), cw_wt, func_wt, days=14, pricing=MOCK_PRICING, workload_type="cpu-heavy")

check("Same score", r_io["lmi_score"] == r_cpu["lmi_score"])
check("CPU-heavy LMI cost >= IO-heavy",
      r_cpu["savings"]["tiers"]["on_demand"]["lmi_cost"] >= r_io["savings"]["tiers"]["on_demand"]["lmi_cost"])


# ===========================================================================
# TEST 8: Memory override
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 8: MEMORY OVERRIDE")
print("=" * 70)

cw_mem = MockCW(invocations=15_000_000*(14/30), avg_duration_ms=800,
                peak_concurrency=60, days=14, active_hours_pct=0.85)
func_mem = make_func("over-provisioned", runtime="nodejs22.x", memory=2048, arch="arm64")

r_def = lmi.analyze_function(MockLambda(), cw_mem, func_mem, days=14, pricing=MOCK_PRICING)
r_ovr = lmi.analyze_function(MockLambda(), cw_mem, func_mem, days=14, pricing=MOCK_PRICING, memory_override=200)

check("Default uses configured memory", r_def["memory_per_exec_mb"] == 2048)
check("Override uses provided memory", r_ovr["memory_per_exec_mb"] == 200)
check("Override: fewer or equal instances",
      r_ovr["savings"]["instances"] <= r_def["savings"]["instances"])


# ===========================================================================
# TEST 9: Provisioned concurrency bypass
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 9: PROVISIONED CONCURRENCY BYPASS")
print("=" * 70)

r_pc = lmi.analyze_function(
    MockLambda(has_provisioned=True),
    MockCW(invocations=5_000_000*(14/30), avg_duration_ms=500,
           peak_concurrency=1, days=14, active_hours_pct=0.80),
    make_func("warm-api"), days=14, pricing=MOCK_PRICING)

check("Not skipped", not r_pc.get("skipped"))
check("Provisioned in reasons", any("provisioned" in r.lower() for r in r_pc.get("reasons", [])))


# ===========================================================================
# TEST 10: Free tier subtraction
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 10: FREE TIER SUBTRACTION")
print("=" * 70)

# Function with exactly free-tier-level traffic: 1M requests, short duration
# Standard Lambda should be ~$0 after free tier
r_free = lmi.analyze_function(
    MockLambda(),
    MockCW(invocations=1_000_000*(14/30), avg_duration_ms=200,
           peak_concurrency=10, days=14, active_hours_pct=0.50),
    make_func("low-cost-api", memory=512, arch="arm64"),
    days=14, pricing=MOCK_PRICING,
)

check("Not skipped", not r_free.get("skipped"))
od_free = r_free["savings"]["tiers"]["on_demand"]
# At 1M inv/month, 200ms, 512MB: GB-sec = 1M * 0.2 * 0.5 = 100K
# Free tier = 400K GB-sec → billable = 0. Requests: 1M - 1M free = 0
check("Standard Lambda ≈ $0 (within free tier)", od_free["standard_lambda"] < 1.0,
      f"Got ${od_free['standard_lambda']}")

print(f"  Std Lambda: ${od_free['standard_lambda']:.2f} (should be ~$0 with free tier)")


# ===========================================================================
# TEST 11: days=30 path (no extrapolation)
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 11: DAYS=30 PATH (NO EXTRAPOLATION)")
print("=" * 70)

inv_30 = 5_000_000
r_30 = lmi.analyze_function(
    MockLambda(),
    MockCW(invocations=inv_30, avg_duration_ms=500,
           peak_concurrency=20, days=30, active_hours_pct=0.80),
    make_func("steady-api"), days=30, pricing=MOCK_PRICING,
)

check("Not skipped", not r_30.get("skipped"))
check("Monthly invocations = raw total (no extrapolation)",
      r_30["monthly_invocations"] == inv_30,
      f"Got {r_30['monthly_invocations']}")


# ===========================================================================
# TEST 12: Missing pricing (estimate_savings returns None)
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 12: MISSING PRICING GRACEFUL HANDLING")
print("=" * 70)

empty_pricing = {"ec2": {}, "lambda": MOCK_PRICING["lambda"],
                 "region": "us-east-1", "location": "US East (N. Virginia)"}
r_no_price = lmi.analyze_function(
    MockLambda(),
    MockCW(invocations=10_000_000*(14/30), avg_duration_ms=1000,
           peak_concurrency=50, days=14, active_hours_pct=0.90),
    make_func("no-price-func"), days=14, pricing=empty_pricing,
)

check("Not skipped", not r_no_price.get("skipped"))
check("Savings is None (no EC2 prices)", r_no_price["savings"] is None)


# ===========================================================================
# TEST 13: Score floor at 0 (no negative scores)
# ===========================================================================
print("\n" + "=" * 70)
print("  TEST 13: SCORE FLOOR AT 0")
print("=" * 70)

# Upgradeable runtime (-5) with minimal positive signals
r_neg = lmi.analyze_function(
    MockLambda(),
    MockCW(invocations=500_000*(14/30), avg_duration_ms=50,
           peak_concurrency=3, days=14, active_hours_pct=0.50),
    make_func("tiny-func", runtime="python3.12", memory=128, arch="x86_64"),
    days=14, pricing=MOCK_PRICING,
)

check("Not skipped", not r_neg.get("skipped"))
check("Score >= 0 (floored)", r_neg["lmi_score"] >= 0, f"Score was {r_neg['lmi_score']}")
check("Classify returns NOT RECOMMENDED", lmi.classify(r_neg["lmi_score"]) == "NOT RECOMMENDED")


# ===========================================================================
# RESULTS
# ===========================================================================
print(f"\n{'=' * 70}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'=' * 70}\n")

sys.exit(0 if failed == 0 else 1)
