# AWS LMI Scheduler

Schedule-driven cost optimisation for [AWS Lambda Managed Instances (LMI)](https://docs.aws.amazon.com/lambda/latest/dg/lambda-managed-instances.html). Automatically scales down capacity providers during idle periods (nights, weekends) and restores them on a defined schedule — eliminating unnecessary EC2 spend while preserving the LMI programming model.

## Problem

LMI runs Lambda functions on customer-owned EC2 instances via capacity providers. Unlike standard Lambda's pay-per-invocation model, these instances incur costs regardless of traffic. For workloads with predictable usage patterns, this results in significant waste during idle hours.

## How it works

The scheduler is an AWS SAM application with two Lambda functions:

- **Management API** — REST API (API Gateway HTTP API) for registering capacity providers, configuring schedules, and querying cost reports.
- **Executor** — Invoked by Amazon EventBridge Scheduler on a cron schedule. Performs the actual scale-down or scale-up using one of two strategies.

The scheduler **does not own** the capacity provider or function lifecycle — those remain managed by your existing IaC (CloudFormation, CDK, SAM). The scheduler operates exclusively on runtime scaling configuration via AWS APIs.

### Strategies

| Strategy | What it does | Trade-off |
| ---------- | ------------- | ----------- |
| **Shape** | Adjusts `MinExecutionEnvironments` and `MaxExecutionEnvironments` to operator-specified targets for every function on the capacity provider | Shapes capacity to match known traffic patterns (e.g. lower bounds overnight, higher during business hours). Works with LMI auto-scaling. Instances may remain running at reduced capacity. |
| **Pause** | Sets `MinExecutionEnvironments` and `MaxExecutionEnvironments` to 0 for every function on the capacity provider | Causes the CP to scale in all instances. Use when the workload is completely idle (nights, weekends). |

Both strategies snapshot the original values before modifying, and restore them exactly on scale-up. All operations are idempotent and safe for EventBridge Scheduler's at-least-once delivery.

## Architecture

```bash
┌─────────────┐     ┌──────────────────┐     ┌───────────────────┐
│  API Gateway │────▶│ Management Lambda │────▶│ DynamoDB (state)   │
└─────────────┘     └──────────────────┘     └───────────────────┘
                           │                         ▲
                           │ creates schedules       │ read/write
                           ▼                         │
                    ┌──────────────────┐     ┌───────────────────┐
                    │ EventBridge      │────▶│ Executor Lambda    │
                    │ Scheduler        │     └───────────────────┘
                    └──────────────────┘             │
                           │                         │ scale-down / scale-up
                           ▼                         ▼
                    ┌──────────────────┐     ┌───────────────────┐
                    │ SQS DLQ          │     │ Lambda CP APIs     │
                    │ (failed retries) │     │ CloudWatch metrics │
                    └──────────────────┘     └───────────────────┘
```

## Prerequisites

- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) >= 1.100.0
- Python 3.13+
- An existing LMI capacity provider with at least one associated function (or use the [included example stacks](docs/examples.md))

## Quick start

The root `Makefile` provides a generic, parameter-driven CLI for managing the scheduler and interacting with registered capacity providers.

### 1. Deploy the scheduler

```bash
git clone https://github.com/sliedig/aws-lmi-scheduler.git
cd aws-lmi-scheduler

make deploy-scheduler
```

This creates the `lmi-scheduler` CloudFormation stack containing the Management API, Executor Lambda, DynamoDB state table, and EventBridge schedule group.

### 2. Check outputs

```bash
make outputs
```

Prints the API endpoint and an export command for your shell:

```text
==========================================
  Scheduler Outputs
==========================================

  API Endpoint:  https://abc123.execute-api.ap-southeast-2.amazonaws.com/prod

  Export for shell:

    export API_ENDPOINT=https://abc123.execute-api.ap-southeast-2.amazonaws.com/prod
```

### 3. Register a capacity provider

