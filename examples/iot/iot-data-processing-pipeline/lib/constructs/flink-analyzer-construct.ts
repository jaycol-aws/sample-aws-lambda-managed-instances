import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as kinesisanalytics from 'aws-cdk-lib/aws-kinesisanalyticsv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3Assets from 'aws-cdk-lib/aws-s3-assets';
import { Construct } from 'constructs';
import * as path from 'path';

export interface FlinkAnalyzerConstructProps {
  /** VPC with private subnets for Flink application deployment */
  readonly vpc: ec2.IVpc;

  /** Security group restricting egress to VPC endpoints only */
  readonly securityGroup: ec2.ISecurityGroup;

  /** IAM execution role for the Flink application */
  readonly flinkExecutionRole: iam.IRole;

  /** Enriched telemetry Kinesis stream (Flink input source) */
  readonly enrichedTelemetryStream: kinesis.IStream;

  /** Alerts Kinesis stream (Flink output for threshold-breach alerts) */
  readonly alertsStream: kinesis.IStream;

  /** DynamoDB analytics results table (Flink output for aggregated statistics) */
  readonly analyticsTable: dynamodb.ITable;

  /** S3 data lake bucket (Flink output for Parquet files) */
  readonly dataLakeBucket: s3.IBucket;

  /** Window duration in seconds for tumbling window aggregation (default: 60) */
  readonly windowDurationSeconds?: number;
}

/**
 * Flink Analyzer Construct
 *
 * Creates the Managed Service for Apache Flink application:
 * - CfnApplication (L1 construct) with Flink 1.19 runtime
 * - Kinesis connector for iot-enriched-telemetry-stream input
 * - Parallelism: 4, auto-scaling enabled
 * - Checkpointing: 60s interval, EXACTLY_ONCE mode, min pause 5s
 * - VPC deployment in same private subnets as LMI
 * - Flink execution role with least-privilege permissions
 * - CloudWatch log group for application logging
 *
 * Requirements: 4.1, 4.5, 4.6, 4.7
 */
export class FlinkAnalyzerConstruct extends Construct {
  /** The Managed Flink CfnApplication resource */
  public readonly flinkApplication: kinesisanalytics.CfnApplication;

  /** CloudWatch log group for Flink application logs */
  public readonly logGroup: logs.LogGroup;

  constructor(scope: Construct, id: string, props: FlinkAnalyzerConstructProps) {
    super(scope, id);

    const region = cdk.Stack.of(this).region;
    const account = cdk.Stack.of(this).account;
    const windowDuration = props.windowDurationSeconds ?? 60;

    // ---------------------------------------------------------------
    // CloudWatch Log Group for Flink application logging
    // Uses a distinct name from the monitoring construct's log group
    // ---------------------------------------------------------------
    this.logGroup = new logs.LogGroup(this, 'FlinkLogGroup', {
      logGroupName: '/iot-pipeline/flink-analyzer/application',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ---------------------------------------------------------------
    // S3 Asset for Flink application JAR
    // The JAR is built from the flink/ directory via Maven
    // ---------------------------------------------------------------
    const flinkJarAsset = new s3Assets.Asset(this, 'FlinkJarAsset', {
      path: path.join(__dirname, '../../flink/target/flink-analyzer-1.0.0.jar'),
    });

    // Grant the Flink execution role read access to the asset bucket
    flinkJarAsset.grantRead(props.flinkExecutionRole);

    // ---------------------------------------------------------------
    // Managed Service for Apache Flink Application (CfnApplication)
    // Req 4.1: Flink 1.19 runtime with Kinesis connector
    // Req 4.5: Checkpointing 60s interval, EXACTLY_ONCE mode
    // Req 4.6: Restore from latest checkpoint on failure
    // Req 4.7: Parallelism 4 KPUs minimum, auto-scaling enabled
    // ---------------------------------------------------------------
    const privateSubnets = props.vpc.selectSubnets({
      subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
    });

    this.flinkApplication = new kinesisanalytics.CfnApplication(this, 'FlinkAnalyzerApp', {
      applicationName: 'iot-flink-analyzer',
      applicationDescription: 'Real-time analytics for IoT enriched telemetry - rolling statistics, anomaly detection, threshold alerting',
      runtimeEnvironment: 'FLINK-1_19',
      serviceExecutionRole: props.flinkExecutionRole.roleArn,

      applicationConfiguration: {
        // Application code configuration — JAR from S3
        applicationCodeConfiguration: {
          codeContent: {
            s3ContentLocation: {
              bucketArn: flinkJarAsset.bucket.bucketArn,
              fileKey: flinkJarAsset.s3ObjectKey,
            },
          },
          codeContentType: 'ZIPFILE',
        },

        // Flink application configuration — checkpointing, parallelism
        flinkApplicationConfiguration: {
          checkpointConfiguration: {
            configurationType: 'CUSTOM',
            checkpointingEnabled: true,
            checkpointInterval: 60000, // 60 seconds
            minPauseBetweenCheckpoints: 5000, // 5 seconds
          },
          monitoringConfiguration: {
            configurationType: 'CUSTOM',
            metricsLevel: 'APPLICATION',
            logLevel: 'INFO',
          },
          parallelismConfiguration: {
            configurationType: 'CUSTOM',
            parallelism: 4,
            parallelismPerKpu: 1,
            autoScalingEnabled: true,
          },
        },

        // Environment properties — runtime configuration for the Flink app
        environmentProperties: {
          propertyGroups: [
            {
              propertyGroupId: 'FlinkApplicationProperties',
              propertyMap: {
                ENRICHED_STREAM_NAME: props.enrichedTelemetryStream.streamName,
                ALERTS_STREAM_NAME: props.alertsStream.streamName,
                DYNAMODB_TABLE_NAME: props.analyticsTable.tableName,
                S3_OUTPUT_PATH: `s3://${props.dataLakeBucket.bucketName}/data/`,
                AWS_REGION: region,
                WINDOW_DURATION_SECONDS: String(windowDuration),
              },
            },
          ],
        },

        // VPC configuration — deploy in same private subnets as LMI
        vpcConfigurations: [
          {
            subnetIds: privateSubnets.subnetIds,
            securityGroupIds: [props.securityGroup.securityGroupId],
          },
        ],
      },
    });

    // ---------------------------------------------------------------
    // CloudWatch Logging Configuration
    // Associate the log group with the Flink application.
    // Managed Flink requires a specific log stream ARN (no wildcards).
    // ---------------------------------------------------------------
    const logStream = new logs.LogStream(this, 'FlinkLogStream', {
      logGroup: this.logGroup,
      logStreamName: 'flink-analyzer',
    });

    const loggingOption = new kinesisanalytics.CfnApplicationCloudWatchLoggingOption(this, 'FlinkLogging', {
      applicationName: this.flinkApplication.applicationName!,
      cloudWatchLoggingOption: {
        logStreamArn: `arn:aws:logs:${region}:${account}:log-group:${this.logGroup.logGroupName}:log-stream:${logStream.logStreamName}`,
      },
    });

    // Ensure the logging option is created after the application
    loggingOption.addDependency(this.flinkApplication);
  }
}
