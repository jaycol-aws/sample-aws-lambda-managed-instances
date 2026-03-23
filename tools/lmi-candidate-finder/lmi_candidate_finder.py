#!/usr/bin/env python3
"""
LMI Candidate Finder — Scan Lambda functions and identify candidates for
Lambda Managed Instances based on invocation patterns, duration, memory,
and runtime compatibility. Includes savings estimates using the LMI
capacity formula with live pricing from the AWS Pricing API.

Modes:
    Scan all functions:
        python lmi_candidate_finder.py --region us-east-1

    Analyze a single function:
        python lmi_candidate_finder.py --region us-east-1 --function my-func \
            --memory-per-exec 200 --workload-type cpu-heavy

    Multi-region scan:
        python lmi_candidate_finder.py --region us-east-1,us-west-2,eu-west-1

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
# LMI-supported runtimes
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

RUNTIME_MAX_CONCURRENCY = {
    "python": 16, "nodejs": 64, "java": 32, "dotnet": 32,
}

# Workload types: CPU% per invocation determines sustainable concurrency
WORKLOAD_TYPES = {
    "io-heavy":  {"label": "IO-Heavy (proxy/queue)", "cpu_pct": 12.5},
    "balanced":  {"label": "Balanced (mixed)",       "cpu_pct": 25.0},
    "cpu-heavy": {"label": "CPU-Heavy (compute)",    "cpu_pct": 50.0},
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HOURS_PER_MONTH = 730
LMI_MANAGEMENT_FEE = 0.15
MIN_FUNCTION_MEMORY_MB = 2048
OVERHEAD_VCPUS = 1
OVERHEAD_MEMORY_MB = 1024

# Instance specs (pricing fetched dynamically, these are structural only)
INSTANCE_SPECS = {
    "c7g.xlarge":  {"vcpus": 4,  "memory_gb": 8,   "ratio": 2},
    "c7g.2xlarge": {"vcpus": 8,  "memory_gb": 16,  "ratio": 2},
    "m7g.xlarge":  {"vcpus": 4,  "memory_gb": 16,  "ratio": 4},
    "m7g.2xlarge": {"vcpus": 8,  "memory_gb": 32,  "ratio": 4},
    "r7g.xlarge":  {"vcpus": 4,  "memory_gb": 32,  "ratio": 8},
}

# Savings plan discounts
SAVINGS_PLANS = {
    "on_demand":    {"label": "On-Demand",        "lmi_discount": 0.00, "lambda_discount": 0.00},
    "compute_sp":   {"label": "Compute SP (1yr)", "lmi_discount": 0.50, "lambda_discount": 0.17},
    "ec2_sp":       {"label": "EC2 Instance SP",  "lmi_discount": 0.72, "lambda_discount": 0.00},
    "reserved_3yr": {"label": "Reserved (3yr)",   "lmi_discount": 0.75, "lambda_discount": 0.00},
}

# Region code to Pricing API location name
REGION_NAMES = {
    "us-east-1": "US East (N. Virginia)", "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)", "us-west-2": "US West (Oregon)",
    "eu-west-1": "Europe (Ireland)", "eu-west-2": "Europe (London)",
    "eu-west-3": "Europe (Paris)", "eu-central-1": "Europe (Frankfurt)",
    "eu-north-1": "Europe (Stockholm)",
    "ap-northeast-1": "Asia Pacific (Tokyo)", "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)", "ap-south-1": "Asia Pacific (Mumbai)",
    "sa-east-1": "South America (Sao Paulo)", "ca-central-1": "Canada (Central)",
}

THRESHOLDS = {
    "min_monthly_invocations": 1_000_000,
    "ideal_monthly_invocations": 10_000_000,
    "min_avg_duration_ms": 100,
    "ideal_avg_duration_ms": 1000,
    "min_concurrency": 5,
    "ideal_concurrency": 50,
    "min_memory_mb": 256,
    "min_traffic_hours_pct": 0.25,  # must have traffic in >=25% of hours
}


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

class ProgressBar:
    def __init__(self, total, prefix="", width=40, enabled=True):
        self.total = total
        self.prefix = prefix
        self.width = width
        self.enabled = enabled
        self.current = 0

    def update(self, name=""):
        if not self.enabled:
            return
        self.current += 1
        pct = self.current / self.total if self.total > 0 else 1
        filled = int(self.width * pct)
        bar = "█" * filled + "░" * (self.width - filled)
        label = name[:30] if name else ""
        sys.stderr.write(f"\r  {self.prefix} |{bar}| {self.current}/{self.total} {label:<30}")
        sys.stderr.flush()

    def finish(self):
        if self.enabled:
            sys.stderr.write("\n")
            sys.stderr.flush()


# ---------------------------------------------------------------------------
# Dynamic pricing
# ---------------------------------------------------------------------------

def fetch_ec2_price(pricing_client, instance_type, location):
    """Fetch On-Demand hourly price for an EC2 instance type from the Pricing API."""
    try:
        resp = pricing_client.get_products(
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ],
            MaxResults=1,
        )
        if resp["PriceList"]:
            product = json.loads(resp["PriceList"][0])
            for term in product["terms"]["OnDemand"].values():
                for dim in term["priceDimensions"].values():
                    return float(dim["pricePerUnit"]["USD"])
    except (ClientError, KeyError, ValueError):
        pass
    return None


def fetch_lambda_pricing(pricing_client, location):
    """Fetch Lambda GB-second (x86 + ARM) and request pricing."""
    prices = {"gbsec_x86": None, "gbsec_arm": None, "request": None}
    for group, key in [("AWS-Lambda-Duration", "gbsec_x86"),
                       ("AWS-Lambda-Duration-ARM", "gbsec_arm"),
                       ("AWS-Lambda-Requests", "request")]:
        try:
            resp = pricing_client.get_products(
                ServiceCode="AWSLambda",
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                    {"Type": "TERM_MATCH", "Field": "group", "Value": group},
                ],
                MaxResults=10,
            )
            # For duration tiers, take Tier-1 (highest price = first 6B GB-sec)
            best = 0
            for item in resp["PriceList"]:
                product = json.loads(item)
                for term in product["terms"]["OnDemand"].values():
                    for dim in term["priceDimensions"].values():
                        p = float(dim["pricePerUnit"]["USD"])
                        if key == "request":
                            prices[key] = p
                        elif p > best:
                            best = p
            if key != "request" and best > 0:
                prices[key] = best
        except (ClientError, KeyError, ValueError):
            pass
    return prices


def load_pricing(session, region):
    """Load all pricing for a region. Returns dict with ec2 prices and lambda prices."""
    location = REGION_NAMES.get(region)
    if not location:
        return None

    # Pricing API only available in us-east-1 and ap-south-1
    pricing_client = session.client("pricing", region_name="us-east-1")

    ec2_prices = {}
    for itype in INSTANCE_SPECS:
        price = fetch_ec2_price(pricing_client, itype, location)
        if price:
            ec2_prices[itype] = price

    lambda_prices = fetch_lambda_pricing(pricing_client, location)

    if not ec2_prices or not lambda_prices["gbsec_x86"]:
        return None

    return {"ec2": ec2_prices, "lambda": lambda_prices, "region": region, "location": location}


# ---------------------------------------------------------------------------
# LMI capacity formula
# ---------------------------------------------------------------------------

def lmi_capacity_plan(runtime_key, memory_per_exec_mb, peak_concurrency,
                      instance_type, workload_type="io-heavy"):
    """Run the LMI capacity formula with workload-type-aware concurrency."""
    spec = INSTANCE_SPECS[instance_type]
    mem_vcpu_ratio = spec["ratio"]

    # Step 1: concurrency per vCPU (workload-type aware)
    runtime_limit = RUNTIME_MAX_CONCURRENCY.get(runtime_key, 16)
    memory_per_vcpu_mb = mem_vcpu_ratio * 1024
    memory_limit = int(memory_per_vcpu_mb / memory_per_exec_mb) if memory_per_exec_mb > 0 else runtime_limit

    # Sustainable concurrency based on CPU usage pattern
    cpu_pct = WORKLOAD_TYPES[workload_type]["cpu_pct"]
    sustainable_limit = int(100 / cpu_pct)

    conc_per_vcpu = max(1, min(runtime_limit, memory_limit, sustainable_limit))

    # Step 2: function memory (min 2048 MB)
    function_memory_mb = max(MIN_FUNCTION_MEMORY_MB, math.ceil(memory_per_exec_mb * conc_per_vcpu))

    # Step 3: vCPUs per execution environment
    vcpus_per_env = max(1, function_memory_mb // (mem_vcpu_ratio * 1024))

    # Step 4: concurrency per environment
    conc_per_env = conc_per_vcpu * vcpus_per_env

    # Step 5: environments needed
    target = max(1, int(peak_concurrency))
    envs_needed = math.ceil(target / conc_per_env)

    # Step 6: packing
    usable_vcpus = spec["vcpus"] - OVERHEAD_VCPUS
    usable_memory_mb = (spec["memory_gb"] * 1024) - OVERHEAD_MEMORY_MB
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
        "workload_type": workload_type,
    }


def pick_best_instance(runtime_key, memory_per_exec_mb, peak_concurrency,
                       pricing, workload_type="io-heavy"):
    """Try each instance type and pick the one with lowest on-demand LMI cost."""
    best = None
    for itype in INSTANCE_SPECS:
        if itype not in pricing["ec2"]:
            continue
        plan = lmi_capacity_plan(runtime_key, memory_per_exec_mb, peak_concurrency,
                                 itype, workload_type)
        monthly_ec2 = plan["instances"] * pricing["ec2"][itype] * HOURS_PER_MONTH
        total = monthly_ec2 * (1 + LMI_MANAGEMENT_FEE)
        if best is None or total < best["total"]:
            best = {"instance_type": itype, "plan": plan, "total": total}
    return best


# ---------------------------------------------------------------------------
# Savings estimate
# ---------------------------------------------------------------------------

def estimate_savings(monthly_invocations, avg_duration_sec, memory_per_exec_mb,
                     std_lambda_memory_mb, peak_concurrency, arch, runtime_key,
                     pricing, workload_type="io-heavy"):
    """Full savings estimate: Standard Lambda vs LMI across savings plan tiers."""
    lp = pricing["lambda"]
    gbsec_price = lp["gbsec_arm"] if arch == "arm64" else lp["gbsec_x86"]
    request_price = lp["request"]
    request_cost = monthly_invocations * request_price

    # Standard Lambda cost
    std_memory_gb = std_lambda_memory_mb / 1024
    std_compute_od = monthly_invocations * avg_duration_sec * std_memory_gb * gbsec_price

    # LMI capacity plan
    best = pick_best_instance(runtime_key, memory_per_exec_mb, peak_concurrency,
                              pricing, workload_type)
    if not best:
        return None
    inst_price = pricing["ec2"][best["instance_type"]]
    plan = best["plan"]
    ec2_od_monthly = plan["instances"] * inst_price * HOURS_PER_MONTH
    mgmt_fee = ec2_od_monthly * LMI_MANAGEMENT_FEE

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
        "workload_type": WORKLOAD_TYPES[workload_type]["label"],
        "tiers": tiers,
    }


# ---------------------------------------------------------------------------
# CloudWatch + Lambda scanning
# ---------------------------------------------------------------------------

def get_lambda_functions(lambda_client, function_name=None):
    if function_name:
        try:
            resp = lambda_client.get_function(FunctionName=function_name)
            return [resp["Configuration"]]
        except ClientError as e:
            print(f"Error: function '{function_name}' not found: {e}", file=sys.stderr)
            return []
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


def check_traffic_regularity(cw_client, function_name, days):
    """Check hourly invocation pattern. Returns (active_hours, total_hours, is_regular)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        resp = cw_client.get_metric_statistics(
            Namespace="AWS/Lambda", MetricName="Invocations",
            Dimensions=[{"Name": "FunctionName", "Value": function_name}],
            StartTime=start, EndTime=end, Period=3600, Statistics=["Sum"],
        )
        points = resp.get("Datapoints", [])
    except ClientError:
        return 0, 1, False

    total_hours = days * 24
    active_hours = len([p for p in points if p.get("Sum", 0) > 0])
    pct = active_hours / total_hours if total_hours > 0 else 0
    return active_hours, total_hours, pct >= THRESHOLDS["min_traffic_hours_pct"]


