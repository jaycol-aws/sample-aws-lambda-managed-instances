# LMI Candidate Finder

Scans Lambda functions in your AWS account and identifies candidates for [Lambda Managed Instances](https://aws.amazon.com/lambda/lambda-managed-instances/) based on invocation patterns, duration, concurrency, memory, and runtime compatibility. Produces per-function savings estimates using live pricing from the AWS Pricing API.

## Quick Start

```bash
# Scan all functions in a region
python lmi_candidate_finder.py --region us-east-1

# Scan multiple regions
python lmi_candidate_finder.py --region us-east-1,us-west-2,eu-west-1

# Analyze a single function with known memory usage
python lmi_candidate_finder.py --region us-east-1 --function my-api \
    --memory-per-exec 200 --workload-type balanced

# CPU-heavy Java function
python lmi_candidate_finder.py --region us-east-1 --function my-processor \
    --memory-per-exec 512 --workload-type cpu-heavy

# JSON output for programmatic use
python lmi_candidate_finder.py --region us-east-1 --json
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--region` | us-east-1 | AWS region(s), comma-separated for multi-region scan |
| `--function` | *(all)* | Analyze a single function by name |
| `--memory-per-exec` | *(configured)* | Actual memory used per execution in MB |
| `--workload-type` | io-heavy | CPU profile: `io-heavy`, `balanced`, or `cpu-heavy` |
| `--days` | 14 | Days of CloudWatch data to analyze |
| `--min-invocations` | 1,000,000 | Minimum monthly invocations to consider |
| `--profile` | *(default)* | AWS CLI profile name |
| `--json` | false | Output as JSON |

## Workload Types

The workload type determines how many concurrent invocations can sustainably run per vCPU:

| Type | CPU per Invocation | Concurrency per vCPU | Best For |
|------|-------------------|---------------------|----------|
| `io-heavy` | 12.5% | Up to 8 | API proxies, queue consumers, DB queries |
| `balanced` | 25% | Up to 4 | Mixed IO and compute, web backends |
| `cpu-heavy` | 50% | Up to 2 | Data processing, ML inference, image/video encoding |

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

## Disqualifiers

Functions are automatically skipped (not scored) if they have:
- **Low/no concurrency** — peak concurrent executions < 2 (LMI needs sustained concurrent load)
- **Irregular traffic** — active in fewer than 25% of hours in the analysis period (LMI doesn't scale to zero)

## Scoring

- 🟢 **STRONG** (60+): High-confidence LMI candidate
- 🟡 **MODERATE** (35-59): Worth evaluating with the cost calculator
- 🟠 **WEAK** (15-34): Marginal benefit, review case-by-case
- 🔴 **NOT RECOMMENDED** (<15): Standard Lambda is likely better

## Savings Estimate

For each candidate, the finder runs the LMI capacity formula to determine:
- Best instance type (from c7g, m7g, r7g families)
- Instance count, environments per instance, concurrency per environment
- Cost comparison across four pricing tiers (On-Demand, Compute SP, EC2 SP, 3yr RI)

Pricing is fetched live from the AWS Pricing API for the selected region.

## Memory Override

By default, the tool uses the function's configured `MemorySize` for capacity planning. This is the *allocated ceiling*, not actual runtime usage. For more accurate estimates, provide the actual memory your function uses:

```bash
# If your function is configured at 1024 MB but only uses ~300 MB
python lmi_candidate_finder.py --region us-east-1 --function my-func --memory-per-exec 300
```

You can find actual memory usage in CloudWatch Logs (`REPORT` lines show `Max Memory Used`).

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
- AWS credentials with permissions for:
  - `lambda:ListFunctions`, `lambda:GetFunction`, `lambda:ListProvisionedConcurrencyConfigs`
  - `cloudwatch:GetMetricStatistics`
  - `pricing:GetProducts` (read-only, fetches public pricing data)
