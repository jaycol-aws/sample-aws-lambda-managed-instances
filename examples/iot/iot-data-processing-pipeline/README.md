# Building a real-time IoT telemetry pipeline with Lambda Managed Instances and Apache Flink on AWS

IoT fleets generating thousands of sensor readings per second need a processing pipeline that validates, enriches, and analyzes data in near real-time. The challenge: doing this without managing servers or accepting the cost unpredictability of per-invocation pricing at sustained throughput.

This project implements a complete serverless IoT telemetry pipeline that combines AWS IoT Core, Amazon Kinesis Data Streams, Lambda Managed Instances (LMI), and Managed Service for Apache Flink. The pipeline processes raw sensor data end-to-end, from device ingestion through real-time analytics and alerting, with all infrastructure defined in AWS CDK.

In this post, you'll learn how to build, test, and deploy this pipeline in your own account.

## Solution overview

The pipeline processes IoT telemetry through four stages: ingestion, processing, analytics, and delivery.

```
IoT Devices ──► IoT Core ──► Kinesis (raw) ──► Lambda LMI ──► Kinesis (enriched) ──► Flink
   (MQTT)        (X.509)      (buffer)         (validate/       (processed)          (analytics)
                                                 enrich)
                                                   │                                     │
                                                   ▼                                     ▼
                                               SQS DLQ                          DynamoDB / S3 / SNS
```

Devices authenticate with X.509 certificates and publish MQTT messages to per-device topics (`devices/{deviceId}/telemetry`). An IoT Core topic rule routes all telemetry into a Kinesis raw stream using `${clientid()}` as the partition key, preserving per-device ordering.

Lambda Managed Instances consume the raw stream in batches of up to 100 records. Each record is decoded, validated against a strict schema, enriched with device location metadata and sensor classification, then written to an enriched Kinesis stream. Records that fail validation are written to an SQS dead-letter queue with the original payload preserved byte-for-byte.

Managed Apache Flink reads the enriched stream, keys records by device group, and applies tumbling event-time windows (60 seconds by default). Within each window, the `StatisticsAggregator` computes per-sensor-type statistics: average, min, max, standard deviation, and count. A `ThresholdDetector` evaluates configurable rules against these statistics and emits alerts when thresholds are breached.

The analytics results fan out to three destinations: an Amazon DynamoDB table for operational queries, an S3 data lake as Parquet files with Snappy compression for historical analysis, and an Amazon SNS topic for real-time alert notifications.

## Why Lambda Managed Instances

Standard Lambda charges per invocation and per-millisecond of compute. For IoT workloads with sustained, predictable throughput, this pricing model becomes expensive. Lambda Managed Instances provide EC2-based pricing with CPU-driven autoscaling, making costs predictable while retaining the operational simplicity of Lambda.

LMI also supports multi-concurrent invocations within a single execution environment. The pipeline configures 2 GB memory with a 60-second timeout, and the event source mapping uses partial batch response reporting. When a record fails due to a transient error (network timeout, throttling), only that record is retried. Deterministic failures like schema violations are written to the DLQ and the batch advances, preventing retry storms.

## How the Flink application works

The Flink application (`FlinkAnalyzerApp.java`) runs on Flink 1.19 with exactly-once checkpointing at 60-second intervals. The pipeline topology:

1. **Kinesis source** reads from the enriched telemetry stream with bounded-out-of-orderness watermarks (10-second tolerance).
2. **Keying** groups records by `deviceGroupId` so that all sensors in a facility or zone are aggregated together.
3. **Tumbling windows** collect records over configurable intervals (default 60 seconds).
4. **StatisticsAggregator** computes rolling statistics per sensor type within each window.
5. **Fan-out** sends results to two sinks:
   - `IdempotentDynamoDBSink` writes analytics results using conditional `PutItem` (`attribute_not_exists(deviceGroupId)`). On checkpoint recovery, replayed records are silently deduplicated.
   - `ThresholdDetector` evaluates rules (e.g., temperature average > 85°C triggers a warning) and emits `AlertRecord` objects to the alerts Kinesis stream via `AlertEmitter`.
6. **S3 FileSink** writes enriched records as Parquet with Snappy compression, partitioned by `year=/month=/day=/sensor_type=`.

The RocksDB state backend handles state that exceeds memory. Checkpoints are retained on cancellation for manual recovery.

