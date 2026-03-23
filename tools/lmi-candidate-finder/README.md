# LMI Candidate Finder

Scans Lambda functions in your AWS account and identifies candidates for [Lambda Managed Instances](https://aws.amazon.com/lambda/lambda-managed-instances/) based on invocation patterns, duration, memory, and runtime compatibility.

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
