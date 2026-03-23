#!/usr/bin/env python3
"""
LMI Candidate Finder — Scan Lambda functions and identify candidates for
Lambda Managed Instances based on invocation patterns, duration, memory,
and runtime compatibility.

Usage:
    python lmi_candidate_finder.py --region us-east-1
    python lmi_candidate_finder.py --region us-east-1 --profile my-profile --days 7
    python lmi_candidate_finder.py --region us-east-1 --min-invocations 100000

Requires: boto3 (pip install boto3)
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    print("Error: boto3 is required. Install with: pip install boto3")
    sys.exit(1)

# ---------------------------------------------------------------------------
# LMI-supported runtimes (minimum versions)
# ---------------------------------------------------------------------------
LMI_RUNTIME_MAP = {
    "python3.13": "python", "python3.14": "python",
    "nodejs22.x": "nodejs",
    "java21": "java", "java23": "java",
    "dotnet8": "dotnet", "dotnet9": "dotnet",
}

# Runtimes that COULD be upgraded to an LMI-supported version
UPGRADEABLE_RUNTIMES = {
    "python3.9": "python3.13", "python3.10": "python3.13",
    "python3.11": "python3.13", "python3.12": "python3.13",
    "nodejs18.x": "nodejs22.x", "nodejs20.x": "nodejs22.x",
    "java11": "java21", "java17": "java21",
    "dotnet6": "dotnet8",
}

# LMI pricing constants (us-east-1, arm64)
LAMBDA_GBSEC_PRICE = 0.0000133333
LAMBDA_REQUEST_PRICE_PER_M = 0.20
LMI_MANAGEMENT_FEE = 0.15
HOURS_PER_MONTH = 730

# Default LMI instance for estimation
DEFAULT_INSTANCE = {"type": "c7g.xlarge", "vcpus": 4, "memory_gb": 8, "price_per_hour": 0.1450}

# Scoring thresholds
THRESHOLDS = {
    "min_monthly_invocations": 1_000_000,   # 1M/month minimum to consider
    "ideal_monthly_invocations": 10_000_000, # 10M/month = strong candidate
    "min_avg_duration_ms": 100,              # >100ms avg duration
    "ideal_avg_duration_ms": 1000,           # >1s = strong candidate
    "min_concurrency": 5,                    # peak concurrent > 5
    "ideal_concurrency": 50,                 # peak concurrent > 50
    "min_memory_mb": 256,                    # at least 256 MB configured
}


def get_lambda_functions(lambda_client):
    """List all Lambda functions with full configuration."""
    functions = []
    paginator = lambda_client.get_paginator("list_functions")
    for page in paginator.paginate():
        functions.extend(page["Functions"])
    return functions


def get_metric_stats(cw_client, function_name, metric_name, stat, days, period=86400):
    """Get CloudWatch metric statistics for a Lambda function."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        resp = cw_client.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName=metric_name,
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start, EndTime=end,
            Period=period, Statistics=[stat],
        )
        return resp.get("Datapoints", [])
    except ClientError:
        return []


