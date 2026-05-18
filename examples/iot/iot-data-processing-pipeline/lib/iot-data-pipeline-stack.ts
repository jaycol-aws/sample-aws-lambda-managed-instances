import * as cdk from 'aws-cdk-lib';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import { TagEnforcementAspect } from './aspects/tag-enforcement-aspect';
import { SecurityPolicyAspect } from './aspects/security-policy-aspect';
import { SecurityConstruct } from './constructs/security-construct';
import { KinesisConstruct } from './constructs/kinesis-construct';
import { IngestionConstruct } from './constructs/ingestion-construct';
import { LmiProcessorConstruct } from './constructs/lmi-processor-construct';
import { DeliveryConstruct } from './constructs/delivery-construct';
import { FlinkAnalyzerConstruct } from './constructs/flink-analyzer-construct';
import { MonitoringConstruct } from './constructs/monitoring-construct';

/**
 * IoT Data Pipeline Stack
 *
 * Orchestrates all pipeline constructs and wires cross-construct references.
 * Supports multi-region deployment via parameterized CDK context.
 *
 * Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
 */
export class IoTDataPipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ---------------------------------------------------------------
    // Context parameters for multi-region deployment (Req 8.2)
    // ---------------------------------------------------------------
    const environment = this.node.tryGetContext('environment') ?? 'dev';
    const projectName = this.node.tryGetContext('projectName') ?? 'iot-data-pipeline';
    const costCenter = this.node.tryGetContext('costCenter') ?? 'iot-operations';

    // ---------------------------------------------------------------
    // Secrets Manager references for sensitive configuration (Req 8.5)
    // ---------------------------------------------------------------
    const _pipelineSecrets = secretsmanager.Secret.fromSecretNameV2(
      this,
      'PipelineSecrets',
      `iot-pipeline/${environment}/config`,
    );

    // ---------------------------------------------------------------
    // 1. Security Construct — KMS, VPC, VPC endpoints, IAM roles
    // Must be instantiated first as other constructs depend on it
    // ---------------------------------------------------------------
    const security = new SecurityConstruct(this, 'Security', {
      environment,
    });

    // ---------------------------------------------------------------
    // 2. Kinesis Construct — Raw, Enriched, and Alerts streams
    // Depends on: encryptionKey from Security
    // ---------------------------------------------------------------
    const kinesis = new KinesisConstruct(this, 'Kinesis', {
      encryptionKey: security.pipelineKey,
    });

    // ---------------------------------------------------------------
    // 3. Ingestion Construct — IoT Core rules and device authentication
    // Depends on: rawTelemetryStream from Kinesis, iotRuleRole from Security
    // ---------------------------------------------------------------
    new IngestionConstruct(this, 'Ingestion', {
      rawTelemetryStream: kinesis.rawTelemetryStream,
      iotRuleRole: security.iotRuleRole,
    });

    // ---------------------------------------------------------------
    // 4. LMI Processor Construct — Lambda function, ESM, DLQ
    // Depends on: vpc, securityGroup, lmiExecutionRole from Security,
    //             rawTelemetryStream, enrichedTelemetryStream from Kinesis,
    //             encryptionKey from Security
    // ---------------------------------------------------------------
    const lmiProcessor = new LmiProcessorConstruct(this, 'LmiProcessor', {
      vpc: security.vpc,
      securityGroup: security.pipelineSecurityGroup,
      encryptionKey: security.pipelineKey,
      rawTelemetryStream: kinesis.rawTelemetryStream,
      enrichedStreamName: kinesis.enrichedTelemetryStream.streamName,
      lmiExecutionRole: security.lmiExecutionRole,
    });

    // ---------------------------------------------------------------
    // 5. Delivery Construct — DynamoDB, S3, SNS, alerts publisher
    // Depends on: encryptionKey from Security, alertsStream from Kinesis,
    //             snsPublishRole from Security
    // ---------------------------------------------------------------
    const delivery = new DeliveryConstruct(this, 'Delivery', {
      encryptionKey: security.pipelineKey,
      alertsStream: kinesis.alertsStream,
      snsPublishRole: security.snsPublishRole,
    });

    // ---------------------------------------------------------------
    // 6. Flink Analyzer Construct — Managed Apache Flink application
    // Depends on: vpc, securityGroup, flinkExecutionRole from Security,
    //             enrichedTelemetryStream, alertsStream from Kinesis,
    //             analyticsTable, dataLakeBucket from Delivery
    // ---------------------------------------------------------------
    new FlinkAnalyzerConstruct(this, 'FlinkAnalyzer', {
      vpc: security.vpc,
      securityGroup: security.pipelineSecurityGroup,
      flinkExecutionRole: security.flinkExecutionRole,
      enrichedTelemetryStream: kinesis.enrichedTelemetryStream,
      alertsStream: kinesis.alertsStream,
      analyticsTable: delivery.analyticsTable,
      dataLakeBucket: delivery.dataLakeBucket,
    });

    // ---------------------------------------------------------------
    // 7. Monitoring Construct — CloudWatch dashboard, alarms, log groups
    // Depends on: encryptionKey from Security, deadLetterQueue from LMI
    // ---------------------------------------------------------------
    new MonitoringConstruct(this, 'Monitoring', {
      encryptionKey: security.pipelineKey,
      deadLetterQueue: lmiProcessor.deadLetterQueue,
    });

    // ---------------------------------------------------------------
    // Stack-level tags for resource management and cost allocation (Req 8.4)
    // Applied BEFORE tag enforcement aspect so tags propagate first
    // ---------------------------------------------------------------
    cdk.Tags.of(this).add('Environment', environment);
    cdk.Tags.of(this).add('Project', projectName);
    cdk.Tags.of(this).add('CostCenter', costCenter);

    // ---------------------------------------------------------------
    // Apply CDK Aspects for tag enforcement and security checks (Req 8.3, 8.4)
    // Registered AFTER tag application to ensure tags are visible during validation
    // ---------------------------------------------------------------
    cdk.Aspects.of(this).add(new TagEnforcementAspect());
    cdk.Aspects.of(this).add(new SecurityPolicyAspect());
  }
}
