import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaNodejs from 'aws-cdk-lib/aws-lambda-nodejs';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';
import * as path from 'path';

export interface LmiProcessorConstructProps {
  /** VPC with private subnets for LMI Capacity Provider deployment */
  readonly vpc: ec2.IVpc;

  /** Security group restricting egress to VPC endpoints only */
  readonly securityGroup: ec2.ISecurityGroup;

  /** KMS customer-managed key for encrypting SQS DLQ */
  readonly encryptionKey: kms.IKey;

  /** Raw telemetry Kinesis stream (event source for LMI processor) */
  readonly rawTelemetryStream: kinesis.IStream;

  /** Name of the enriched telemetry stream (for Lambda environment variable) */
  readonly enrichedStreamName: string;

  /** IAM execution role for the LMI Lambda function */
  readonly lmiExecutionRole: iam.IRole;
}

/**
 * LMI Processor Construct
 *
 * Creates the Lambda Managed Instances (LMI) processor infrastructure:
 * - SQS Dead Letter Queue with KMS CMK encryption and 14-day retention
 * - Lambda function (Node.js 22.x) configured for LMI Capacity Provider
 * - Kinesis Event Source Mapping with partial batch response, bisect on error,
 *   and on-failure destination to DLQ
 *
 * Note: LMI is a new Lambda capability that runs on customer-owned EC2 instances.
 * Since LMI-specific CDK constructs may not yet exist in aws-cdk-lib, this
 * implementation uses standard Lambda constructs with comments noting where
 * LMI-specific configuration (Capacity Provider, multi-concurrency) would go.
 *
 * Requirements: 3.1, 3.2, 3.5, 3.6, 3.8, 3.9, 7.1, 7.3, 7.4
 */
export class LmiProcessorConstruct extends Construct {
  /** SQS Dead Letter Queue for failed telemetry records */
  public readonly deadLetterQueue: sqs.Queue;

  /** Lambda function for LMI processing */
  public readonly processorFunction: lambdaNodejs.NodejsFunction;

  constructor(scope: Construct, id: string, props: LmiProcessorConstructProps) {
    super(scope, id);

    // ---------------------------------------------------------------
    // SQS Dead Letter Queue — KMS CMK encryption, 14-day retention
    // Req 3.5, 7.1: Failed records written here with original payload
    // and validation error description, encrypted at rest with CMK
    // ---------------------------------------------------------------
    this.deadLetterQueue = new sqs.Queue(this, 'ProcessorDlq', {
      queueName: 'iot-pipeline-dlq',
      encryption: sqs.QueueEncryption.KMS,
      encryptionMasterKey: props.encryptionKey,
      retentionPeriod: cdk.Duration.days(14),
      visibilityTimeout: cdk.Duration.seconds(300),
    });

    // ---------------------------------------------------------------
    // Lambda Function — LMI Processor
    // Req 3.1, 3.2, 3.6, 7.3, 7.4
    //
    // LMI Capacity Provider configuration (conceptual):
    // - VPC: Private subnets across 2+ AZs (Req 3.9, 7.4)
    // - Security groups: Egress to VPC endpoints only
    // - Minimum function size: 2 GB memory, 1 vCPU (Req 3.2)
    // - Scaling mode: Auto (CPU utilization-based)
    // - Multi-concurrency: Enabled (Req 3.6)
    //
    // Note: When LMI-specific CDK constructs become available, replace
    // the standard Lambda function with an LMI Capacity Provider resource
    // and associate this function with it. The VPC/subnet/SG configuration
    // below represents the network placement that the Capacity Provider
    // would manage.
    // ---------------------------------------------------------------
    this.processorFunction = new lambdaNodejs.NodejsFunction(this, 'LmiProcessorFn', {
      functionName: 'iot-lmi-processor',
      description: 'LMI processor: validates, enriches, and routes IoT telemetry records',
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'handler',
      entry: path.join(__dirname, '../../lambda/lmi-processor/src/handler.ts'),
      role: props.lmiExecutionRole,
      bundling: {
        minify: true,
        sourceMap: true,
        target: 'node22',
        externalModules: [
          '@aws-sdk/client-kinesis',
          '@aws-sdk/client-sqs',
          '@aws-sdk/client-cloudwatch',
        ],
      },

      // Req 3.2: Minimum 2 GB memory / 1 vCPU for LMI Capacity Provider
      memorySize: 2048,
      timeout: cdk.Duration.seconds(60),

      // Req 3.9, 7.4: Private subnets across 2+ AZs, no internet access
      vpc: props.vpc,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
      },
      securityGroups: [props.securityGroup],

      environment: {
        ENRICHED_STREAM_NAME: props.enrichedStreamName,
        DLQ_URL: '', // Will be set after queue creation via addEnvironment
        NODE_OPTIONS: '--enable-source-maps',
        PIPELINE_VERSION: '1.0.0',
      },
    });

    // Set the DLQ URL environment variable (circular reference resolved)
    this.processorFunction.addEnvironment('DLQ_URL', this.deadLetterQueue.queueUrl);

    // ---------------------------------------------------------------
    // Event Source Mapping — Kinesis to LMI Processor
    // Req 3.1, 3.8: Batch size 100, batching window 5s, partial batch
    // response enabled, bisect on error, max retry 3, on-failure DLQ
    // ---------------------------------------------------------------
    this.processorFunction.addEventSource(
      new lambdaEventSources.KinesisEventSource(props.rawTelemetryStream, {
        // Req 3.1: Batch size up to 100 records
        batchSize: 100,

        // Req 3.1: Batching window of up to 5 seconds
        maxBatchingWindow: cdk.Duration.seconds(5),

        // Starting position: process all existing records
        startingPosition: lambda.StartingPosition.TRIM_HORIZON,

        // Req 3.8: Report partial batch failures
        reportBatchItemFailures: true,

        // Bisect batch on error to isolate failing records
        bisectBatchOnError: true,

        // Maximum retry attempts before sending to on-failure destination
        retryAttempts: 3,

        // On-failure destination: SQS DLQ for records that exhaust retries
        onFailure: new lambdaEventSources.SqsDlq(this.deadLetterQueue),
      }),
    );
  }
}