def analyze_function(lambda_client, cw_client, func, days):
    """Analyze a single Lambda function for LMI candidacy."""
    name = func["FunctionName"]
    runtime = func.get("Runtime", "")
    memory_mb = func.get("MemorySize", 128)
    arch = func.get("Architectures", ["x86_64"])[0]
    timeout = func.get("Timeout", 3)

    # Runtime check
    lmi_ready = runtime in LMI_RUNTIME_MAP
    upgradeable = runtime in UPGRADEABLE_RUNTIMES
    if not lmi_ready and not upgradeable:
        return None  # unsupported runtime family entirely

    # Get invocation count (Sum over the period)
    inv_points = get_metric_stats(cw_client, name, "Invocations", "Sum", days)
    total_invocations = sum(dp["Sum"] for dp in inv_points)
    monthly_invocations = total_invocations * (30 / days) if days != 30 else total_invocations

    if monthly_invocations < THRESHOLDS["min_monthly_invocations"] * 0.1:
        return None  # too low volume to even consider

    # Get average duration
    dur_points = get_metric_stats(cw_client, name, "Duration", "Average", days)
    avg_duration_ms = (sum(dp["Average"] for dp in dur_points) / len(dur_points)) if dur_points else 0

    # Get peak concurrent executions
    conc_points = get_metric_stats(cw_client, name, "ConcurrentExecutions", "Maximum", days)
    peak_concurrency = max((dp["Maximum"] for dp in conc_points), default=0)

    # Get throttle count
    throttle_points = get_metric_stats(cw_client, name, "Throttles", "Sum", days)
    total_throttles = sum(dp["Sum"] for dp in throttle_points)

    # Check for provisioned concurrency (already paying for warm capacity)
    has_provisioned = False
    try:
        pc_resp = lambda_client.list_provisioned_concurrency_configs(FunctionName=name)
        has_provisioned = len(pc_resp.get("ProvisionedConcurrencyConfigs", [])) > 0
    except ClientError:
        pass

    # --- Scoring ---
    score = 0
    reasons = []

    # Volume score (0-30)
    if monthly_invocations >= THRESHOLDS["ideal_monthly_invocations"]:
        score += 30
        reasons.append(f"High volume: {monthly_invocations:,.0f} inv/month")
    elif monthly_invocations >= THRESHOLDS["min_monthly_invocations"]:
        score += 15
        reasons.append(f"Moderate volume: {monthly_invocations:,.0f} inv/month")
    else:
        reasons.append(f"Low volume: {monthly_invocations:,.0f} inv/month")

    # Duration score (0-25)
    if avg_duration_ms >= THRESHOLDS["ideal_avg_duration_ms"]:
        score += 25
        reasons.append(f"Long duration: {avg_duration_ms:,.0f}ms avg")
    elif avg_duration_ms >= THRESHOLDS["min_avg_duration_ms"]:
        score += 12
        reasons.append(f"Moderate duration: {avg_duration_ms:,.0f}ms avg")
    else:
        reasons.append(f"Short duration: {avg_duration_ms:,.0f}ms avg (LMI less beneficial)")

    # Concurrency score (0-20)
    if peak_concurrency >= THRESHOLDS["ideal_concurrency"]:
        score += 20
        reasons.append(f"High concurrency: {peak_concurrency:.0f} peak")
    elif peak_concurrency >= THRESHOLDS["min_concurrency"]:
        score += 10
        reasons.append(f"Moderate concurrency: {peak_concurrency:.0f} peak")

    # Memory score (0-10)
    if memory_mb >= 512:
        score += 10
        reasons.append(f"Good memory fit: {memory_mb} MB")
    elif memory_mb >= THRESHOLDS["min_memory_mb"]:
        score += 5

    # Provisioned concurrency bonus (0-15) — already paying for warm capacity
    if has_provisioned:
        score += 15
        reasons.append("Has provisioned concurrency (LMI replaces this)")

    # Runtime readiness
    if lmi_ready:
        runtime_status = "ready"
    else:
        runtime_status = f"upgrade needed ({runtime} → {UPGRADEABLE_RUNTIMES[runtime]})"
        score -= 5  # small penalty for needing upgrade

    # Throttle indicator
    if total_throttles > 0:
        reasons.append(f"Throttled {total_throttles:,.0f} times (LMI may help)")
        score += 5

    # --- Cost estimate ---
    lmi_runtime = LMI_RUNTIME_MAP.get(runtime, LMI_RUNTIME_MAP.get(UPGRADEABLE_RUNTIMES.get(runtime, ""), "python"))
    cost_estimate = estimate_costs(
        monthly_invocations=monthly_invocations,
        avg_duration_sec=avg_duration_ms / 1000,
        memory_mb=max(memory_mb, 2048),  # LMI minimum 2 GB
        peak_concurrency=max(peak_concurrency, 1),
        arch=arch,
    )

    return {
        "function_name": name,
        "runtime": runtime,
        "runtime_status": runtime_status,
        "memory_mb": memory_mb,
        "architecture": arch,
        "timeout_sec": timeout,
        "monthly_invocations": monthly_invocations,
        "avg_duration_ms": avg_duration_ms,
        "peak_concurrency": peak_concurrency,
        "has_provisioned_concurrency": has_provisioned,
        "throttle_count": total_throttles,
        "lmi_score": min(score, 100),
        "reasons": reasons,
        "cost_estimate": cost_estimate,
    }