## Idempotent writes and exactly-once delivery

Flink's exactly-once checkpointing guarantees that each record is processed once under normal operation. During checkpoint recovery, records between the last successful checkpoint and the failure point are replayed. Without idempotent sinks, this replay would create duplicate rows in DynamoDB.

The `IdempotentDynamoDBSink` solves this with a conditional write:

```java
PutItemRequest request = PutItemRequest.builder()
    .tableName(tableName)
    .item(item)
    .conditionExpression("attribute_not_exists(deviceGroupId)")
    .build();
```

If the record already exists (from a previous successful write before the failure), the `ConditionalCheckFailedException` is caught and logged at INFO level. The sink retries up to 3 times with exponential backoff (100ms, 200ms, 400ms) for transient errors, then logs to CloudWatch after exhausting retries.

Each record includes a `writeId` (SHA-256 hash of `deviceGroupId:windowEndTimestamp:sensorType`) for auditability.

## VPC and security architecture

The entire pipeline runs within a VPC that has no internet gateway and no NAT gateways. All AWS service communication flows through VPC endpoints:

- **Interface endpoints**: Kinesis Streams, SQS, CloudWatch Monitoring, CloudWatch Logs, KMS, Secrets Manager
- **Gateway endpoints**: S3, DynamoDB

A shared KMS customer-managed key encrypts all data at rest: Kinesis streams, DynamoDB table, S3 bucket, SQS queue, and SNS topics. Key rotation is enabled.

Security groups follow a two-tier model. The pipeline security group allows egress only to the endpoint security group on port 443. The endpoint security group allows inbound HTTPS from the VPC CIDR. No other traffic is permitted.

IAM roles follow least-privilege principles with no wildcard actions. Each component (LMI processor, Flink application, IoT rule, alerts publisher) has a dedicated role scoped to the specific resources it needs.

## CDK Aspects for policy enforcement

Two CDK Aspects run during synthesis to catch misconfigurations before deployment:

**TagEnforcementAspect** visits every `CfnResource` and verifies that `Environment`, `Project`, and `CostCenter` tags are present. Missing tags produce synthesis errors.

**SecurityPolicyAspect** checks two policies:
1. No public subnets exist (flags any subnet with `MapPublicIpOnLaunch: true`).
2. KMS encryption is configured on Kinesis streams, DynamoDB tables, S3 buckets, SQS queues, and SNS topics.

These aspects act as guardrails. A developer adding a new resource without encryption or tags will see the error at `cdk synth` time, not after deployment.

## Testing approach

The project uses three testing strategies:

**Property-based tests** (fast-check) validate correctness properties across random inputs:
- Property 1: Any invalid record is rejected by the validator, and the original payload survives a base64 round-trip for DLQ storage.
- Property 2: Any valid record produces an enriched output with ISO-8601 timestamps, non-empty location fields, correct sensor classification, and preserved readings.
- Property 3: The batch handler returns an empty `batchItemFailures` array for deterministic failures (they go to DLQ, not retry).

**CDK infrastructure assertions** (61 tests) verify the synthesized CloudFormation template: KMS encryption on all resources, VPC configuration, IAM role policies, event source mapping settings, Flink checkpointing, and tag propagation.

**Integration and stress tests** run against a deployed stack with real AWS resources, validating end-to-end data flow from IoT Core through to DynamoDB and S3.

## Prerequisites

- Node.js 22.x
- Java 11+ and Maven 3.8+ (for the Flink application)
- AWS CDK CLI (`npm install -g aws-cdk`)
- AWS credentials configured with permissions to create the required resources

## Project structure

