#!/usr/bin/env python3
"""
LMI Candidate Finder — Scan Lambda functions and identify candidates for
Lambda Managed Instances based on invocation patterns, duration, memory,
and runtime compatibility. Includes savings estimates using the LMI
capacity formula.

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

UPGRADEABLE_RUNTIMES = {
    "python3.9": "python3.13", "python3.10": "python3.13",
    "python3.11": "python3.13", "python3.12": "python3.13",
    "nodejs18.x": "nodejs22.x", "nodejs20.x": "nodejs22.x",
    "java11": "java21", "java17": "java21",
    "dotnet6": "dotnet8",
}

# Runtime max concurrency per vCPU (from LMI capacity formula)
RUNTIME_MAX_CONCURRENCY = {
    "python": 16, "nodejs": 64, "java": 32, "dotnet": 32,
}

# ---------------------------------------------------------------------------
# Pricing constants (us-east-1)
# ---------------------------------------------------------------------------
HOURS_PER_MONTH = 730
LMI_MANAGEMENT_FEE = 0.15
LAMBDA_REQUEST_PRICE_PER_M = 0.20
LAMBDA_GBSEC_PRICE_ARM = 0.0000133333
LAMBDA_GBSEC_PRICE_X86 = 0.0000166667
MIN_FUNCTION_MEMORY_MB = 2048

# Savings plan discounts (from LMI cost calculation docs)
SAVINGS_PLANS = {
    "on_demand":    {"label": "On-Demand",           "lmi_discount": 0.00, "lambda_discount": 0.00},
    "compute_sp":   {"label": "Compute SP (1yr)",    "lmi_discount": 0.50, "lambda_discount": 0.17},
    "ec2_sp":       {"label": "EC2 Instance SP",     "lmi_discount": 0.72, "lambda_discount": 0.00},
    "reserved_3yr": {"label": "Reserved (3yr)",      "lmi_discount": 0.75, "lambda_discount": 0.00},
}

# Instance types for capacity planning (Graviton, us-east-1 On-Demand)
EC2_INSTANCES = {
    "c7g.xlarge":   {"vcpus": 4,  "memory_gb": 8,   "price_per_hour": 0.1450, "ratio": 2},
    "c7g.2xlarge":  {"vcpus": 8,  "memory_gb": 16,  "price_per_hour": 0.2900, "ratio": 2},
    "m7g.xlarge":   {"vcpus": 4,  "memory_gb": 16,  "price_per_hour": 0.1632, "ratio": 4},
    "m7g.2xlarge":  {"vcpus": 8,  "memory_gb": 32,  "price_per_hour": 0.3264, "ratio": 4},
    "r7g.xlarge":   {"vcpus": 4,  "memory_gb": 32,  "price_per_hour": 0.2134, "ratio": 8},
}

OVERHEAD_VCPUS = 1
OVERHEAD_MEMORY_MB = 1024

# Scoring thresholds
THRESHOLDS = {
    "min_monthly_invocations": 1_000_000,
    "ideal_monthly_invocations": 10_000_000,
    "min_avg_duration_ms": 100,
    "ideal_avg_duration_ms": 1000,
    "min_concurrency": 5,
    "ideal_concurrency": 50,
    "min_memory_mb": 256,
}


# ---------------------------------------------------------------------------
# LMI capacity formula (aligned with lmi_calculator.py)
# ---------------------------------------------------------------------------

def lmi_capacity_plan(runtime_key, memory_per_exec_mb, peak_concurrency, instance_type):
    """Run the LMI capacity formula and return instance count + packing details."""
    inst = EC2_INSTANCES[instance_type]
    mem_vcpu_ratio = inst["ratio"]

    # Step 1: concurrency per vCPU
    runtime_limit = RUNTIME_MAX_CONCURRENCY.get(runtime_key, 16)
    memory_per_vcpu_mb = mem_vcpu_ratio * 1024
    memory_limit = int(memory_per_vcpu_mb / memory_per_exec_mb) if memory_per_exec_mb > 0 else runtime_limit
    conc_per_vcpu = max(1, min(runtime_limit, memory_limit))

    # Step 2: function memory (min 2048 MB)
    function_memory_mb = max(MIN_FUNCTION_MEMORY_MB, math.ceil(memory_per_exec_mb * conc_per_vcpu))

    # Step 3: vCPUs per execution environment
    vcpus_per_env = max(1, function_memory_mb // (mem_vcpu_ratio * 1024))

    # Step 4: concurrency per environment
    conc_per_env = conc_per_vcpu * vcpus_per_env

    # Step 5: environments needed
    target = max(1, int(peak_concurrency))
    envs_needed = math.ceil(target / conc_per_env)

    # Step 6: packing — how many envs fit per instance
    usable_vcpus = inst["vcpus"] - OVERHEAD_VCPUS
    usable_memory_mb = (inst["memory_gb"] * 1024) - OVERHEAD_MEMORY_MB
    by_vcpu = usable_vcpus // vcpus_per_env if vcpus_per_env > 0 else 0
    by_memory = usable_memory_mb // function_memory_mb if function_memory_mb > 0 else 0
    envs_per_instance = max(1, min(by_vcpu, by_memory))

    # Step 7: instances (min 3 for AZ resiliency)
    instances = max(3, math.ceil(envs_needed / envs_per_instance))

    return {
        "instances": instances,
        "envs_per_instance": envs_per_instance,
        "envs_needed": envs_needed,
        "conc_per_env": conc_per_env,
        "function_memory_mb": function_memory_mb,
    }


def pick_best_instance(runtime_key, memory_per_exec_mb, peak_concurrency):
    """Try each instance type and pick the one with lowest on-demand LMI cost."""
    best = None
    for itype, inst in EC2_INSTANCES.items():
        plan = lmi_capacity_plan(runtime_key, memory_per_exec_mb, peak_concurrency, itype)
        monthly_ec2 = plan["instances"] * inst["price_per_hour"] * HOURS_PER_MONTH
        total = monthly_ec2 * (1 + LMI_MANAGEMENT_FEE)
        if best is None or total < best["total"]:
            best = {"instance_type": itype, "plan": plan, "total": total}
    return best


# ---------------------------------------------------------------------------
# Savings estimate
# ---------------------------------------------------------------------------

def estimate_savings(monthly_invocations, avg_duration_sec, memory_per_exec_mb,
                     std_lambda_memory_mb, peak_concurrency, arch, runtime_key):
    """Full savings estimate: Standard Lambda vs LMI across savings plan tiers."""
    gb_sec_price = LAMBDA_GBSEC_PRICE_ARM if arch == "arm64" else LAMBDA_GBSEC_PRICE_X86
    request_cost = (monthly_invocations / 1_000_000) * LAMBDA_REQUEST_PRICE_PER_M

    # Standard Lambda cost (uses the function's actual configured memory)
    std_memory_gb = std_lambda_memory_mb / 1024
    std_compute_od = monthly_invocations * avg_duration_sec * std_memory_gb * gb_sec_price

    # LMI capacity plan (pick best instance type)
    best = pick_best_instance(runtime_key, memory_per_exec_mb, peak_concurrency)
    inst = EC2_INSTANCES[best["instance_type"]]
    plan = best["plan"]
    ec2_od_monthly = plan["instances"] * inst["price_per_hour"] * HOURS_PER_MONTH
    mgmt_fee = ec2_od_monthly * LMI_MANAGEMENT_FEE  # always on OD price

    # Build comparison across savings plans
    tiers = {}
    for key, sp in SAVINGS_PLANS.items():
        std_compute = std_compute_od * (1 - sp["lambda_discount"])
        std_total = std_compute + request_cost

        lmi_ec2 = ec2_od_monthly * (1 - sp["lmi_discount"])
        lmi_total = lmi_ec2 + mgmt_fee + request_cost

        savings = std_total - lmi_total
        savings_pct = (savings / std_total * 100) if std_total > 0 else 0

        tiers[key] = {
            "label": sp["label"],
            "standard_lambda": round(std_total, 2),
            "lmi_cost": round(lmi_total, 2),
            "savings": round(savings, 2),
            "savings_pct": round(savings_pct, 1),
        }

    return {
        "instance_type": best["instance_type"],
        "instances": plan["instances"],
        "envs_per_instance": plan["envs_per_instance"],
        "function_memory_mb": plan["function_memory_mb"],
        "conc_per_env": plan["conc_per_env"],
        "tiers": tiers,
    }


# ---------------------------------------------------------------------------
# CloudWatch + Lambda scanning
# ---------------------------------------------------------------------------

def get_lambda_functions(lambda_client):
    functions = []
    paginator = lambda_client.get_paginator("list_functions")
    for page in paginator.paginate():
        functions.extend(page["Functions"])
    return functions


def get_metric_stats(cw_client, function_name, metric_name, stat, days, period=86400):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        resp = cw_client.get_metric_statistics(
            Namespace="AWS/Lambda", MetricName=metric_name,
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start, EndTime=end, Period=period, Statistics=[stat],
        )
        return resp.get("Datapoints", [])
    except ClientError:
        return []


def resolve_runtime_key(runtime):
    """Map a Lambda runtime string to an LMI runtime key."""
    if runtime in LMI_RUNTIME_MAP:
        return LMI_RUNTIME_MAP[runtime]
    if runtime in UPGRADEABLE_RUNTIMES:
        return LMI_RUNTIME_MAP.get(UPGRADEABLE_RUNTIMES[runtime], "python")
    return None


def analyze_function(lambda_client, cw_client, func, days):
    name = func["FunctionName"]
    runtime = func.get("Runtime", "")
    memory_mb = func.get("MemorySize", 128)
    arch = func.get("Architectures", ["x86_64"])[0]
    timeout = func.get("Timeout", 3)

    runtime_key = resolve_runtime_key(runtime)
    if runtime_key is None:
        return None

    lmi_ready = runtime in LMI_RUNTIME_MAP

    # CloudWatch metrics
    inv_points = get_metric_stats(cw_client, name, "Invocations", "Sum", days)
    total_invocations = sum(dp["Sum"] for dp in inv_points)
    monthly_invocations = total_invocations * (30 / days) if days != 30 else total_invocations

    if monthly_invocations < THRESHOLDS["min_monthly_invocations"] * 0.1:
        return None

    dur_points = get_metric_stats(cw_client, name, "Duration", "Average", days)
    avg_duration_ms = (sum(dp["Average"] for dp in dur_points) / len(dur_points)) if dur_points else 0

    conc_points = get_metric_stats(cw_client, name, "ConcurrentExecutions", "Maximum", days)
    peak_concurrency = max((dp["Maximum"] for dp in conc_points), default=0)

    throttle_points = get_metric_stats(cw_client, name, "Throttles", "Sum", days)
    total_throttles = sum(dp["Sum"] for dp in throttle_points)

    has_provisioned = False
    try:
        pc_resp = lambda_client.list_provisioned_concurrency_configs(FunctionName=name)
        has_provisioned = len(pc_resp.get("ProvisionedConcurrencyConfigs", [])) > 0
    except ClientError:
        pass

    # --- Scoring ---
    score = 0
    reasons = []

    if monthly_invocations >= THRESHOLDS["ideal_monthly_invocations"]:
        score += 30
        reasons.append(f"High volume: {monthly_invocations:,.0f} inv/month")
    elif monthly_invocations >= THRESHOLDS["min_monthly_invocations"]:
        score += 15
        reasons.append(f"Moderate volume: {monthly_invocations:,.0f} inv/month")
    else:
        reasons.append(f"Low volume: {monthly_invocations:,.0f} inv/month")

    if avg_duration_ms >= THRESHOLDS["ideal_avg_duration_ms"]:
        score += 25
        reasons.append(f"Long duration: {avg_duration_ms:,.0f}ms avg")
    elif avg_duration_ms >= THRESHOLDS["min_avg_duration_ms"]:
        score += 12
        reasons.append(f"Moderate duration: {avg_duration_ms:,.0f}ms avg")
    else:
        reasons.append(f"Short duration: {avg_duration_ms:,.0f}ms avg (LMI less beneficial)")

    if peak_concurrency >= THRESHOLDS["ideal_concurrency"]:
        score += 20
        reasons.append(f"High concurrency: {peak_concurrency:.0f} peak")
    elif peak_concurrency >= THRESHOLDS["min_concurrency"]:
        score += 10
        reasons.append(f"Moderate concurrency: {peak_concurrency:.0f} peak")

    if memory_mb >= 512:
        score += 10
        reasons.append(f"Good memory fit: {memory_mb} MB")
    elif memory_mb >= THRESHOLDS["min_memory_mb"]:
        score += 5

    if has_provisioned:
        score += 15
        reasons.append("Has provisioned concurrency (LMI replaces this)")

    if not lmi_ready:
        score -= 5

    if total_throttles > 0:
        reasons.append(f"Throttled {total_throttles:,.0f} times (LMI may help)")
        score += 5

    runtime_status = "ready" if lmi_ready else f"upgrade needed ({runtime} → {UPGRADEABLE_RUNTIMES[runtime]})"

    # --- Savings estimate using real capacity formula ---
    # For Standard Lambda comparison, use the function's actual memory.
    # For LMI capacity planning, use memory_per_exec = configured memory
    # (best approximation without profiling data).
    savings = estimate_savings(
        monthly_invocations=monthly_invocations,
        avg_duration_sec=avg_duration_ms / 1000,
        memory_per_exec_mb=memory_mb,
        std_lambda_memory_mb=memory_mb,
        peak_concurrency=max(peak_concurrency, 1),
        arch=arch,
        runtime_key=runtime_key,
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
        "savings": savings,
    }


def classify(score):
    if score >= 60:
        return "STRONG"
    elif score >= 35:
        return "MODERATE"
    elif score >= 15:
        return "WEAK"
    return "NOT RECOMMENDED"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(candidates, region, days, total_functions):
    strong = [c for c in candidates if c["lmi_score"] >= 60]
    moderate = [c for c in candidates if 35 <= c["lmi_score"] < 60]
    weak = [c for c in candidates if c["lmi_score"] < 35]

    print(f"{'=' * 90}")
    print(f"  LMI CANDIDATE REPORT — {region}")
    print(f"  Analysis period: last {days} days | Functions scanned: {total_functions}")
    print(f"  Candidates found: {len(strong)} strong, {len(moderate)} moderate, {len(weak)} weak")
    print(f"  ⚠️  Estimates use configured memory (not actual usage) and us-east-1 pricing")
    print(f"{'=' * 90}\n")

    for c in candidates:
        rating = classify(c["lmi_score"])
        icon = {"STRONG": "🟢", "MODERATE": "🟡", "WEAK": "🟠", "NOT RECOMMENDED": "🔴"}[rating]
        s = c["savings"]
        od = s["tiers"]["on_demand"]

        print(f"{icon} {c['function_name']}  [{rating} — score {c['lmi_score']}]")
        print(f"   Runtime: {c['runtime']} ({c['runtime_status']})")
        print(f"   Memory: {c['memory_mb']} MB | Arch: {c['architecture']} | Timeout: {c['timeout_sec']}s")
        print(f"   Invocations: {c['monthly_invocations']:,.0f}/month | Avg duration: {c['avg_duration_ms']:,.0f}ms | Peak concurrency: {c['peak_concurrency']:.0f}")
        if c["has_provisioned_concurrency"]:
            print(f"   ⚡ Has provisioned concurrency (LMI can replace this)")
        if c["throttle_count"] > 0:
            print(f"   ⚠️  Throttled {c['throttle_count']:,.0f} times")
        for reason in c["reasons"]:
            print(f"   • {reason}")

        # Savings estimate
        print(f"\n   📊 Savings Estimate (LMI on {s['instance_type']}, {s['instances']} instances, "
              f"{s['envs_per_instance']} envs/inst, {s['conc_per_env']} conc/env)")
        print(f"   {'Plan':<22} {'Std Lambda':>12} {'LMI':>12} {'Savings':>12} {'%':>7}")
        print(f"   {'─' * 22} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 7}")
        for tier in s["tiers"].values():
            sav_str = f"${tier['savings']:,.2f}" if tier['savings'] >= 0 else f"-${abs(tier['savings']):,.2f}"
            pct_str = f"{tier['savings_pct']:+.0f}%" if tier['standard_lambda'] > 0 else "N/A"
            print(f"   {tier['label']:<22} ${tier['standard_lambda']:>10,.2f} ${tier['lmi_cost']:>10,.2f} {sav_str:>12} {pct_str:>7}")

        # Flag when LMI is more expensive at all tiers
        if all(t["savings"] < 0 for t in s["tiers"].values()):
            print(f"   ⚠️  LMI is more expensive at current traffic — revisit at higher volume")
        print()

    # Portfolio summary for strong candidates
    if strong:
        print(f"{'─' * 90}")
        print(f"  💡 STRONG CANDIDATES — PORTFOLIO SAVINGS SUMMARY\n")
        print(f"  {'Function':<35} {'On-Demand':>12} {'Compute SP':>12} {'EC2 SP':>12} {'3yr RI':>12}")
        print(f"  {'─' * 35} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 12}")
        totals = {k: 0 for k in SAVINGS_PLANS}
        for c in strong:
            row = []
            for key in SAVINGS_PLANS:
                sav = c["savings"]["tiers"][key]["savings"]
                totals[key] += sav
                row.append(f"${sav:>10,.2f}")
            print(f"  {c['function_name'][:35]:<35} {row[0]:>12} {row[1]:>12} {row[2]:>12} {row[3]:>12}")
        print(f"  {'─' * 35} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 12}")
        row = [f"${totals[k]:>10,.2f}" for k in SAVINGS_PLANS]
        print(f"  {'TOTAL':<35} {row[0]:>12} {row[1]:>12} {row[2]:>12} {row[3]:>12}")
        print(f"\n  Use the LMI Pricing Calculator for detailed capacity planning.")
        print(f"{'─' * 90}\n")


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

    log = (lambda msg: None) if args.json else (lambda msg: print(msg))
    log(f"\n🔍 Scanning Lambda functions in {args.region} (last {args.days} days)...\n")

    functions = get_lambda_functions(lambda_client)
    log(f"Found {len(functions)} Lambda functions. Analyzing metrics...\n")

    candidates = []
    for func in functions:
        result = analyze_function(lambda_client, cw_client, func, args.days)
        if result:
            candidates.append(result)

    candidates.sort(key=lambda x: x["lmi_score"], reverse=True)

    if args.json:
        print(json.dumps(candidates, indent=2, default=str))
        return

    if not candidates:
        print("No LMI candidates found. Functions may have unsupported runtimes or insufficient traffic.")
        return

    print_report(candidates, args.region, args.days, len(functions))


if __name__ == "__main__":
    main()
