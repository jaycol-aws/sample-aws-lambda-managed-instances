# Example stacks

This guide walks through deploying the included example LMI infrastructure, registering it with the scheduler, testing scale operations, and tearing it all down.

> **Prerequisites**: The scheduler must already be deployed (`make deploy-scheduler` from the project root). See the main [README](../README.md) for details.

## Overview

The examples directory contains three CloudFormation stacks that create a minimal LMI environment for testing:

| Stack | Template | What it creates |
|-------|----------|----------------|
| `lmi-example-network` | `network-stack/template.yaml` | VPC, private subnets, security group |
| `lmi-example-capacity-provider` | `capacity-provider-stack/template.yaml` | IAM operator role, LMI capacity provider (arm64) |
| `lmi-example-application` | `application-stack/template.yaml` | Sample Lambda function on the capacity provider |

A **shape variant** reuses the capacity provider and application templates with different stack names, giving you a second CP to test with the shape strategy:

| Stack | What it creates |
|-------|----------------|
| `lmi-example-capacity-provider-shape` | Second capacity provider (`lmi-example-cp-shape`) |
| `lmi-example-application-shape` | Second function (`lmi-example-function-shape`) |

Both variants share the network stack.

## Deploy

All commands run from the `examples/` directory:

```bash
cd examples
```

### Pause example (network + CP + app)

```bash
make deploy-pause
```

This deploys the three stacks in order: network → capacity provider → application. Each stack resolves its dependencies from the previous stack's outputs automatically.

### Shape example (CP + app)

The shape example reuses the network stack from above:

```bash
make deploy-shape
```

This deploys `lmi-example-capacity-provider-shape` and `lmi-example-application-shape`.

### Check outputs

```bash
make outputs
```

Prints CP names, ARNs, and function ARNs for both the pause and shape examples:

```
==========================================
  Example Stack Outputs
==========================================

  Pause Example:
    CP Name:       lmi-example-cp
    CP ARN:        arn:aws:lambda:ap-southeast-2:123456789012:capacity-provider/lmi-example-cp
    Function ARN:  arn:aws:lambda:ap-southeast-2:123456789012:function:lmi-example-function

  Shape Example:
    CP Name:       lmi-example-cp-shape
    CP ARN:        arn:aws:lambda:ap-southeast-2:123456789012:capacity-provider/lmi-example-cp-shape
    Function ARN:  arn:aws:lambda:ap-southeast-2:123456789012:function:lmi-example-function-shape
```

### Check deployment status

```bash
make status
```

Shows the CloudFormation status of all five example stacks.

## Register and test the pause example

### Register

```bash
make register-pause-strategy
```

This resolves the CP name from the example stack and registers it with the scheduler using the `pause` strategy. Uses the default schedule (scale down at 10 PM, scale up at 6 AM, weekdays).

### Test scale operations

You can invoke scale-down and scale-up immediately without waiting for the schedule:

```bash
# Scale down (sets min/max execution environments to 0)
make scale-down-pause-strategy

# Scale up (restores original execution environment values)
make scale-up-pause-strategy
```

### Cost report

```bash
# Current month
make cost-report-pause-strategy

# Custom date range (pass START/END through to the root Makefile)
make cost-report-pause-strategy START=2026-01-01 END=2026-02-01
```

### Deregister

```bash
make deregister-pause-strategy
```

If the CP is currently scaled down, scale-up is performed automatically before deregistration.

## Register and test the shape example

### Register

```bash
make register-shape-strategy
```

Registers the shape CP (`lmi-example-cp-shape`) with the `shape` strategy.

### Test scale operations

```bash
# Scale down (adjusts execution environment bounds to operator-specified targets)
make scale-down-shape-strategy

# Scale up (restores original execution environment values)
make scale-up-shape-strategy
```

### Cost report

```bash
make cost-report-shape-strategy

make cost-report-shape-strategy START=2026-01-01 END=2026-02-01
```

### Deregister

```bash
make deregister-shape-strategy
```

## Teardown

### Deregister first

Always deregister capacity providers before tearing down stacks:

```bash
make deregister-pause-strategy
make deregister-shape-strategy
```

### Delete example stacks

```bash
# Tear down pause stacks (app → CP → network, in reverse order)
make teardown-pause

# Tear down shape stacks (app → CP)
make teardown-shape
```

> **Warning**: The example stacks provision EC2 instances and will incur costs. Run teardown when done.

## Make targets

| Target | Description |
|--------|------------|
| `deploy-pause` | Deploy all pause stacks in order (network, CP, app) |
| `deploy-pause-cp` | Deploy the pause capacity provider stack |
| `deploy-pause-app` | Deploy the pause application stack |
| `deploy-shape` | Deploy all shape stacks in order (CP, app) |
| `deploy-shape-cp` | Deploy the shape capacity provider stack |
| `deploy-shape-app` | Deploy the shape application stack |
| `deploy-network` | Deploy the network stack (VPC, subnets, security group) |
| `register-pause-strategy` | Register the pause example CP with the scheduler |
| `deregister-pause-strategy` | Deregister the pause example CP |
| `scale-down-pause-strategy` | Scale down the pause example CP |
| `scale-up-pause-strategy` | Scale up the pause example CP |
| `cost-report-pause-strategy` | Get cost report for the pause example CP |
| `register-shape-strategy` | Register the shape example CP with the scheduler |
| `deregister-shape-strategy` | Deregister the shape example CP |
| `scale-down-shape-strategy` | Scale down the shape example CP |
| `scale-up-shape-strategy` | Scale up the shape example CP |
| `cost-report-shape-strategy` | Get cost report for the shape example CP |
| `teardown-pause` | Tear down all pause stacks in reverse order |
| `teardown-pause-app` | Delete the pause application stack |
| `teardown-pause-cp` | Delete the pause capacity provider stack |
| `teardown-shape` | Tear down all shape stacks in reverse order |
| `teardown-shape-app` | Delete the shape application stack |
| `teardown-shape-cp` | Delete the shape capacity provider stack |
| `teardown-network` | Delete the network stack |
| `outputs` | Print example stack outputs (CP names, ARNs, function ARNs) |
| `status` | Show deployment status of all example stacks |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBNET_COUNT` | `3` | Number of subnets in the network stack (1-3) |
| `REGION` | `ap-southeast-2` | AWS region for all stack operations |
