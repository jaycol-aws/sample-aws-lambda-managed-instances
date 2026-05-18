import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import { Construct } from 'constructs';

export interface SecurityConstructProps {
  /** Environment name for resource naming (e.g., 'prod', 'dev') */
  readonly environment: string;
}

export class SecurityConstruct extends Construct {
  /** KMS customer-managed key shared across all pipeline resources */
  public readonly pipelineKey: kms.Key;

  /** VPC with private subnets across 2+ AZs, no internet gateway */
  public readonly vpc: ec2.Vpc;

  /** Security group for pipeline resources — egress restricted to VPC endpoints */
  public readonly pipelineSecurityGroup: ec2.SecurityGroup;

  /** Security group attached to VPC interface endpoints */
  public readonly endpointSecurityGroup: ec2.SecurityGroup;

  /** IAM role for LMI Lambda execution */
  public readonly lmiExecutionRole: iam.Role;

  /** IAM role for Flink application execution */
  public readonly flinkExecutionRole: iam.Role;

  /** IAM role for IoT Rule Kinesis action */
  public readonly iotRuleRole: iam.Role;

  /** IAM role for SNS publish from alerts Lambda */
  public readonly snsPublishRole: iam.Role;

  /** VPC interface endpoint for Kinesis Streams */
  public readonly kinesisEndpoint: ec2.InterfaceVpcEndpoint;

  /** VPC interface endpoint for SQS */
  public readonly sqsEndpoint: ec2.InterfaceVpcEndpoint;

  /** VPC interface endpoint for CloudWatch Monitoring */
  public readonly monitoringEndpoint: ec2.InterfaceVpcEndpoint;

  /** VPC interface endpoint for CloudWatch Logs */
  public readonly logsEndpoint: ec2.InterfaceVpcEndpoint;

  /** VPC interface endpoint for KMS */
  public readonly kmsEndpoint: ec2.InterfaceVpcEndpoint;

  /** VPC interface endpoint for Secrets Manager */
  public readonly secretsManagerEndpoint: ec2.InterfaceVpcEndpoint;

  /** VPC gateway endpoint for S3 */
  public readonly s3Endpoint: ec2.GatewayVpcEndpoint;

  /** VPC gateway endpoint for DynamoDB */
  public readonly dynamoDbEndpoint: ec2.GatewayVpcEndpoint;

  constructor(scope: Construct, id: string, props: SecurityConstructProps) {
    super(scope, id);

    const region = cdk.Stack.of(this).region;
    const account = cdk.Stack.of(this).account;

    // ---------------------------------------------------------------
    // KMS Customer-Managed Key — shared across all pipeline resources
    // ---------------------------------------------------------------
    this.pipelineKey = new kms.Key(this, 'PipelineCmk', {
      alias: `iot-pipeline-cmk-${props.environment}`,
      description: 'Customer-managed key for IoT data pipeline encryption at rest',
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ---------------------------------------------------------------
    // VPC — private subnets across 2+ AZs, NO internet gateway
    // ---------------------------------------------------------------
    this.vpc = new ec2.Vpc(this, 'PipelineVpc', {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'Private',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
    });

    // ---------------------------------------------------------------
    // Security Groups
    // ---------------------------------------------------------------

    // Security group for VPC interface endpoints — allows inbound HTTPS
    this.endpointSecurityGroup = new ec2.SecurityGroup(this, 'EndpointSg', {
      vpc: this.vpc,
      description: 'Security group for VPC interface endpoints',
      allowAllOutbound: false,
    });
    this.endpointSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      'Allow HTTPS from VPC CIDR',
    );

    // Security group for pipeline resources — egress only to endpoint ENIs
    this.pipelineSecurityGroup = new ec2.SecurityGroup(this, 'PipelineSg', {
      vpc: this.vpc,
      description: 'Security group for pipeline resources - egress to VPC endpoints only',
      allowAllOutbound: false,
    });
    this.pipelineSecurityGroup.addEgressRule(
      this.endpointSecurityGroup,
      ec2.Port.tcp(443),
      'Allow HTTPS egress to VPC endpoint ENIs',
    );

    // ---------------------------------------------------------------
    // VPC Interface Endpoints
    // ---------------------------------------------------------------
    const privateSubnets = this.vpc.selectSubnets({
      subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
    });

    const interfaceEndpointProps: Omit<ec2.InterfaceVpcEndpointOptions, 'service'> = {
      privateDnsEnabled: true,
      subnets: privateSubnets,
      securityGroups: [this.endpointSecurityGroup],
    };

    this.kinesisEndpoint = this.vpc.addInterfaceEndpoint('KinesisEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.KINESIS_STREAMS,
      ...interfaceEndpointProps,
    });

    this.sqsEndpoint = this.vpc.addInterfaceEndpoint('SqsEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SQS,
      ...interfaceEndpointProps,
    });

