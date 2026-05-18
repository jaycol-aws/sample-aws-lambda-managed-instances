import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as quicksight from 'aws-cdk-lib/aws-quicksight';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';

export interface DeliveryConstructProps {
  /** KMS customer-managed key for encrypting all delivery resources */
  readonly encryptionKey: kms.IKey;

  /** Alerts Kinesis Data Stream (consumed by alert Lambda to publish to SNS) */
  readonly alertsStream: kinesis.IStream;

  /** IAM execution role for the alerts Lambda function */
  readonly snsPublishRole: iam.IRole;

  /** Optional TTL attribute name for DynamoDB table (enables automatic item expiration) */
  readonly ttlAttributeName?: string;

  /** QuickSight principal ARN for data source permissions (e.g., user or group ARN) */
  readonly quickSightPrincipalArn?: string;
}

/**
 * Delivery Construct
 *
 * Creates the downstream delivery infrastructure for the IoT data pipeline:
 * - DynamoDB table for analytics results (partition key: deviceGroupId, sort key: windowEndTimestamp)
 * - S3 data lake bucket for Parquet files (SSE-KMS, lifecycle to Glacier after 90 days)
 * - SNS topic for pipeline alerts (KMS CMK encryption)
 * - Lambda function consuming iot-alerts-stream and publishing to SNS
 * - QuickSight data source configured against the S3 data lake bucket
 *
 * Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.8, 7.1
 */
export class DeliveryConstruct extends Construct {
  /** DynamoDB table for analytics results */
  public readonly analyticsTable: dynamodb.Table;

  /** S3 data lake bucket for Parquet files */
  public readonly dataLakeBucket: s3.Bucket;

  /** SNS topic for pipeline alerts */
  public readonly alertsTopic: sns.Topic;

