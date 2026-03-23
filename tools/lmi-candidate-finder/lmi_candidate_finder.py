#!/usr/bin/env python3
"""
LMI Candidate Finder — Scan Lambda functions and identify candidates for
Lambda Managed Instances based on invocation patterns, duration, memory,
and runtime compatibility. Includes savings estimates using the LMI
capacity formula with live pricing from the AWS Pricing and Savings Plans APIs.

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

# Lambda free tier (monthly)
LAMBDA_FREE_REQUESTS = 1_000_000
LAMBDA_FREE_GB_SECONDS = 400_000

INSTANCE_SPECS = {
    "c7g.xlarge":  {"vcpus": 4,  "memory_gb": 8,   "ratio": 2},
    "c7g.2xlarge": {"vcpus": 8,  "memory_gb": 16,  "ratio": 2},
    "m7g.xlarge":  {"vcpus": 4,  "memory_gb": 16,  "ratio": 4},
    "m7g.2xlarge": {"vcpus": 8,  "memory_gb": 32,  "ratio": 4},
    "r7g.xlarge":  {"vcpus": 4,  "memory_gb": 32,  "ratio": 8},
    "c7i.xlarge":  {"vcpus": 4,  "memory_gb": 8,   "ratio": 2},
    "c7i.2xlarge": {"vcpus": 8,  "memory_gb": 16,  "ratio": 2},
    "m7i.xlarge":  {"vcpus": 4,  "memory_gb": 16,  "ratio": 4},
    "m7i.2xlarge": {"vcpus": 8,  "memory_gb": 32,  "ratio": 4},
}

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
    "min_traffic_hours_pct": 0.25,
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
# Dynamic pricing — EC2 OD, RI, SP, and Lambda
# ---------------------------------------------------------------------------

def fetch_ec2_od_price(pricing_client, instance_type, location):
    """Fetch On-Demand hourly price for an EC2 instance type."""
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
            # Also extract RI 3yr All Upfront Standard effective hourly
            ri_eff = _extract_ri_rate(product, "3yr", "All Upfront", "standard")
            od = None
            for term in product["terms"]["OnDemand"].values():
                for dim in term["priceDimensions"].values():
                    od = float(dim["pricePerUnit"]["USD"])
            return od, ri_eff
    except (ClientError, KeyError, ValueError):
        pass
    return None, None


def _extract_ri_rate(product, lease_match, purchase_match, class_match):
    """Extract effective hourly RI rate from a Pricing API product."""
    for offer in product["terms"].get("Reserved", {}).values():
        attrs = offer["termAttributes"]
        if (lease_match in attrs.get("LeaseContractLength", "") and
                attrs.get("PurchaseOption", "") == purchase_match and
                attrs.get("OfferingClass", "") == class_match):
            hourly = upfront = 0
            for dim in offer["priceDimensions"].values():
                p = float(dim["pricePerUnit"].get("USD", "0"))
                if "Upfront Fee" in dim["description"]:
                    upfront = p
                else:
                    hourly = p
            months = 12 if "1yr" in lease_match else 36
            return hourly + (upfront / (months * HOURS_PER_MONTH))
    return None


def fetch_ec2_sp_rates(sp_client, instance_type, region):
    """Fetch Compute SP and EC2 Instance SP hourly rates for an instance type."""
    rates = {}
    for sp_type, key in [("Compute", "compute_sp_1yr"), ("EC2Instance", "ec2_sp_1yr")]:
        try:
            resp = sp_client.describe_savings_plans_offering_rates(
                savingsPlanPaymentOptions=["No Upfront"],
                savingsPlanTypes=[sp_type],
                products=["EC2"],
                serviceCodes=["AmazonEC2"],
                filters=[
                    {"name": "region", "values": [region]},
                    {"name": "instanceType", "values": [instance_type]},
                    {"name": "tenancy", "values": ["shared"]},
                    {"name": "productDescription", "values": ["Linux/UNIX"]},
                ],
                maxResults=10,
            )
            for rate in resp.get("searchResults", []):
                if ("BoxUsage" in rate["usageType"] and
                        rate["savingsPlanOffering"]["durationSeconds"] == 31536000):
                    rates[key] = float(rate["rate"])
        except (ClientError, KeyError, ValueError):
            pass
    return rates


def fetch_lambda_pricing(pricing_client, sp_client, location, region):
    """Fetch Lambda OD pricing and Compute SP rate."""
    prices = {"gbsec_x86": None, "gbsec_arm": None, "request": None, "sp_gbsec": None}

    # On-Demand from Pricing API
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

    # Lambda Compute SP rate from Savings Plans API
    try:
        resp = sp_client.describe_savings_plans_offering_rates(
            savingsPlanPaymentOptions=["No Upfront"],
            savingsPlanTypes=["Compute"],
            products=["Lambda"],
            serviceCodes=["AWSLambda"],
            usageTypes=["Lambda-GB-Second"],
            filters=[{"name": "region", "values": [region]}],
            maxResults=5,
        )
        for rate in resp.get("searchResults", []):
            if rate["savingsPlanOffering"]["durationSeconds"] == 31536000:
                prices["sp_gbsec"] = float(rate["rate"])
    except (ClientError, KeyError, ValueError):
        pass

    return prices


def load_pricing(session, region):
    """Load all pricing for a region: EC2 OD/SP/RI + Lambda OD/SP."""
    location = REGION_NAMES.get(region)
    if not location:
        return None

    pricing_client = session.client("pricing", region_name="us-east-1")
    sp_client = session.client("savingsplans", region_name="us-east-1")

    ec2 = {}
    for itype in INSTANCE_SPECS:
        od, ri = fetch_ec2_od_price(pricing_client, itype, location)
        if od:
            sp_rates = fetch_ec2_sp_rates(sp_client, itype, region)
            ec2[itype] = {"od": od, "ri_3yr": ri, **sp_rates}

    lambda_prices = fetch_lambda_pricing(pricing_client, sp_client, location, region)

    if not ec2 or not lambda_prices["gbsec_x86"]:
        return None

    return {"ec2": ec2, "lambda": lambda_prices, "region": region, "location": location}


# ---------------------------------------------------------------------------
# LMI capacity formula
# ---------------------------------------------------------------------------

def lmi_capacity_plan(runtime_key, memory_per_exec_mb, peak_concurrency,
                      instance_type, workload_type="io-heavy"):
    """Run the LMI capacity formula with workload-type-aware concurrency."""
    spec = INSTANCE_SPECS[instance_type]
    mem_vcpu_ratio = spec["ratio"]

    runtime_limit = RUNTIME_MAX_CONCURRENCY.get(runtime_key, 16)
    memory_per_vcpu_mb = mem_vcpu_ratio * 1024
    memory_limit = int(memory_per_vcpu_mb / memory_per_exec_mb) if memory_per_exec_mb > 0 else runtime_limit
    cpu_pct = WORKLOAD_TYPES[workload_type]["cpu_pct"]
    sustainable_limit = int(100 / cpu_pct)
    conc_per_vcpu = max(1, min(runtime_limit, memory_limit, sustainable_limit))

    function_memory_mb = max(MIN_FUNCTION_MEMORY_MB, math.ceil(memory_per_exec_mb * conc_per_vcpu))
    vcpus_per_env = max(1, function_memory_mb // (mem_vcpu_ratio * 1024))
    conc_per_env = conc_per_vcpu * vcpus_per_env

    target = max(1, int(peak_concurrency))
    envs_needed = math.ceil(target / conc_per_env)

    usable_vcpus = spec["vcpus"] - OVERHEAD_VCPUS
    usable_memory_mb = (spec["memory_gb"] * 1024) - OVERHEAD_MEMORY_MB
    by_vcpu = usable_vcpus // vcpus_per_env if vcpus_per_env > 0 else 0
    by_memory = usable_memory_mb // function_memory_mb if function_memory_mb > 0 else 0
    envs_per_instance = max(1, min(by_vcpu, by_memory))

    instances = max(3, math.ceil(envs_needed / envs_per_instance))

    return {
        "instances": instances, "envs_per_instance": envs_per_instance,
        "envs_needed": envs_needed, "conc_per_env": conc_per_env,
        "function_memory_mb": function_memory_mb, "workload_type": workload_type,
    }


def pick_best_instance(runtime_key, memory_per_exec_mb, peak_concurrency,
                       pricing, workload_type="io-heavy", arch="arm64"):
    """Try each instance type and pick the one with lowest on-demand LMI cost.
    Filters to Graviton (g) instances for arm64, Intel (i) for x86_64."""
    best = None
    for itype in INSTANCE_SPECS:
        if itype not in pricing["ec2"]:
            continue
        is_graviton = "g." in itype
        if arch == "arm64" and not is_graviton:
            continue
        if arch == "x86_64" and is_graviton:
            continue
        plan = lmi_capacity_plan(runtime_key, memory_per_exec_mb, peak_concurrency,
                                 itype, workload_type)
        od = pricing["ec2"][itype]["od"]
        monthly_ec2 = plan["instances"] * od * HOURS_PER_MONTH
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
    """Full savings estimate using live pricing: OD, Compute SP, EC2 SP, RI 3yr."""
    lp = pricing["lambda"]
    gbsec_price = lp["gbsec_arm"] if arch == "arm64" else lp["gbsec_x86"]
    request_price = lp["request"]

    # Standard Lambda cost with free tier
    std_memory_gb = std_lambda_memory_mb / 1024
    raw_gb_seconds = monthly_invocations * avg_duration_sec * std_memory_gb
    billable_gb_seconds = max(0, raw_gb_seconds - LAMBDA_FREE_GB_SECONDS)
    billable_requests = max(0, monthly_invocations - LAMBDA_FREE_REQUESTS)
    std_compute_od = billable_gb_seconds * gbsec_price
    std_request_cost = billable_requests * request_price

    # Lambda Compute SP rate (applied to all GB-seconds, no free tier interaction)
    lambda_sp_rate = lp.get("sp_gbsec") or gbsec_price  # fallback to OD

    # LMI capacity plan
    best = pick_best_instance(runtime_key, memory_per_exec_mb, peak_concurrency,
                              pricing, workload_type, arch)
    if not best:
        return None
    itype = best["instance_type"]
    plan = best["plan"]
    ec2_info = pricing["ec2"][itype]
    ec2_od_monthly = plan["instances"] * ec2_info["od"] * HOURS_PER_MONTH
    mgmt_fee = ec2_od_monthly * LMI_MANAGEMENT_FEE
    lmi_request_cost = billable_requests * request_price

    # Build tiers with live rates
    tiers = {}

    # On-Demand
    std_od_total = std_compute_od + std_request_cost
    lmi_od_total = ec2_od_monthly + mgmt_fee + lmi_request_cost
    tiers["on_demand"] = _make_tier("On-Demand", std_od_total, lmi_od_total)

    # Compute SP 1yr — applies to both EC2 and Lambda
    csp_rate = ec2_info.get("compute_sp_1yr")
    if csp_rate:
        lmi_csp = plan["instances"] * csp_rate * HOURS_PER_MONTH + mgmt_fee + lmi_request_cost
        std_csp_compute = billable_gb_seconds * lambda_sp_rate
        std_csp = std_csp_compute + std_request_cost
        csp_ec2_disc = round((1 - csp_rate / ec2_info["od"]) * 100, 1)
        csp_lam_disc = round((1 - lambda_sp_rate / gbsec_price) * 100, 1) if lambda_sp_rate != gbsec_price else 0
        tiers["compute_sp"] = _make_tier(
            f"Compute SP 1yr (EC2 -{csp_ec2_disc}%, Λ -{csp_lam_disc}%)",
            std_csp, lmi_csp)

    # EC2 Instance SP 1yr — EC2 only, no Lambda discount
    eisp_rate = ec2_info.get("ec2_sp_1yr")
    if eisp_rate:
        lmi_eisp = plan["instances"] * eisp_rate * HOURS_PER_MONTH + mgmt_fee + lmi_request_cost
        eisp_disc = round((1 - eisp_rate / ec2_info["od"]) * 100, 1)
        tiers["ec2_sp"] = _make_tier(
            f"EC2 Instance SP 1yr (-{eisp_disc}%)",
            std_od_total, lmi_eisp)

    # RI 3yr All Upfront Standard
    ri_rate = ec2_info.get("ri_3yr")
    if ri_rate:
        lmi_ri = plan["instances"] * ri_rate * HOURS_PER_MONTH + mgmt_fee + lmi_request_cost
        ri_disc = round((1 - ri_rate / ec2_info["od"]) * 100, 1)
        tiers["reserved_3yr"] = _make_tier(
            f"RI 3yr All Upfront (-{ri_disc}%)",
            std_od_total, lmi_ri)

    return {
        "instance_type": itype,
        "instances": plan["instances"],
        "envs_per_instance": plan["envs_per_instance"],
        "function_memory_mb": plan["function_memory_mb"],
        "conc_per_env": plan["conc_per_env"],
        "workload_type": WORKLOAD_TYPES[workload_type]["label"],
        "tiers": tiers,
    }


def _make_tier(label, std_total, lmi_total):
    savings = std_total - lmi_total
    savings_pct = (savings / std_total * 100) if std_total > 0 else 0
    return {
        "label": label,
        "standard_lambda": round(std_total, 2),
        "lmi_cost": round(lmi_total, 2),
        "savings": round(savings, 2),
        "savings_pct": round(savings_pct, 1),
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

    # Fetch hourly invocations (used for both volume and regularity — 1 API call)
    hourly_points = get_metric_stats(cw_client, name, "Invocations", "Sum", days, period=3600)
    total_invocations = sum(dp["Sum"] for dp in hourly_points)
    monthly_invocations = total_invocations * (30 / days) if days != 30 else total_invocations

    if monthly_invocations < THRESHOLDS["min_monthly_invocations"] * 0.1:
        return None

    # Traffic regularity from the same hourly data
    total_hours = days * 24
    active_hours = len([p for p in hourly_points if p.get("Sum", 0) > 0])
    is_regular = (active_hours / total_hours) >= THRESHOLDS["min_traffic_hours_pct"] if total_hours > 0 else False

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

    # --- Disqualifiers ---
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
        "memory_mb": memory_mb, "memory_per_exec_mb": mem_per_exec,
        "architecture": arch, "timeout_sec": timeout,
        "monthly_invocations": monthly_invocations,
        "avg_duration_ms": avg_duration_ms,
        "peak_concurrency": peak_concurrency,
        "has_provisioned_concurrency": has_provisioned,
        "throttle_count": total_throttles,
        "active_hours": active_hours, "total_hours": total_hours,
        "lmi_score": max(0, min(score, 100)),
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
    print(f"  Pricing: {pricing['location']} (live from AWS Pricing + Savings Plans APIs)")
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
            print(f"   {'Plan':<40} {'Std Lambda':>12} {'LMI':>12} {'Savings':>12} {'%':>7}")
            print(f"   {'─' * 40} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 7}")
            for tier in s["tiers"].values():
                sav_str = f"${tier['savings']:,.2f}" if tier['savings'] >= 0 else f"-${abs(tier['savings']):,.2f}"
                pct_str = f"{tier['savings_pct']:+.0f}%" if tier['standard_lambda'] > 0 else "N/A"
                print(f"   {tier['label']:<40} ${tier['standard_lambda']:>10,.2f} ${tier['lmi_cost']:>10,.2f} {sav_str:>12} {pct_str:>7}")
            if all(t["savings"] < 0 for t in s["tiers"].values()):
                print(f"   ⚠️  LMI is more expensive at current traffic — revisit at higher volume")
        print()

    if skipped:
        print(f"  ⏭️  {len(skipped)} functions skipped (low/no concurrency or irregular traffic):")
        for s in skipped[:10]:
            reasons = "; ".join(s["skip_reasons"])
            print(f"     • {s['function_name']}: {reasons}")
        if len(skipped) > 10:
            print(f"     ... and {len(skipped) - 10} more")
        print()

    if strong:
        print(f"{'─' * 90}")
        print(f"  💡 STRONG CANDIDATES — PORTFOLIO SAVINGS SUMMARY (On-Demand)\n")
        total_std = sum(c["savings"]["tiers"]["on_demand"]["standard_lambda"] for c in strong)
        total_lmi = sum(c["savings"]["tiers"]["on_demand"]["lmi_cost"] for c in strong)
        for c in strong:
            od = c["savings"]["tiers"]["on_demand"]
            print(f"  {c['function_name'][:50]:<50} Std ${od['standard_lambda']:>10,.2f} → LMI ${od['lmi_cost']:>10,.2f}")
        print(f"  {'─' * 50} {'─' * 14}   {'─' * 14}")
        print(f"  {'TOTAL':<50} Std ${total_std:>10,.2f} → LMI ${total_lmi:>10,.2f}")
        if total_std > 0:
            print(f"  Potential savings: ${total_std - total_lmi:,.2f}/mo ({(total_std - total_lmi) / total_std * 100:.0f}%)")
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
  python lmi_candidate_finder.py --region us-east-1
  python lmi_candidate_finder.py --region us-east-1,us-west-2,eu-west-1
  python lmi_candidate_finder.py --region us-east-1 --function my-api \\
      --memory-per-exec 200 --workload-type balanced

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
                        help="Actual memory used per execution in MB")
    parser.add_argument("--workload-type", default="io-heavy",
                        choices=list(WORKLOAD_TYPES.keys()), dest="workload_type",
                        help="Workload CPU profile (default: io-heavy)")
    parser.add_argument("--min-invocations", type=int, default=None,
                        help="Override minimum monthly invocation threshold")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    thresholds = dict(THRESHOLDS)
    if args.min_invocations is not None:
        thresholds["min_monthly_invocations"] = args.min_invocations
        THRESHOLDS.update(thresholds)

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
        ec2_count = len(pricing["ec2"])
        sp_count = sum(1 for v in pricing["ec2"].values() if v.get("compute_sp_1yr"))
        log(f"   EC2: {ec2_count} instance types | SP rates: {sp_count} | Lambda: ✓")

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