    this.monitoringEndpoint = this.vpc.addInterfaceEndpoint('MonitoringEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_MONITORING,
      ...interfaceEndpointProps,
    });

    this.logsEndpoint = this.vpc.addInterfaceEndpoint('LogsEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
      ...interfaceEndpointProps,
    });

    this.kmsEndpoint = this.vpc.addInterfaceEndpoint('KmsEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.KMS,
      ...interfaceEndpointProps,
    });

    this.secretsManagerEndpoint = this.vpc.addInterfaceEndpoint('SecretsManagerEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
      ...interfaceEndpointProps,
    });

    // ---------------------------------------------------------------
    // VPC Gateway Endpoints
    // ---------------------------------------------------------------
    this.s3Endpoint = this.vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
      subnets: [privateSubnets],
    });

    this.dynamoDbEndpoint = this.vpc.addGatewayEndpoint('DynamoDbEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
      subnets: [privateSubnets],
    });

    // ---------------------------------------------------------------
    // IAM Roles — least-privilege with placeholder ARN patterns
    // (Actual resource ARNs will be wired in Task 11.1)
    // ---------------------------------------------------------------

    // LMI Execution Role
    this.lmiExecutionRole = new iam.Role(this, 'LmiExecutionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Execution role for LMI processor Lambda - least-privilege',
    });

    // Read from raw Kinesis stream
    this.lmiExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KinesisReadRawStream',
      effect: iam.Effect.ALLOW,
      actions: [
        'kinesis:GetRecords',
        'kinesis:GetShardIterator',
        'kinesis:DescribeStream',
        'kinesis:DescribeStreamSummary',
        'kinesis:ListShards',
        'kinesis:SubscribeToShard',
      ],
      resources: [`arn:aws:kinesis:${region}:${account}:stream/iot-raw-telemetry-stream`],
    }));

    // Write to enriched Kinesis stream
    this.lmiExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KinesisWriteEnrichedStream',
      effect: iam.Effect.ALLOW,
      actions: [
        'kinesis:PutRecord',
        'kinesis:PutRecords',
      ],
      resources: [`arn:aws:kinesis:${region}:${account}:stream/iot-enriched-telemetry-stream`],
    }));

    // Write to SQS DLQ
    this.lmiExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'SqsDlqWrite',
      effect: iam.Effect.ALLOW,
      actions: [
        'sqs:SendMessage',
      ],
      resources: [`arn:aws:sqs:${region}:${account}:iot-pipeline-dlq`],
    }));

    // CloudWatch metrics and logs
    this.lmiExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchMetricsAndLogs',
      effect: iam.Effect.ALLOW,
      actions: [
        'cloudwatch:PutMetricData',
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
      ],
      resources: ['*'],
      conditions: {
        StringEquals: {
          'aws:RequestedRegion': region,
        },
      },
    }));

    // KMS decrypt/encrypt for pipeline CMK
    this.lmiExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KmsAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'kms:Decrypt',
        'kms:GenerateDataKey',
      ],
      resources: [this.pipelineKey.keyArn],
    }));

    // VPC networking permissions for ENI management
    this.lmiExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'VpcNetworking',
      effect: iam.Effect.ALLOW,
      actions: [
        'ec2:CreateNetworkInterface',
        'ec2:DescribeNetworkInterfaces',
        'ec2:DeleteNetworkInterface',
      ],
      resources: ['*'],
    }));

    // Flink Execution Role
    this.flinkExecutionRole = new iam.Role(this, 'FlinkExecutionRole', {
      assumedBy: new iam.ServicePrincipal('kinesisanalytics.amazonaws.com'),
      description: 'Execution role for Managed Apache Flink application',
    });

    // Read from enriched Kinesis stream
    this.flinkExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KinesisReadEnrichedStream',
      effect: iam.Effect.ALLOW,
      actions: [
        'kinesis:GetRecords',
        'kinesis:GetShardIterator',
        'kinesis:DescribeStream',
        'kinesis:DescribeStreamSummary',
        'kinesis:ListShards',
        'kinesis:SubscribeToShard',
      ],
      resources: [`arn:aws:kinesis:${region}:${account}:stream/iot-enriched-telemetry-stream`],
    }));

    // Write to alerts Kinesis stream
    this.flinkExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KinesisWriteAlertsStream',
      effect: iam.Effect.ALLOW,
      actions: [
        'kinesis:PutRecord',
        'kinesis:PutRecords',
        'kinesis:DescribeStream',
      ],
      resources: [`arn:aws:kinesis:${region}:${account}:stream/iot-alerts-stream`],
    }));

    // Write to DynamoDB analytics table
    this.flinkExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DynamoDbWrite',
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:PutItem',
        'dynamodb:BatchWriteItem',
        'dynamodb:DescribeTable',
      ],
      resources: [`arn:aws:dynamodb:${region}:${account}:table/iot-analytics-results`],
    }));

    // Write to S3 data lake
    this.flinkExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3DataLakeWrite',
      effect: iam.Effect.ALLOW,
      actions: [
        's3:PutObject',
        's3:GetBucketLocation',
        's3:ListBucket',
      ],
      resources: [
        `arn:aws:s3:::iot-data-lake-${account}-${region}`,
        `arn:aws:s3:::iot-data-lake-${account}-${region}/*`,
      ],
    }));

    // CloudWatch metrics and logs for Flink
    this.flinkExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchMetricsAndLogs',
      effect: iam.Effect.ALLOW,
      actions: [
        'cloudwatch:PutMetricData',
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
        'logs:DescribeLogGroups',
        'logs:DescribeLogStreams',
      ],
      resources: ['*'],
      conditions: {
        StringEquals: {
          'aws:RequestedRegion': region,
        },
      },
    }));

    // KMS access for Flink
    this.flinkExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KmsAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'kms:Decrypt',
        'kms:GenerateDataKey',
      ],
      resources: [this.pipelineKey.keyArn],
    }));

    // VPC networking for Flink
    this.flinkExecutionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'VpcNetworking',
      effect: iam.Effect.ALLOW,
      actions: [
        'ec2:DescribeVpcs',
        'ec2:DescribeSubnets',
        'ec2:DescribeSecurityGroups',
        'ec2:DescribeDhcpOptions',
        'ec2:CreateNetworkInterface',
        'ec2:CreateNetworkInterfacePermission',
        'ec2:DescribeNetworkInterfaces',
        'ec2:DeleteNetworkInterface',
      ],
      resources: ['*'],
    }));

    // IoT Rule Role — kinesis:PutRecord on raw stream only
    this.iotRuleRole = new iam.Role(this, 'IoTRuleRole', {
      assumedBy: new iam.ServicePrincipal('iot.amazonaws.com'),
      description: 'Role for IoT Rule to write to raw Kinesis stream',
    });

    this.iotRuleRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KinesisPutRecordRawStream',
      effect: iam.Effect.ALLOW,
      actions: [
        'kinesis:PutRecord',
      ],
      resources: [`arn:aws:kinesis:${region}:${account}:stream/iot-raw-telemetry-stream`],
    }));

    // SNS Publish Role — scoped to the alerts topic
    this.snsPublishRole = new iam.Role(this, 'SnsPublishRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Role for alerts Lambda to publish to SNS topic',
    });

    this.snsPublishRole.addToPolicy(new iam.PolicyStatement({
      sid: 'SnsPublishAlerts',
      effect: iam.Effect.ALLOW,
      actions: [
        'sns:Publish',
      ],
      resources: [`arn:aws:sns:${region}:${account}:iot-pipeline-alerts`],
    }));

    // KMS access for SNS publish role
    this.snsPublishRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KmsAccess',
      effect: iam.Effect.ALLOW,
      actions: [
        'kms:Decrypt',
        'kms:GenerateDataKey',
      ],
      resources: [this.pipelineKey.keyArn],
    }));

    // Kinesis read for alerts stream (Lambda consuming alerts)
    this.snsPublishRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KinesisReadAlertsStream',
      effect: iam.Effect.ALLOW,
      actions: [
        'kinesis:GetRecords',
        'kinesis:GetShardIterator',
        'kinesis:DescribeStream',
        'kinesis:DescribeStreamSummary',
        'kinesis:ListShards',
        'kinesis:SubscribeToShard',
      ],
      resources: [`arn:aws:kinesis:${region}:${account}:stream/iot-alerts-stream`],
    }));

    // CloudWatch logs for SNS publish Lambda
    this.snsPublishRole.addToPolicy(new iam.PolicyStatement({
      sid: 'CloudWatchLogs',
      effect: iam.Effect.ALLOW,
      actions: [
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
      ],
      resources: ['*'],
      conditions: {
        StringEquals: {
          'aws:RequestedRegion': region,
        },
      },
    }));
  }
}