```bash
# Register with shape strategy (default)
make register CP_NAME=my-capacity-provider

# Register with pause strategy
make register CP_NAME=my-capacity-provider STRATEGY=pause

# Custom schedule
make register CP_NAME=my-capacity-provider \
  SCALE_DOWN_CRON="cron(0 20 ? * * *)" \
  SCALE_UP_CRON="cron(0 8 ? * MON-FRI *)"
```

### 4. Verify

```bash
make list-registrations
```

### 5. Test scale commands

You can invoke scale-down and scale-up on demand without waiting for the cron schedule:

```bash
# Scale down now
make scale-down CP_NAME=my-capacity-provider

# Scale up now
make scale-up CP_NAME=my-capacity-provider
```

### 6. Cost reports

```bash
# Cost report for a specific capacity provider (current month)
make cost-report CP_NAME=my-capacity-provider

# With a custom date range
make cost-report CP_NAME=my-capacity-provider START=2026-01-01 END=2026-02-01

# Aggregate cost report across all registered capacity providers
make cost-report

# Aggregate with date range
make cost-report START=2026-01-01 END=2026-02-01
```

### 7. Clean up

```bash
# Deregister (triggers scale-up if currently scaled down)
make deregister CP_NAME=my-capacity-provider

# Delete the scheduler stack
make teardown-scheduler
```

> **Tip**: For a guided walkthrough using included example LMI infrastructure, see [docs/examples.md](docs/examples.md). For the web dashboard, see [docs/ui.md](docs/ui.md).

### Make targets

```bash
make help
```

| Target | Description |
|--------|------------|
| `deploy-scheduler` | Build and deploy the scheduler SAM stack |
| `teardown-scheduler` | Delete the scheduler stack |
| `outputs` | Print the scheduler API endpoint |
| `status` | Show deployment status of the scheduler stack |
| `register` | Register a CP (requires `CP_NAME`; optional `STRATEGY`, `SCALE_DOWN_CRON`, `SCALE_UP_CRON`) |
| `deregister` | Deregister a CP (requires `CP_NAME`) |
| `scale-down` | Invoke scale-down for a CP (requires `CP_NAME`) |
| `scale-up` | Invoke scale-up for a CP (requires `CP_NAME`) |
| `cost-report` | Get cost report (optional `CP_NAME` for single CP; omit for aggregate) |
| `list-registrations` | List all registered capacity providers |
| `ui-install` | Install UI dependencies |
| `ui-dev` | Start UI dev server (requires `API_ENDPOINT`) |
| `ui-build` | Build UI for production (requires `API_ENDPOINT`) |
| `ui-deploy` | Deploy UI to S3 and invalidate CloudFront cache |
| `ui-stack-deploy` | Deploy the UI hosting stack (S3 + CloudFront) |
| `ui-stack-teardown` | Delete the UI hosting stack |

See `examples/Makefile` for demo infrastructure targets.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `STAGE` | `prod` | Scheduler stage name |
| `REGION` | `ap-southeast-2` | AWS region |
| `STRATEGY` | `shape` | Scaling strategy (`shape` or `pause`) |
| `SCALE_DOWN_CRON` | `cron(0 22 ? * MON-FRI *)` | Scale-down schedule (EventBridge cron) |
| `SCALE_UP_CRON` | `cron(15 6 ? * MON-FRI *)` | Scale-up schedule (EventBridge cron) |
| `START` | _(none)_ | Start date for cost reports (ISO 8601, e.g. `2026-01-01`) |
| `END` | _(none)_ | End date for cost reports (ISO 8601, e.g. `2026-02-01`) |

Example-specific variables (`SUBNET_COUNT`, etc.) are documented in [docs/examples.md](docs/examples.md).

## API reference

All endpoints are relative to the API Gateway base URL (see `make outputs` or the `ApiEndpoint` stack output).

### Register a capacity provider

```text
POST /registrations
```

| Field | Required | Description |
|-------|----------|-------------|
| `capacityProviderName` | Yes | Name of the LMI capacity provider |
| `strategy` | Yes | `shape` or `pause` |
| `scaleDownSchedule` | Yes | EventBridge cron/rate expression for scale-down |
| `scaleUpSchedule` | Yes | EventBridge cron/rate expression for scale-up |
| `minExecutionEnvironments` | No | Target min execution environments for shape strategy (default: 1) |
| `maxExecutionEnvironments` | No | Target max execution environments for shape strategy (default: 1) |

