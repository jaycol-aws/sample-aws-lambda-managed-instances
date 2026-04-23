# Java Lambda Optimization: Comparing 4 Deployment Modes

## Introduction

This repository provides a ready-to-deploy benchmark framework for comparing Spring Boot 3.4 workloads on AWS Lambda across four deployment modes. It helps you evaluate cold-start behavior, warm-execution latency, and tail-latency characteristics so you can choose the deployment mode that fits your workload profile.

| Mode | Description |
|------|-------------|
| **Standard AWS Lambda** | Default AWS Lambda execution — full JVM cold start on each new instance |
| **Lambda SnapStart** | CRaC-based snapshot restore — skips JVM boot and Spring context initialization |
| **AWS Lambda Managed Instances (LMI)** | Always-warm instances with persistent JIT compilation — no cold starts |
| **GraalVM Native Image** | Ahead-of-time (AOT) compiled binary — sub-second initialization, no JVM |

## Use Cases

Three workloads exercise different JVM optimization dimensions:

| # | Use Case | Workload Profile | Key JVM Dimension |
|---|----------|-------------------|-------------------|
| 1 | **PDF Generation** | Query Amazon DynamoDB → render multi-page PDF → upload to Amazon S3 | CPU-intensive (string formatting, byte manipulation) |
| 2 | **Data Aggregation** | Query Amazon DynamoDB → in-memory aggregation → CSV export to Amazon S3 | Memory-intensive (large collections, streaming) |
| 3 | **API Orchestration** | Fan-out to Amazon DynamoDB + Amazon SNS + Amazon SQS in parallel → compose response | I/O + concurrency (CompletableFuture, thread pools) |

Each use case is deployable in all four modes with identical business logic.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  Amazon API Gateway                      │
├──────────┬──────────┬──────────┬────────────────────────┤
│ Standard │ SnapStart│   LMI    │    GraalVM Native      │
│  Lambda  │  Lambda  │  Lambda  │      Lambda            │
│ (JVM)    │ (CRaC)   │ (JVM)    │   (AOT binary)         │
├──────────┴──────────┴──────────┴────────────────────────┤
│           DynamoDB  ·  S3  ·  SNS  ·  SQS               │
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

- **Java 21** (Amazon Corretto)
- **Spring Boot 3.4.3** with Spring Cloud Function AWS Adapter
- **AWS SDK v2** (DynamoDB Enhanced Client with `StaticTableSchema`)
- **GraalVM CE 21** for native image builds
- **AWS SAM CLI** for deployment
- **CRaC 1.5.0** for SnapStart priming

## Project Structure

```
├── use-case-1-pdf-generation/
│   ├── src/main/java/com/aws/lmi/pdf/
│   ├── pom.xml
│   ├── template.yaml              # Standard Lambda
│   ├── template.snapstart.yaml    # SnapStart
│   ├── template.lmi.yaml          # Lambda Managed Instances
│   └── template.native.yaml       # GraalVM Native Image
├── use-case-2-data-aggregation/
│   └── (same structure)
├── use-case-3-api-orchestration/
│   └── (same structure)
└── shared/
    ├── scripts/seed-data.py       # DynamoDB data seeding
    ├── scripts/run-benchmark.sh   # Load test runner
    ├── metrics/JvmMetricsCollector.java
    └── cloudwatch-queries.md      # Amazon CloudWatch Logs Insights queries
```

## Quick Start

### Prerequisites