```
lib/
  iot-data-pipeline-stack.ts          Main CDK stack orchestrating all constructs
  constructs/
    security-construct.ts             KMS, VPC, VPC endpoints, IAM roles
    kinesis-construct.ts              Three Kinesis Data Streams
    ingestion-construct.ts            IoT Core topic rule and device policy
    lmi-processor-construct.ts        Lambda function, event source mapping, DLQ
    delivery-construct.ts             DynamoDB, S3, SNS, alerts publisher
    flink-analyzer-construct.ts       Managed Apache Flink application
    monitoring-construct.ts           Dashboard, alarms, log groups
  aspects/
    tag-enforcement-aspect.ts         Required tag validation
    security-policy-aspect.ts         Encryption and subnet checks

lambda/lmi-processor/
  src/
    handler.ts                        Batch handler with partial failure reporting
    validator.ts                      Manual JSON schema validation
    enricher.ts                       Record enrichment (location, classification, correlation ID)
    device-registry.ts                Pluggable device metadata interface
    types.ts                          Shared TypeScript interfaces
  test/
    validator.property.test.ts        Property-based: invalid record handling
    enricher.property.test.ts         Property-based: enrichment correctness
    batch.property.test.ts            Property-based: partial batch response
    handler.test.ts                   Unit tests for the batch handler
    validator.test.ts                 Unit tests for schema validation
    enricher.test.ts                  Unit tests for enrichment logic

flink/
  src/main/java/com/iotpipeline/
    FlinkAnalyzerApp.java             Application entry point and topology
    StatisticsAggregator.java         Tumbling window aggregation
    ThresholdDetector.java            Configurable threshold rules
    AlertEmitter.java                 Kinesis alerts stream sink
    IdempotentDynamoDBSink.java       Conditional write sink
    EnrichedRecordDeserializer.java   Kinesis record deserialization

test/
  iot-data-pipeline.test.ts           CDK infrastructure assertions (61 tests)
  integration/                        Live AWS integration and stress tests
```

## Build and deploy

Install dependencies and build all components:

```bash
npm install
cd lambda/lmi-processor && npm install && cd ../..
cd flink && mvn clean package -DskipTests && cd ..
```

Synthesize the CloudFormation template to verify everything compiles:

```bash
npx cdk synth
```

Run the test suites:

```bash
# Lambda processor tests (unit + property-based)
cd lambda/lmi-processor && npx jest && cd ../..

# Flink unit tests
cd flink && mvn test && cd ..

# CDK infrastructure assertions
npx jest
```

Deploy to your account:

```bash
npx cdk bootstrap   # First time only
npx cdk deploy
```

Override context parameters for different environments:

```bash
npx cdk deploy -c environment=production -c region=eu-west-1
```

## Monitoring and operations

The stack provisions a CloudWatch dashboard (`IoT-Pipeline-Operations`) with five widget rows: pipeline throughput, end-to-end latency percentiles, LMI health (CPU, execution environments, concurrency saturation), Flink health (checkpoint duration, records/second, watermark lag), and alert/error activity.

Five alarms notify the `iot-pipeline-ops` SNS topic:
- End-to-end latency exceeds 30 seconds
- LMI error rate exceeds 5%
- Flink checkpoint duration exceeds 120 seconds
- Flink watermark lag exceeds 300 seconds
- DLQ message count greater than 0

All components emit structured JSON logs with correlation IDs, enabling trace-level debugging across the pipeline stages.

## Extending the pipeline

**Custom threshold rules**: Modify the `getDefaultThresholdRules()` method in `FlinkAnalyzerApp.java` or load rules from a configuration source. The `ThresholdDetector` accepts any list of `ThresholdRule` objects specifying sensor type, metric, threshold value, operator, and severity.

**Device registry integration**: Replace `StubDeviceRegistry` with an implementation that queries your device management system (AWS IoT Device Shadow, a DynamoDB registry table, or an external API). The `DeviceRegistry` interface requires a single async method: `getDevice(deviceId) → DeviceRegistryEntry | null`.

**Additional sensor types**: Add new values to the `sensorType` union in `types.ts`, update the `VALID_SENSOR_TYPES` array in `validator.ts`, and add classification mappings in `enricher.ts`.

## Cleanup

Remove all deployed resources:

```bash
npx cdk destroy
```

The DynamoDB table and S3 bucket use `RETAIN` removal policies. Delete them manually if you no longer need the data.

## Related resources

- [AWS IoT Core documentation](https://docs.aws.amazon.com/iot/latest/developerguide/)
- [Amazon Kinesis Data Streams developer guide](https://docs.aws.amazon.com/streams/latest/dev/)
- [Managed Service for Apache Flink documentation](https://docs.aws.amazon.com/managed-flink/latest/java/)
- [AWS CDK developer guide](https://docs.aws.amazon.com/cdk/v2/guide/)
- [Lambda Managed Instances](https://docs.aws.amazon.com/lambda/latest/dg/lambda-managed-instances.html)