### List registrations

```text
GET /registrations
```

### Get a registration

```text
GET /registrations/{cpName}
```

### Update a registration

```text
PUT /registrations/{cpName}
```

Accepts `strategy`, `scaleDownSchedule`, `scaleUpSchedule` (all optional). Updates are rejected while the capacity provider is scaled down (HTTP 409).

### Delete a registration

```text
DELETE /registrations/{cpName}
```

If the capacity provider is currently scaled down, scale-up is performed automatically before deletion.

### Cost reports

```text
GET /reports/{cpName}?start=2026-01-01&end=2026-02-01
GET /reports?start=2026-01-01&end=2026-02-01
```

Cost estimates are based on EC2 on-demand pricing plus the 15% LMI management fee. A disclaimer is included in every response.

## Project structure

```text
aws-lmi-scheduler/
├── Makefile                              # Scheduler CLI (deploy, register, scale, cost reports)
├── template.yaml                         # Scheduler SAM template
├── samconfig.toml                        # SAM deploy defaults
├── src/
│   └── scheduler/
│       ├── management.py                 # Management API handler (7 routes)
│       ├── executor.py                   # Executor handler (scale-down/scale-up)
│       ├── strategies/
│       │   ├── base.py                   # Strategy interface
│       │   ├── shape.py                  # Shape: adjust execution environment bounds
│       │   └── pause.py                  # Pause: execution environments → 0
│       ├── state/
│       │   └── repository.py             # DynamoDB single-table operations
│       └── reporting/
│           ├── cost.py                   # Fleet snapshot savings calculation
│           └── pricing.py               # EC2 pricing via Price List API with static fallback
├── docs/
│   ├── examples.md                       # Guided walkthrough with example LMI stacks
│   └── ui.md                             # Web dashboard setup, development, and deployment
├── ui/                                   # Scheduler web UI (Nuxt v4 SPA)
│   ├── template.yaml                     # SAM template for S3 + CloudFront hosting
│   ├── nuxt.config.ts                    # Nuxt config (SPA mode, Tailwind, runtime config)
│   ├── pages/                            # File-based routes (dashboard, schedules, reports)
│   ├── components/                       # Vue components (CpCard, ScheduleForm, charts, etc.)
│   ├── composables/                      # API client and data-fetching composables
│   └── schemas/                          # Zod schemas for validation
├── examples/
│   ├── Makefile                          # Example stack lifecycle (deploy/teardown/register)
│   ├── network-stack/template.yaml       # VPC, subnets, security group
│   ├── capacity-provider-stack/template.yaml  # IAM role + capacity provider
│   └── application-stack/template.yaml   # Sample LMI function
└── tests/
    └── unit/
```

## Running tests

```bash
pip install -r tests/requirements.txt

PYTHONPATH=src python -m pytest tests/unit/ -v
```

## Cost reporting limitations

- Estimates use EC2 on-demand pricing via the Price List API with a static fallback. Accounts using Savings Plans, Reserved Instances, or Spot will see different actual savings.
- Fleet snapshots are captured at scale-down time via `ec2:DescribeInstances`. The snapshot reflects allocation at that moment.
- The 15% LMI management fee multiplier is hardcoded and may change.
- Instance types not in the pricing table are flagged as "unknown" and excluded from calculations.

## Security

The scheduler uses least-privilege IAM policies:

- **Management Lambda**: DynamoDB CRUD, EventBridge Scheduler schedule management, `iam:PassRole` (scoped to the scheduler execution role), `lambda:GetCapacityProvider`.
- **Executor Lambda**: DynamoDB CRUD, Lambda capacity provider and function scaling APIs, EC2 DescribeInstances, Price List API.
- **EventBridge Scheduler role**: `lambda:InvokeFunction` (scoped to the Executor), `sqs:SendMessage` (scoped to the DLQ).

## License

MIT