def resolve_runtime_key(runtime):
    if runtime in LMI_RUNTIME_MAP:
        return LMI_RUNTIME_MAP[runtime]
    if runtime in UPGRADEABLE_RUNTIMES:
        return LMI_RUNTIME_MAP.get(UPGRADEABLE_RUNTIMES[runtime], "python")
    return None


def analyze_function(lambda_client, cw_client, func, days, pricing,
                     workload_type="io-heavy", memory_override=None):
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

    # Traffic regularity check
    active_hours, total_hours, is_regular = check_traffic_regularity(cw_client, name, days)

    has_provisioned = False
    try:
        pc_resp = lambda_client.list_provisioned_concurrency_configs(FunctionName=name)
        has_provisioned = len(pc_resp.get("ProvisionedConcurrencyConfigs", [])) > 0
    except ClientError:
        pass

    # --- Disqualifiers: skip functions unsuitable for LMI ---
    skip_reasons = []
    if peak_concurrency < 2 and not has_provisioned:
        skip_reasons.append(f"Low/no concurrency ({peak_concurrency:.0f} peak) — LMI needs sustained concurrent load")
    if not is_regular:
        skip_reasons.append(f"Irregular traffic (active {active_hours}/{total_hours} hours) — LMI needs steady traffic")
    if skip_reasons:
        return {"function_name": name, "skipped": True, "skip_reasons": skip_reasons,
                "runtime": runtime, "peak_concurrency": peak_concurrency,
                "monthly_invocations": monthly_invocations, "active_hours": active_hours,
                "total_hours": total_hours}

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

    traffic_pct = (active_hours / total_hours * 100) if total_hours > 0 else 0
    reasons.append(f"Traffic regularity: {active_hours}/{total_hours} hours ({traffic_pct:.0f}%)")

    runtime_status = "ready" if lmi_ready else f"upgrade needed ({runtime} → {UPGRADEABLE_RUNTIMES[runtime]})"

    # Memory for capacity planning: use override if provided, else configured
    mem_per_exec = memory_override if memory_override else memory_mb

    savings = estimate_savings(
        monthly_invocations=monthly_invocations,
        avg_duration_sec=avg_duration_ms / 1000,
        memory_per_exec_mb=mem_per_exec,
        std_lambda_memory_mb=memory_mb,
        peak_concurrency=max(peak_concurrency, 1),
        arch=arch, runtime_key=runtime_key,
        pricing=pricing, workload_type=workload_type,
    )

    return {
        "function_name": name, "skipped": False,
        "runtime": runtime, "runtime_status": runtime_status,
        "memory_mb": memory_mb,
        "memory_per_exec_mb": mem_per_exec,
        "architecture": arch, "timeout_sec": timeout,
        "monthly_invocations": monthly_invocations,
        "avg_duration_ms": avg_duration_ms,
        "peak_concurrency": peak_concurrency,
        "has_provisioned_concurrency": has_provisioned,
        "throttle_count": total_throttles,
        "active_hours": active_hours, "total_hours": total_hours,
        "lmi_score": min(score, 100),
        "reasons": reasons, "savings": savings,
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

def print_report(candidates, skipped, region, days, total_functions, pricing):
    viable = [c for c in candidates if not c.get("skipped")]
    strong = [c for c in viable if c["lmi_score"] >= 60]
    moderate = [c for c in viable if 35 <= c["lmi_score"] < 60]
    weak = [c for c in viable if c["lmi_score"] < 35]

    print(f"\n{'=' * 90}")
    print(f"  LMI CANDIDATE REPORT — {region}")
    print(f"  Pricing: {pricing['location']} (live from AWS Pricing API)")
    print(f"  Analysis period: last {days} days | Functions scanned: {total_functions}")
    print(f"  Candidates: {len(strong)} strong, {len(moderate)} moderate, {len(weak)} weak | Skipped: {len(skipped)}")
    print(f"{'=' * 90}\n")

    for c in viable:
        rating = classify(c["lmi_score"])
        icon = {"STRONG": "🟢", "MODERATE": "🟡", "WEAK": "🟠", "NOT RECOMMENDED": "🔴"}[rating]
        s = c["savings"]

        print(f"{icon} {c['function_name']}  [{rating} — score {c['lmi_score']}]")
        print(f"   Runtime: {c['runtime']} ({c['runtime_status']})")
        mem_note = f" (actual: {c['memory_per_exec_mb']} MB)" if c["memory_per_exec_mb"] != c["memory_mb"] else ""
        print(f"   Memory: {c['memory_mb']} MB configured{mem_note} | Arch: {c['architecture']} | Timeout: {c['timeout_sec']}s")
        print(f"   Invocations: {c['monthly_invocations']:,.0f}/month | Avg duration: {c['avg_duration_ms']:,.0f}ms | Peak concurrency: {c['peak_concurrency']:.0f}")
        if c["has_provisioned_concurrency"]:
            print(f"   ⚡ Has provisioned concurrency (LMI can replace this)")
        if c["throttle_count"] > 0:
            print(f"   ⚠️  Throttled {c['throttle_count']:,.0f} times")
        for reason in c["reasons"]:
            print(f"   • {reason}")

        if s:
            print(f"\n   📊 Savings Estimate ({s['workload_type']}, LMI on {s['instance_type']}, "
                  f"{s['instances']} instances, {s['envs_per_instance']} envs/inst, {s['conc_per_env']} conc/env)")
            print(f"   {'Plan':<22} {'Std Lambda':>12} {'LMI':>12} {'Savings':>12} {'%':>7}")
            print(f"   {'─' * 22} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 7}")
            for tier in s["tiers"].values():
                sav_str = f"${tier['savings']:,.2f}" if tier['savings'] >= 0 else f"-${abs(tier['savings']):,.2f}"
                pct_str = f"{tier['savings_pct']:+.0f}%" if tier['standard_lambda'] > 0 else "N/A"
                print(f"   {tier['label']:<22} ${tier['standard_lambda']:>10,.2f} ${tier['lmi_cost']:>10,.2f} {sav_str:>12} {pct_str:>7}")
            if all(t["savings"] < 0 for t in s["tiers"].values()):
                print(f"   ⚠️  LMI is more expensive at current traffic — revisit at higher volume")
        print()

    # Skipped functions summary
    if skipped:
        print(f"  ⏭️  {len(skipped)} functions skipped (low/no concurrency or irregular traffic):")
        for s in skipped[:10]:
            reasons = "; ".join(s["skip_reasons"])
            print(f"     • {s['function_name']}: {reasons}")
        if len(skipped) > 10:
            print(f"     ... and {len(skipped) - 10} more")
        print()

    # Portfolio summary
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Find Lambda functions that are good candidates for LMI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan all functions in a region
  python lmi_candidate_finder.py --region us-east-1

  # Scan multiple regions
  python lmi_candidate_finder.py --region us-east-1,us-west-2,eu-west-1

  # Analyze a single function with known memory usage and workload type
  python lmi_candidate_finder.py --region us-east-1 --function my-api \\
      --memory-per-exec 200 --workload-type balanced

  # CPU-heavy Java function
  python lmi_candidate_finder.py --region us-east-1 --function my-processor \\
      --memory-per-exec 512 --workload-type cpu-heavy

Workload types:
  io-heavy   12.5%% CPU per invocation (proxy, queue consumer, API gateway)
  balanced   25%% CPU per invocation (mixed IO and compute)
  cpu-heavy  50%% CPU per invocation (data processing, ML inference, crypto)
""",
    )
    parser.add_argument("--region", default="us-east-1",
                        help="AWS region(s), comma-separated (default: us-east-1)")
    parser.add_argument("--profile", default=None, help="AWS CLI profile name")
    parser.add_argument("--days", type=int, default=14,
                        help="Days of CloudWatch data to analyze (default: 14)")
    parser.add_argument("--function", default=None,
                        help="Analyze a single function by name (default: scan all)")
    parser.add_argument("--memory-per-exec", type=int, default=None, dest="memory_per_exec",
                        help="Actual memory used per execution in MB (default: function's configured MemorySize)")
    parser.add_argument("--workload-type", default="io-heavy",
                        choices=list(WORKLOAD_TYPES.keys()), dest="workload_type",
                        help="Workload CPU profile (default: io-heavy)")
    parser.add_argument("--min-invocations", type=int, default=None,
                        help="Override minimum monthly invocation threshold")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.min_invocations is not None:
        THRESHOLDS["min_monthly_invocations"] = args.min_invocations

    regions = [r.strip() for r in args.region.split(",")]
    is_json = args.json
    log = (lambda msg: None) if is_json else (lambda msg: print(msg, file=sys.stderr))

    session_kwargs = {}
    if args.profile:
        session_kwargs["profile_name"] = args.profile
    session = boto3.Session(**session_kwargs)

    all_results = []

    for region in regions:
        if region not in REGION_NAMES:
            log(f"⚠️  Region '{region}' not in pricing map — skipping")
            continue

        log(f"\n💲 Fetching live pricing for {region} ({REGION_NAMES[region]})...")
        pricing = load_pricing(session, region)
        if not pricing:
            log(f"⚠️  Could not fetch pricing for {region} — skipping")
            continue
        log(f"   EC2: {len(pricing['ec2'])} instance types | Lambda: ✓")

        lambda_client = session.client("lambda", region_name=region)
        cw_client = session.client("cloudwatch", region_name=region)

        log(f"🔍 Scanning Lambda functions in {region} (last {args.days} days)...")
        functions = get_lambda_functions(lambda_client, args.function)
        log(f"   Found {len(functions)} function(s). Analyzing metrics...")

        progress = ProgressBar(len(functions), prefix=region, enabled=not is_json)
        candidates = []
        skipped = []

        for func in functions:
            name = func.get("FunctionName", "")
            progress.update(name)
            result = analyze_function(
                lambda_client, cw_client, func, args.days, pricing,
                workload_type=args.workload_type,
                memory_override=args.memory_per_exec,
            )
            if result is None:
                continue
            if result.get("skipped"):
                skipped.append(result)
            else:
                candidates.append(result)

        progress.finish()
        candidates.sort(key=lambda x: x["lmi_score"], reverse=True)

        if is_json:
            all_results.extend(candidates + skipped)
        else:
            if not candidates and not skipped:
                print(f"\nNo LMI candidates found in {region}.")
            else:
                print_report(candidates, skipped, region, args.days, len(functions), pricing)

    if is_json:
        print(json.dumps(all_results, indent=2, default=str))


if __name__ == "__main__":
    main()