- Java 21 (Amazon Corretto recommended)
- Maven 3.9+
- AWS SAM CLI v1.155+ (required for LMI `AWS::Serverless::CapacityProvider`)
- Docker (for GraalVM native builds)
- AWS CLI configured with appropriate permissions
- [hey](https://github.com/rakyll/hey) for load testing

### Deploy a Use Case (Standard Lambda)

```bash
cd use-case-1-pdf-generation

# Build
export JAVA_HOME=/path/to/corretto-21
mvn clean package

# Deploy
sam deploy --template-file template.yaml \
  --stack-name ljpt-pdf-standard \
  --resolve-s3 --capabilities CAPABILITY_IAM \
  --parameter-overrides MemorySize=1024

# Seed test data
cd ../shared/scripts
python3 seed-data.py --use-case 1 --stack-name ljpt-pdf-standard

# Test
API=$(aws cloudformation describe-stacks --stack-name ljpt-pdf-standard \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text)
curl -X POST "$API" -H "Content-Type: application/json" \
  -d '{"accountId":"ACCT-1","startDate":"2026-01-01","endDate":"2026-01-31"}'
```

### Deploy with SnapStart

```bash
sam deploy --template-file template.snapstart.yaml \
  --stack-name ljpt-pdf-snapstart \
  --resolve-s3 --capabilities CAPABILITY_IAM \
  --parameter-overrides MemorySize=1024
```

### Deploy with LMI

LMI requires a VPC. Provide subnet IDs and a security group:

```bash
sam deploy --template-file template.lmi.yaml \
  --stack-name ljpt-pdf-lmi \
  --resolve-s3 --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    SubnetIds=subnet-xxx,subnet-yyy \
    SecurityGroupIds=sg-zzz
```

> **Note:** LMI uses `ExecutionEnvironmentMemoryGiBPerVCpu` (minimum 2 GiB per vCPU) and provisions always-on instances. These instances incur charges while running. Delete the stack when done to stop charges.

### Deploy with GraalVM Native Image

```bash
# Build native binary (requires Docker)
docker run --rm --platform linux/amd64 \
  -v "$(pwd)":/project -v "$HOME/.m2":/root/.m2 -w /project \
  -e MAVEN_OPTS="-Xmx4g" --entrypoint mvn graalvm-maven:21 \
  clean package -Pnative -DskipTests

# Deploy
sam deploy --template-file template.native.yaml \
  --stack-name ljpt-pdf-native \
  --resolve-s3 --capabilities CAPABILITY_IAM
```

> **Note:** The GraalVM Docker image (`graalvm-maven:21`) must be built first. See the following [GraalVM Build Setup](#graalvm-build-setup) section.

### Run Load Tests

```bash
API="<your API endpoint from stack outputs>"
hey -n 500 -c 2 -q 5 -m POST -T "application/json" \
  -d '{"accountId":"ACCT-1","startDate":"2026-01-01","endDate":"2026-01-31"}' \
  "$API"
```

## GraalVM Build Setup

Build the Docker image used for native compilation:

```dockerfile
FROM container-registry.oracle.com/graalvm/community:21-ol9
RUN microdnf install -y maven findutils tar gzip && microdnf clean all
ENTRYPOINT ["mvn"]
```

```bash
docker build -t graalvm-maven:21 .
```

### Key GraalVM Adaptations

1. **`StaticTableSchema`** instead of `TableSchema.fromBean()` — the DynamoDB Enhanced Client's annotation-based schema uses hidden class generation that is incompatible with AOT compilation
2. **`reflect-config.json`** for model POJOs — GraalVM needs explicit reflection metadata
3. **`--initialize-at-build-time=org.slf4j,ch.qos.logback`** — logging framework initialization at build time
4. **Spring AOT processing** via `spring-boot-maven-plugin` `process-aot` goal

## Key Design Decisions

- **Spring Cloud Function** adapter for AWS Lambda — single handler class works across all four modes
- **CRaC priming** in `beforeCheckpoint()` — exercises DynamoDB client, Jackson, and business logic before SnapStart snapshot
- **AWS X-Ray safe degradation** — `traced()` methods catch `NoClassDefFoundError` for modes where the X-Ray agent is not available
- **`FANOUT_EXECUTOR`** (use case 3) — dedicated `CachedThreadPool` for `CompletableFuture` fan-out, avoids `ForkJoinPool` contention under LMI multi-concurrency
- **EMF (Embedded Metric Format) metrics** — each handler emits Amazon CloudWatch Embedded Metric Format JSON for custom dashboards

## Cleanup

> **Important:** Each deployed stack creates billable AWS resources including AWS Lambda functions, Amazon DynamoDB tables (on-demand billing), Amazon S3 buckets, and Amazon API Gateway REST APIs. LMI stacks additionally provision always-on instances that incur continuous charges. Delete all stacks promptly when you are done testing. The API Gateway REST APIs are deleted automatically when the CloudFormation stack is deleted.

> **Cost Warning:** The benchmark scripts (`run-benchmark.sh`, `run-final-benchmark.sh`, etc.) execute a large number of Lambda invocations (thousands per run) that incur costs. Review the script parameters before running and monitor your AWS billing dashboard.

> **Warning:** Some templates include Amazon S3 lifecycle rules that automatically delete objects after 7 days. If you need to retain generated output (PDFs, CSVs), download them before the lifecycle policy takes effect.

Delete all stacks for each use case when done:

```bash
# Use case 1 — PDF Generation
for STACK in ljpt-pdf-standard ljpt-pdf-snapstart ljpt-pdf-lmi ljpt-pdf-native; do
  aws cloudformation delete-stack --stack-name "$STACK"
done

# Use case 2 — Data Aggregation
for STACK in ljpt-etl-standard ljpt-etl-snapstart ljpt-etl-lmi ljpt-etl-native; do
  aws cloudformation delete-stack --stack-name "$STACK"
done

# Use case 3 — API Orchestration
for STACK in ljpt-api-standard ljpt-api-snapstart ljpt-api-lmi ljpt-api-native; do
  aws cloudformation delete-stack --stack-name "$STACK"
done
```

To verify that all stacks have been deleted:

```bash
aws cloudformation list-stacks --stack-status-filter DELETE_COMPLETE \
  --query 'StackSummaries[?starts_with(StackName,`ljpt-`)].StackName' --output table
```

> **Note:** Amazon CloudWatch log groups created by AWS Lambda are not deleted with the stack. To remove them manually:
>
> ```bash
> aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/ljpt-" \
>   --query 'logGroups[].logGroupName' --output text | tr '\t' '\n' | \
>   while read lg; do aws logs delete-log-group --log-group-name "$lg"; done
> ```

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## Conclusion

This benchmark framework provides a reproducible way to compare AWS Lambda deployment modes for Java workloads. By deploying the same Spring Boot application across Standard, SnapStart, LMI, and GraalVM Native modes, you can measure cold-start, warm-latency, and tail-latency tradeoffs for your own workload profiles. Use the results to inform which deployment mode to adopt based on your traffic patterns, latency requirements, and cost constraints.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