  /** Lambda function that consumes alerts stream and publishes to SNS */
  public readonly alertsPublisherFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: DeliveryConstructProps) {
    super(scope, id);

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // ---------------------------------------------------------------
    // DynamoDB Analytics Results Table
    // Req 5.1, 5.8, 7.1: On-demand billing, KMS CMK encryption,
    // partition key deviceGroupId (String), sort key windowEndTimestamp (Number)
    // Idempotent writes via conditional PutItem (handled by Flink sink)
    // ---------------------------------------------------------------
    this.analyticsTable = new dynamodb.Table(this, 'AnalyticsResultsTable', {
      partitionKey: {
        name: 'deviceGroupId',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'windowEndTimestamp',
        type: dynamodb.AttributeType.NUMBER,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: props.encryptionKey,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      timeToLiveAttribute: props.ttlAttributeName,
    });

    // ---------------------------------------------------------------
    // S3 Data Lake Bucket
    // Req 5.2, 5.5, 7.1: SSE-KMS with CMK, lifecycle rule to Glacier
    // after 90 days, partitioned by date and sensor type (Parquet files)
    // Configured as QuickSight data source for historical analysis
    // ---------------------------------------------------------------
    this.dataLakeBucket = new s3.Bucket(this, 'DataLakeBucket', {
      bucketName: `iot-data-lake-${account}-${region}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: props.encryptionKey,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          id: 'TransitionToGlacier',
          enabled: true,
          transitions: [
            {
              storageClass: s3.StorageClass.GLACIER,
              transitionAfter: cdk.Duration.days(90),
            },
          ],
        },
      ],
    });

    // ---------------------------------------------------------------
    // SNS Alert Topic
    // Req 5.3, 7.1: KMS CMK encryption for alert notifications
    // Subscriptions (email, SMS, Lambda) configured per environment
    // ---------------------------------------------------------------
    this.alertsTopic = new sns.Topic(this, 'AlertsTopic', {
      topicName: 'iot-pipeline-alerts',
      masterKey: props.encryptionKey,
      displayName: 'IoT Pipeline Alerts',
    });

    // ---------------------------------------------------------------
    // Lambda Function — Alerts Stream Consumer → SNS Publisher
    // Req 5.3: Consumes iot-alerts-stream and publishes to SNS topic
    // Uses the snsPublishRole from security construct (least-privilege)
    // ---------------------------------------------------------------
    this.alertsPublisherFunction = new lambda.Function(this, 'AlertsPublisherFn', {
      functionName: 'iot-alerts-publisher',
      description: 'Consumes alert records from iot-alerts-stream and publishes to SNS topic',
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'index.handler',
      code: lambda.Code.fromInline(this.getAlertsPublisherCode()),
      role: props.snsPublishRole,
      memorySize: 256,
      timeout: cdk.Duration.seconds(30),
      environment: {
        SNS_TOPIC_ARN: this.alertsTopic.topicArn,
      },
    });

    // Event Source Mapping: iot-alerts-stream → alerts publisher Lambda
    this.alertsPublisherFunction.addEventSource(
      new lambdaEventSources.KinesisEventSource(props.alertsStream, {
        batchSize: 10,
        maxBatchingWindow: cdk.Duration.seconds(5),
        startingPosition: lambda.StartingPosition.TRIM_HORIZON,
        reportBatchItemFailures: true,
        retryAttempts: 3,
      }),
    );

    // ---------------------------------------------------------------
    // QuickSight Data Source
    // Req 5.5: S3 bucket configured as QuickSight data source
    // Parquet files with SPICE refresh schedule for historical analysis
    // Only created if a QuickSight principal ARN is provided
    // ---------------------------------------------------------------
    if (props.quickSightPrincipalArn) {
      new quicksight.CfnDataSource(this, 'QuickSightDataSource', {
        awsAccountId: account,
        dataSourceId: 'iot-data-lake-source',
        name: 'IoT Data Lake',
        type: 'S3',
        dataSourceParameters: {
          s3Parameters: {
            manifestFileLocation: {
              bucket: this.dataLakeBucket.bucketName,
              key: 'quicksight-manifest.json',
            },
          },
        },
        permissions: [
          {
            principal: props.quickSightPrincipalArn,
            actions: [
              'quicksight:DescribeDataSource',
              'quicksight:DescribeDataSourcePermissions',
              'quicksight:PassDataSource',
              'quicksight:UpdateDataSource',
              'quicksight:DeleteDataSource',
              'quicksight:UpdateDataSourcePermissions',
            ],
          },
        ],
        sslProperties: {
          disableSsl: false,
        },
      });
    }
  }

  /**
   * Inline Lambda code for the alerts publisher function.
   * Decodes Kinesis records and publishes alert payloads to SNS.
   */
  private getAlertsPublisherCode(): string {
    return `
const { SNSClient, PublishCommand } = require('@aws-sdk/client-sns');

const snsClient = new SNSClient({});
const TOPIC_ARN = process.env.SNS_TOPIC_ARN;

exports.handler = async (event) => {
  const failures = [];

  for (const record of event.Records) {
    try {
      const payload = Buffer.from(record.kinesis.data, 'base64').toString('utf-8');
      const alert = JSON.parse(payload);

      const subject = \`[\${alert.severity?.toUpperCase() || 'ALERT'}] \${alert.alertType || 'Pipeline Alert'} - \${alert.deviceGroupId || 'Unknown Group'}\`;

      await snsClient.send(new PublishCommand({
        TopicArn: TOPIC_ARN,
        Subject: subject.substring(0, 100),
        Message: JSON.stringify(alert, null, 2),
        MessageAttributes: {
          severity: {
            DataType: 'String',
            StringValue: alert.severity || 'info',
          },
          alertType: {
            DataType: 'String',
            StringValue: alert.alertType || 'unknown',
          },
          deviceGroupId: {
            DataType: 'String',
            StringValue: alert.deviceGroupId || 'unknown',
          },
        },
      }));
    } catch (error) {
      console.error(JSON.stringify({
        level: 'ERROR',
        message: 'Failed to publish alert to SNS',
        error: error.message,
        sequenceNumber: record.kinesis.sequenceNumber,
      }));
      failures.push({ itemIdentifier: record.kinesis.sequenceNumber });
    }
  }

  return { batchItemFailures: failures };
};
`.trim();
  }
}