def estimate_costs(monthly_invocations, avg_duration_sec, memory_mb, peak_concurrency, arch):
    """Rough cost comparison: Standard Lambda vs LMI on c7g.xlarge."""
    # Standard Lambda cost
    memory_gb = memory_mb / 1024
    gb_sec_price = LAMBDA_GBSEC_PRICE if arch == "arm64" else 0.0000166667
    std_compute = monthly_invocations * avg_duration_sec * memory_gb * gb_sec_price
    std_requests = (monthly_invocations / 1_000_000) * LAMBDA_REQUEST_PRICE_PER_M
    std_total = std_compute + std_requests

    # LMI cost estimate (simplified: pack onto c7g.xlarge)
    inst = DEFAULT_INSTANCE
    # Assume 3 envs per instance (conservative for c7g.xlarge with 2GB functions)
    envs_per_instance = max(1, (inst["vcpus"] - 1) // 1)
    instances_needed = max(3, math.ceil(peak_concurrency / (envs_per_instance * 4)))  # 4 conc/env
    ec2_monthly = instances_needed * inst["price_per_hour"] * HOURS_PER_MONTH
    mgmt_fee = ec2_monthly * LMI_MANAGEMENT_FEE
    lmi_requests = (monthly_invocations / 1_000_000) * LAMBDA_REQUEST_PRICE_PER_M
    lmi_total = ec2_monthly + mgmt_fee + lmi_requests

    savings_pct = ((std_total - lmi_total) / std_total * 100) if std_total > 0 else 0

    return {
        "standard_lambda_monthly": round(std_total, 2),
        "lmi_monthly_estimate": round(lmi_total, 2),
        "estimated_savings_pct": round(savings_pct, 1),
        "lmi_instances_estimate": instances_needed,
    }


def classify(score):
    if score >= 60:
        return "STRONG"
    elif score >= 35:
        return "MODERATE"
    elif score >= 15:
        return "WEAK"
    return "NOT RECOMMENDED"


def main():
    parser = argparse.ArgumentParser(description="Find Lambda functions that are good candidates for LMI")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--profile", default=None, help="AWS CLI profile name")
    parser.add_argument("--days", type=int, default=14, help="Days of CloudWatch data to analyze (default: 14)")
    parser.add_argument("--min-invocations", type=int, default=None,
                        help="Override minimum monthly invocation threshold")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.min_invocations is not None:
        THRESHOLDS["min_monthly_invocations"] = args.min_invocations

    session_kwargs = {"region_name": args.region}
    if args.profile:
        session_kwargs["profile_name"] = args.profile
    session = boto3.Session(**session_kwargs)
    lambda_client = session.client("lambda")
    cw_client = session.client("cloudwatch")

    print(f"\n🔍 Scanning Lambda functions in {args.region} (last {args.days} days)...\n")

    functions = get_lambda_functions(lambda_client)
    print(f"Found {len(functions)} Lambda functions. Analyzing metrics...\n")

    candidates = []
    for func in functions:
        result = analyze_function(lambda_client, cw_client, func, args.days)
        if result:
            candidates.append(result)

    candidates.sort(key=lambda x: x["lmi_score"], reverse=True)

    if args.json:
        print(json.dumps(candidates, indent=2, default=str))
        return

    # Pretty print results
    if not candidates:
        print("No LMI candidates found. Functions may have unsupported runtimes or insufficient traffic.")
        return

    strong = [c for c in candidates if c["lmi_score"] >= 60]
    moderate = [c for c in candidates if 35 <= c["lmi_score"] < 60]
    weak = [c for c in candidates if c["lmi_score"] < 35]

    print(f"{'=' * 80}")
    print(f"  LMI CANDIDATE REPORT — {args.region}")
    print(f"  Analysis period: last {args.days} days | Functions scanned: {len(functions)}")
    print(f"  Candidates found: {len(strong)} strong, {len(moderate)} moderate, {len(weak)} weak")
    print(f"{'=' * 80}\n")

    for candidate in candidates:
        rating = classify(candidate["lmi_score"])
        icon = {"STRONG": "🟢", "MODERATE": "🟡", "WEAK": "🟠", "NOT RECOMMENDED": "🔴"}[rating]
        cost = candidate["cost_estimate"]

        print(f"{icon} {candidate['function_name']}  [{rating} — score {candidate['lmi_score']}]")
        print(f"   Runtime: {candidate['runtime']} ({candidate['runtime_status']})")
        print(f"   Memory: {candidate['memory_mb']} MB | Arch: {candidate['architecture']} | Timeout: {candidate['timeout_sec']}s")
        print(f"   Invocations: {candidate['monthly_invocations']:,.0f}/month | Avg duration: {candidate['avg_duration_ms']:,.0f}ms | Peak concurrency: {candidate['peak_concurrency']:.0f}")
        if candidate["has_provisioned_concurrency"]:
            print(f"   ⚡ Has provisioned concurrency (LMI can replace this)")
        if candidate["throttle_count"] > 0:
            print(f"   ⚠️  Throttled {candidate['throttle_count']:,.0f} times")
        print(f"   💰 Standard Lambda: ${cost['standard_lambda_monthly']:,.2f}/mo → LMI estimate: ${cost['lmi_monthly_estimate']:,.2f}/mo ({cost['estimated_savings_pct']:+.1f}%)")
        print(f"      LMI instances needed: ~{cost['lmi_instances_estimate']} (c7g.xlarge)")
        for reason in candidate["reasons"]:
            print(f"   • {reason}")
        print()

    # Summary
    total_std = sum(c["cost_estimate"]["standard_lambda_monthly"] for c in strong)
    total_lmi = sum(c["cost_estimate"]["lmi_monthly_estimate"] for c in strong)
    if strong and total_std > 0:
        print(f"{'─' * 80}")
        print(f"  💡 Strong candidates combined: ${total_std:,.2f}/mo → ${total_lmi:,.2f}/mo")
        print(f"     Potential savings: ${total_std - total_lmi:,.2f}/mo ({(total_std - total_lmi) / total_std * 100:.0f}%)")
        print(f"     Use the LMI Cost Calculator for detailed capacity planning.")
        print(f"{'─' * 80}\n")


if __name__ == "__main__":
    main()
