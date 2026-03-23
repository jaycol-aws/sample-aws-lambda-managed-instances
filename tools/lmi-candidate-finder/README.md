# LMI Candidate Finder

Scans Lambda functions in your AWS account and identifies candidates for [AWS Lambda Managed Instances (LMI)](https://aws.amazon.com/lambda/lambda-managed-instances/) based on invocation patterns, duration, concurrency, memory, and runtime compatibility. Produces per-function savings estimates using live pricing from the AWS Pricing API.

## Table of Contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Methodology](#methodology)
- [Parameters](#parameters)
- [Workload Types](#workload-types)
- [Scoring](#scoring)
- [Disqualifiers](#disqualifiers)
- [Savings Estimate](#savings-estimate)
- [Memory Override](#memory-override)
- [Examples](#examples)
- [Testing](#testing)
- [Architecture](#architecture)
- [Requirements](#requirements)

## Quick Start

```bash
# Install dependency
pip install boto3

# Scan all functions in a region
python lmi_candidate_finder.py --region us-east-1

# Analyze a single function with known memory usage
python lmi_candidate_finder.py --region us-east-1 --function my-api \
    --memory-per-exec 200 --workload-type balanced
```

## How It Works

The tool supports two modes designed for a two-step workflow:

### Step 1: Scan — Identify candidates across your account

```bash
python lmi_candidate_finder.py --region us-east-1
```

Scan mode lists all Lambda functions, filters out unsuitable ones (wrong runtime, low concurrency, irregular traffic), and ranks the rest by LMI candidacy score. Use this to get a broad view of which functions are worth investigating. The default `io-heavy` workload type and configured memory are used for initial estimates — good enough for triage, not for final decisions.

### Step 2: Function — Deep dive into specific candidates

```bash
python lmi_candidate_finder.py --region us-east-1 --function payment-api \
    --memory-per-exec 256 --workload-type balanced
```

Once you've identified promising candidates from the scan, analyze them individually with accurate inputs. Provide the actual memory your function uses at runtime (`--memory-per-exec`) and the correct workload type (`--workload-type`) for that specific function. This produces a precise capacity plan and savings estimate you can use for planning.

### Under the hood

For each function, the tool:

1. **Filter** — Skip functions with unsupported runtimes, insufficient invocation volume, low/no concurrency, or irregular traffic patterns
2. **Score** — Assign a 0–100 candidacy score based on invocation volume, duration, concurrency, memory, provisioned concurrency, and throttle history
3. **Estimate** — Run the LMI capacity formula (same as the [Pricing Calculator](https://aws-samples.github.io/sample-aws-lambda-managed-instances/)) to determine instance count, packing efficiency, and cost comparison across four pricing tiers
4. **Report** — Display ranked candidates with savings estimates, or output as JSON for programmatic use

Pricing is fetched live from the [AWS Pricing API](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html) for the selected region — no hardcoded prices.

## Methodology

### Core hypothesis

Lambda Managed Instances trade Lambda's pay-per-invocation model for dedicated EC2 capacity with a 15% management fee. This is economically favorable when a function's usage pattern looks more like a server than a serverless spike — sustained concurrency, long durations, and predictable traffic. The candidate finder tests each function against this hypothesis using CloudWatch metrics.

The readiness calculation answers one question: **"Does this function's observed behavior match the cost profile where LMI wins?"**

### Decision model

The tool applies a three-stage funnel — disqualify, score, validate — so that only functions with genuine LMI potential reach the savings estimate (which is the most expensive step, requiring live pricing API calls and capacity formula execution).

```
All functions
  │
  ▼
Stage 1: DISQUALIFY — hard gates (binary pass/fail)
  │  Remove functions that fundamentally don't fit LMI's always-on model.
  │  Any single disqualifier eliminates the function.
  │
  ▼
Stage 2: SCORE — weighted signal aggregation (0–100)
  │  Combine multiple usage signals into a composite readiness score.
  │  Higher scores mean stronger alignment with LMI's cost model.
  │
  ▼
Stage 3: VALIDATE — capacity formula + live pricing
     Run the full LMI capacity plan and compare cost across pricing tiers.
     A high score with negative savings means the score was directionally
     right but the numbers don't work at current volume.
```

### Stage 1: Disqualification rules

These are binary gates. A function that fails any one is skipped entirely — no score, no savings estimate. The rationale is that these conditions make LMI structurally uneconomical regardless of other signals.

| Rule | Threshold | Why it's a hard gate |
|------|-----------|----------------------|
| Peak concurrency < 2 | Unless function has provisioned concurrency | LMI's value comes from packing multiple concurrent invocations onto shared vCPUs. A function that never runs concurrently gets no packing benefit — you'd pay for 3 instances (AZ minimum) to run one invocation at a time. |
| Traffic regularity < 25% of hours | Active hours / total hours over analysis period | LMI instances run 24/7. A function active only 6 hours/day pays for 24 hours of compute. Standard Lambda's scale-to-zero model wins for bursty or periodic workloads. |
| Unsupported runtime | Not Python, Node.js, Java, or .NET | LMI only supports these runtime families. Functions on Go, Ruby, custom runtimes, or container images can't use LMI. |
| Projected volume < 100K/month | 10% of the minimum scoring threshold | Too few invocations to justify analysis overhead. Even if other signals are strong, the absolute dollar amounts are negligible. |

**Provisioned concurrency exception:** Functions with provisioned concurrency bypass the low-concurrency gate because they're already paying for warm, always-on capacity — exactly the model LMI replaces, often at lower cost.

### Stage 2: Scoring rules

The score is an additive composite of six independent signals. Each signal contributes points based on threshold bands. The weights reflect how strongly each signal predicts LMI cost advantage based on the capacity formula economics.

| Signal | Max points | Weight rationale |
|--------|-----------|------------------|
| Monthly invocations | 30 | Highest weight. Volume is the primary driver — LMI's fixed instance cost amortizes over invocations. More invocations = lower per-invocation cost. |
| Average duration | 25 | Second highest. Longer durations mean more GB-seconds billed under Standard Lambda. LMI doesn't charge per GB-second, so long-running functions see the biggest delta. |
| Peak concurrency | 20 | Determines how many environments (and instances) are needed. Higher concurrency means better instance utilization through packing. |
| Provisioned concurrency | 15 | Direct replacement signal. If you're already paying for warm capacity, LMI is a like-for-like alternative with potentially better economics. |
| Memory allocation | 10 | Higher memory functions cost more per GB-second on Standard Lambda. LMI's per-instance pricing doesn't scale linearly with function memory. |
| Throttle history | 5 | Weak signal, but throttles indicate demand exceeding capacity — LMI's dedicated instances eliminate this. |
| Runtime upgrade needed | -5 | Penalty. If the function requires a runtime upgrade before LMI adoption, that's friction that reduces readiness. |

**Why these weights:** The top three signals (volume, duration, concurrency) account for 75 of 100 possible points because they directly determine the Standard Lambda bill and the LMI capacity plan. The remaining signals are adoption indicators — they suggest readiness but don't drive the cost math as strongly.

**Score interpretation:** The score predicts *likelihood of cost savings*, not *magnitude*. A score of 80 means the function's usage pattern strongly matches LMI's sweet spot. The actual dollar savings depend on the pricing tier, instance selection, and workload type — that's what Stage 3 calculates.

### Stage 3: Savings validation

The score is a heuristic. The savings estimate is the ground truth. Stage 3 runs the full [LMI capacity formula](https://aws-samples.github.io/sample-aws-lambda-managed-instances/) with live pricing to produce an actual dollar comparison.

Key assumptions in the capacity formula:

| Assumption | Value | Source |
|------------|-------|--------|
| OS overhead per instance | 1 vCPU + 1 GB memory | AWS LMI documentation |
| Minimum instances | 3 (AZ resiliency) | AWS LMI documentation |
| Minimum function memory | 2,048 MB | AWS LMI documentation |
| Management fee | 15% of EC2 On-Demand price | AWS LMI pricing |
| Concurrency per vCPU | min(runtime limit, memory fit, CPU sustainability) | Capacity formula — varies by workload type |
| Standard Lambda free tier | 1M requests + 400K GB-seconds/month | AWS Lambda pricing |

**When score and savings disagree:** A high score with negative savings (LMI costs more) typically means the function has the right *pattern* but not enough *volume* to overcome LMI's fixed cost floor (3 instances × 730 hours). The report flags this explicitly. Conversely, a moderate score with strong savings usually means one signal (like very high volume) dominates the economics even though other signals are weak.

### Limitations and known conservatisms

- **Workload type is uniform in scan mode.** All functions get the same CPU profile. Mixed-workload accounts should re-analyze strong candidates individually with the correct `--workload-type`.
- **Memory per execution defaults to configured MemorySize.** This overstates actual usage for most functions, leading to conservative (fewer environments per instance) capacity plans. Use `--memory-per-exec` with actual runtime memory for accurate estimates.
- **No cold start modeling.** The savings estimate doesn't account for cold start elimination, which is a non-financial benefit of LMI that may justify adoption even at cost parity.
- **Traffic assumed steady.** The capacity plan sizes for peak concurrency 24/7. Functions with predictable peak/off-peak patterns could use fewer instances during off-peak, but the tool doesn't model time-of-day scaling.
- **Free tier applied to Standard Lambda only.** This slightly favors Standard Lambda in the comparison for low-volume functions near the free tier boundary.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--region` | `us-east-1` | AWS region(s), comma-separated for multi-region scan |
| `--function` | *(scan all)* | Analyze a single function by name instead of scanning all |
| `--memory-per-exec` | *(configured MemorySize)* | Actual memory used per execution in MB (see [Memory Override](#memory-override)) |
| `--workload-type` | `io-heavy` | CPU profile: `io-heavy`, `balanced`, or `cpu-heavy` (see [Workload Types](#workload-types)) |
| `--days` | `14` | Days of CloudWatch history to analyze |
| `--min-invocations` | `1,000,000` | Minimum projected monthly invocations to consider a function |
| `--profile` | *(default)* | AWS CLI named profile |
| `--json` | `false` | Output results as JSON (suppresses progress output) |

## Workload Types

The workload type determines how many concurrent invocations can sustainably run per vCPU. This directly affects the LMI capacity formula — choosing the wrong type will over- or under-estimate instance requirements.

| Type | CPU per Invocation | Max Concurrency per vCPU | Best For |
|------|-------------------|--------------------------|----------|
| `io-heavy` | 12.5% | 8 | API proxies, queue consumers, database queries, HTTP calls |
| `balanced` | 25% | 4 | Mixed IO and compute, web backends, light data transforms |
| `cpu-heavy` | 50% | 2 | Data processing, ML inference, image/video encoding, crypto |

The concurrency per vCPU is further capped by the runtime limit (Python: 16, Node.js: 64, Java: 32, .NET: 32) and the memory-fitting limit (how many invocations fit in one vCPU's memory budget).

**How to choose:** If your function spends most of its time waiting for network/IO responses, use `io-heavy`. If it's doing sustained computation (loops, math, encoding), use `cpu-heavy`. When in doubt, `balanced` is a safe middle ground.

**Note:** In scan-all mode, the workload type applies uniformly to every function. For accounts with mixed workloads (e.g., IO-heavy APIs and CPU-heavy processors), analyze high-value functions individually with the `--function` flag and appropriate `--workload-type`.

## Scoring

Each function receives a 0–100 candidacy score:

| Signal | Points | Criteria |
|--------|--------|----------|
| Monthly invocations | 0 / 15 / 30 | < 1M / 1M–10M / ≥ 10M |
| Average duration | 0 / 12 / 25 | < 100ms / 100ms–1s / ≥ 1s |
| Peak concurrency | 0 / 10 / 20 | < 5 / 5–50 / ≥ 50 |
| Memory allocation | 0 / 5 / 10 | < 256 MB / 256–512 MB / ≥ 512 MB |
| Provisioned concurrency | 0 / 15 | No / Yes (LMI replaces this) |
| Throttle history | 0 / 5 | No throttles / Has throttles |
| Runtime upgrade needed | 0 / -5 | Ready / Needs upgrade |

**Rating thresholds:**

| Rating | Score | Meaning |
|--------|-------|---------|
| 🟢 STRONG | 60+ | High-confidence LMI candidate — run the Pricing Calculator for detailed planning |
| 🟡 MODERATE | 35–59 | Worth evaluating — may benefit from LMI depending on workload type and commitment |
| 🟠 WEAK | 15–34 | Marginal benefit — review case-by-case |
| 🔴 NOT RECOMMENDED | < 15 | Standard Lambda is likely more cost-effective |

## Disqualifiers

Functions are automatically skipped (not scored) if they meet any of these criteria:

| Condition | Threshold | Rationale |
|-----------|-----------|-----------|
| Low/no concurrency | Peak concurrent executions < 2 | LMI's multi-concurrency model requires sustained parallel invocations |
| Irregular traffic | Active in < 25% of hours over the analysis period | LMI doesn't scale to zero — you pay for instances 24/7 |
| Unsupported runtime | Not Python, Node.js, Java, or .NET | LMI only supports these runtime families |
| Insufficient volume | < 100K projected monthly invocations | Too low to justify analysis |

**Exception:** Functions with provisioned concurrency bypass the low-concurrency disqualifier, since they're already paying for warm capacity that LMI can replace.

## Savings Estimate

For each candidate, the tool runs the full LMI capacity formula:

1. Calculate sustainable concurrency per vCPU (based on runtime, memory, and workload type)
2. Determine function memory allocation (minimum 2,048 MB for LMI)
3. Calculate environments needed for peak concurrency
4. Pack environments onto instances (accounting for 1 vCPU + 1 GB OS overhead)
5. Enforce minimum 3 instances for AZ resiliency
6. Select the cheapest instance type from c7g, m7g, and r7g families

The estimate compares Standard Lambda vs LMI across four pricing tiers:

| Tier | Source | Applies To |
|------|--------|------------|
| On-Demand | AWS Pricing API | Baseline comparison |
| Compute Savings Plan (1yr No Upfront) | AWS Savings Plans API | EC2 + Lambda compute |
| EC2 Instance Savings Plan (1yr No Upfront) | AWS Savings Plans API | EC2 only |
| Reserved Instances (3yr All Upfront Standard) | AWS Pricing API (Reserved terms) | EC2 only |

Discount percentages are fetched live per instance type and region — not hardcoded. The tier labels in the output show the actual discount percentage (e.g., "Compute SP 1yr (EC2 -28.3%, Λ -11.8%)").

**Notes:**
- The 15% LMI management fee is always calculated on the On-Demand EC2 price, regardless of discounts
- Standard Lambda cost subtracts the free tier (1M requests + 400K GB-seconds/month)
- Lambda Compute SP rate is fetched from the Savings Plans API for the selected region

## Memory Override

By default, the tool uses the function's configured `MemorySize` for capacity planning. This is the *allocated ceiling*, not actual runtime usage — a function configured at 1024 MB may only use 200 MB per invocation.

Using the actual memory improves estimate accuracy because:
- Lower memory per execution → more invocations fit per vCPU → fewer LMI instances needed
- The function memory allocation (min 2,048 MB) is calculated from `memory_per_exec × concurrency_per_vcpu`

```bash
# Find actual memory usage from CloudWatch Logs
# Look for "Max Memory Used" in REPORT lines
aws logs filter-log-events --log-group-name /aws/lambda/my-func \
    --filter-pattern "REPORT" --limit 10 \
    --query 'events[].message' --output text | grep -o 'Max Memory Used: [0-9]*'

# Use the actual value
python lmi_candidate_finder.py --region us-east-1 --function my-func --memory-per-exec 200
```

## Examples

### Scan all functions in a region
```bash
python lmi_candidate_finder.py --region us-east-1
```

### Multi-region scan
```bash
python lmi_candidate_finder.py --region us-east-1,us-west-2,eu-west-1
```

### Analyze a specific API gateway backend
```bash
python lmi_candidate_finder.py --region us-east-1 \
    --function payment-api \
    --memory-per-exec 256 \
    --workload-type io-heavy
```

### Analyze a CPU-intensive data processor
```bash
python lmi_candidate_finder.py --region us-east-1 \
    --function etl-processor \
    --memory-per-exec 512 \
    --workload-type cpu-heavy
```

### JSON output for CI/CD or dashboards
```bash
python lmi_candidate_finder.py --region us-east-1 --json | \
    jq '[.[] | select(.skipped != true and .lmi_score >= 60)]'
```

### Lower the volume threshold for dev/test accounts
```bash
python lmi_candidate_finder.py --region us-east-1 --min-invocations 100000
```

## Testing

### Unit tests (no AWS credentials required)

The test suite validates scoring, disqualifiers, capacity formula, workload types, and memory override using mock CloudWatch data:

```bash
python test_lmi_candidate_finder.py
```

Test scenarios:
| Test | Scenario | Expected |
|------|----------|----------|
| 1 | High-volume Java API, 50M inv/month, 2s duration, 100 concurrency, provisioned | STRONG (score 100), RI tier cheaper than OD |
| 2 | Python 3.12, 2M inv/month, 150ms duration, 8 concurrency, x86_64 | MODERATE (score ~37), x86 instance selected |
| 3 | High volume but peak concurrency = 1 | Skipped (low concurrency) |
| 4 | High volume + concurrency but only 10% hours active | Skipped (irregular traffic) |
| 5 | Ruby, Go, custom runtimes | Filtered (returns None) |
| 6 | Capacity formula cross-check against lmi_calculator.py | Exact match |
| 7 | Same function as io-heavy vs cpu-heavy | Same score, different cost |
| 8 | Memory override (2048 configured, 200 actual) | Better packing with override |
| 9 | Low concurrency + provisioned concurrency | Not skipped (bypass) |
| 10 | 1M inv/month within free tier | Standard Lambda ≈ $0 |
| 11 | days=30 (no extrapolation) | Monthly = raw total |
| 12 | Empty EC2 pricing | savings = None (graceful) |
| 13 | Upgradeable runtime, minimal signals | Score floored at 0 |

### Integration test with a real Lambda function

```bash
cd test-function
sam build && sam deploy --guided

# Generate traffic (300 invocations, 15 concurrent)
cd ..
./load-test.sh 300 15

# Wait 2-3 min for CloudWatch metrics, then scan
python lmi_candidate_finder.py --region us-east-1 --days 1 --min-invocations 1000

# Clean up
aws cloudformation delete-stack --stack-name lmi-candidate-test
```

## Architecture

```
lmi-candidate-finder/
├── lmi_candidate_finder.py          # Main tool (single file, no dependencies beyond boto3)
├── test_lmi_candidate_finder.py     # Unit tests with mock data (49 checks)
├── load-test.sh                     # Bash script for concurrent Lambda invocations
├── README.md                        # This file
└── test-function/
    ├── app.py                       # CPU-bound test Lambda (SHA-256 hash chaining)
    ├── template.yaml                # SAM template (arm64, Python 3.13, 512 MB)
    └── .gitignore                   # Excludes .aws-sam/ build artifacts
```

### Data flow

```
AWS Pricing API ──→ EC2 + Lambda prices (per region)
                         │
Lambda ListFunctions ──→ Function configs (runtime, memory, arch)
                         │
CloudWatch Metrics ────→ Invocations, Duration, Concurrency, Throttles
                         │
                    ┌────▼────┐
                    │ Filter  │ → Skip: unsupported runtime, low volume,
                    │         │        low concurrency, irregular traffic
                    └────┬────┘
                    ┌────▼────┐
                    │  Score  │ → 0-100 based on volume, duration,
                    │         │   concurrency, memory, provisioned, throttles
                    └────┬────┘
                    ┌────▼────┐
                    │Estimate │ → LMI capacity formula: instance type,
                    │         │   count, packing, cost across 4 tiers
                    └────┬────┘
                    ┌────▼────┐
                    │ Report  │ → Terminal output or JSON
                    └─────────┘
```

## Requirements

- Python 3.9+
- boto3 (`pip install boto3`)
- AWS credentials with permissions for:
  - `lambda:ListFunctions`, `lambda:GetFunction`, `lambda:ListProvisionedConcurrencyConfigs`
  - `cloudwatch:GetMetricStatistics`
  - `pricing:GetProducts` (read-only, fetches public pricing data)
  - `savingsplans:DescribeSavingsPlansOfferingRates` (read-only, fetches SP rates)

## Supported Regions

Pricing lookup supports: us-east-1, us-east-2, us-west-1, us-west-2, eu-west-1, eu-west-2, eu-west-3, eu-central-1, eu-north-1, ap-northeast-1, ap-southeast-1, ap-southeast-2, ap-south-1, sa-east-1, ca-central-1.

LMI is currently available in: us-east-1, us-east-2, us-west-2, ap-northeast-1, eu-west-1. The tool can scan functions in any region, but LMI deployment is limited to these regions.

## License

This tool is part of the [sample-aws-lambda-managed-instances](https://github.com/aws-samples/sample-aws-lambda-managed-instances) repository, licensed under MIT-0.
