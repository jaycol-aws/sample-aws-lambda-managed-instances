# LMI Candidate Finder

Scans Lambda functions in your AWS account and identifies candidates for [Lambda Managed Instances](https://aws.amazon.com/lambda/lambda-managed-instances/) based on invocation patterns, duration, memory, and runtime compatibility. Produces per-function savings estimates across multiple pricing tiers.

## Quick Start

```bash
# Scan your account
python lmi_candidate_finder.py --region us-east-1

# Use a specific profile
python lmi_candidate_finder.py --region us-east-1 --profile my-profile

# Analyze last 7 days with lower threshold
python lmi_candidate_finder.py --region us-east-1 --days 7 --min-invocations 100000

# JSON output for programmatic use
python lmi_candidate_finder.py --region us-east-1 --json
```

## What It Checks

| Signal | Weight | Why |
|--------|--------|-----|
| Monthly invocations | 30 pts | LMI shines at high volume (no per-duration charge) |
| Average duration | 25 pts | Longer durations = more savings vs per-GB-second pricing |
| Peak concurrency | 20 pts | Multi-concurrency per environment is LMI's key advantage |
| Provisioned concurrency | 15 pts | Already paying for warm capacity — LMI replaces this |
| Memory allocation | 10 pts | Higher memory = higher per-invocation cost on standard Lambda |
| Throttle history | 5 pts | Throttling suggests capacity constraints LMI can address |
| Runtime compatibility | -5 pts | Penalty if runtime upgrade needed |

## Scoring

- 🟢 **STRONG** (60+): High-confidence LMI candidate
- 🟡 **MODERATE** (35-59): Worth evaluating with the cost calculator
- 🟠 **WEAK** (15-34): Marginal benefit, review case-by-case
- 🔴 **NOT RECOMMENDED** (<15): Standard Lambda is likely better

## Savings Estimate

For each candidate, the finder runs the LMI capacity formula to determine:
- Best instance type (from c7g, m7g, r7g families)
- Instance count, environments per instance, concurrency per environment
- Cost comparison across four pricing tiers:

| Tier | LMI Discount | Lambda Discount |
|------|-------------|-----------------|
| On-Demand | 0% | 0% |
| Compute Savings Plan (1yr) | 50% | 17% |
| EC2 Instance Savings Plan | 72% | 0% |
| Reserved Instances (3yr) | 75% | 0% |

The estimate uses the same capacity formula as the [LMI Pricing Calculator](https://aws-samples.github.io/sample-aws-lambda-managed-instances/), including AZ resiliency minimums and instance packing efficiency.

**Note:** These are estimates based on CloudWatch averages. Actual savings depend on traffic patterns, memory profiling, and workload characteristics. Use the Pricing Calculator for detailed capacity planning.

## Supported Runtimes

LMI requires: Python 3.13+, Node.js 22+, Java 21+, .NET 8+

The finder also flags functions on older runtimes that could be upgraded.

## Testing

Deploy the included test function and load test it:

```bash
cd test-function
sam build && sam deploy --guided

# Generate traffic
cd ..
./load-test.sh 500 10

# Wait 2-3 min for CloudWatch, then scan
python lmi_candidate_finder.py --region us-east-1 --days 1 --min-invocations 1000
```

## Requirements

- Python 3.9+
- boto3 (`pip install boto3`)
- AWS credentials with `lambda:ListFunctions`, `lambda:ListProvisionedConcurrencyConfigs`, `cloudwatch:GetMetricStatistics` permissions

## Known Limitations

- **Memory estimate uses configured, not actual usage.** Lambda's `MemorySize` is the allocated ceiling, not runtime consumption. A function configured at 512 MB may only use 100 MB, which would improve LMI packing efficiency. For accurate estimates, profile actual memory usage with Lambda Insights.
- **No workload type inference.** The capacity formula assumes IO-heavy workloads (highest concurrency per vCPU). CPU-bound functions will achieve lower concurrency in practice. Use the Pricing Calculator to model specific workload types.
- **Pricing is us-east-1 only.** EC2 and Lambda prices vary by region. Estimates for other regions may differ.
- **Single region per run.** Run the tool once per region, or script a multi-region wrapper.
- **API call volume scales linearly.** ~5 API calls per function. Accounts with 1000+ functions may take several minutes.
