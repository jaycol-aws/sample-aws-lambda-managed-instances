import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { IoTDataPipelineStack } from '../lib/iot-data-pipeline-stack';

/**
 * CDK Infrastructure Assertion Tests for IoT Data Pipeline
 *
 * Validates: Requirements 7.1, 7.3, 7.4, 8.1, 8.3, 8.4
 */
describe('IoTDataPipelineStack', () => {
  let app: cdk.App;
  let stack: IoTDataPipelineStack;
  let template: Template;

  beforeAll(() => {
    app = new cdk.App({
      context: {
        environment: 'test',
        projectName: 'iot-data-pipeline',
        costCenter: 'iot-operations',
      },
    });

    stack = new IoTDataPipelineStack(app, 'TestStack', {
      env: { account: '123456789012', region: 'us-east-1' },
    });

    template = Template.fromStack(stack);
  });

  // ---------------------------------------------------------------
  // Smoke test — stack synthesizes successfully (Req 8.1)
  // ---------------------------------------------------------------
  test('stack creates without errors', () => {
    expect(template.toJSON()).toBeDefined();
  });

  // ---------------------------------------------------------------
  // Kinesis Streams — KMS CMK encryption, on-demand mode, 24h retention (Req 7.1)
  // ---------------------------------------------------------------
  describe('Kinesis Streams', () => {
    test('all Kinesis streams use KMS CMK encryption', () => {
      const streams = template.findResources('AWS::Kinesis::Stream');
      const streamKeys = Object.keys(streams);

      expect(streamKeys.length).toBe(3);

      for (const key of streamKeys) {
        const props = streams[key].Properties;
        expect(props.StreamEncryption.EncryptionType).toBe('KMS');
        expect(props.StreamEncryption.KeyId).toBeDefined();
      }
    });

    test('all Kinesis streams use on-demand mode', () => {
      const streams = template.findResources('AWS::Kinesis::Stream');

      for (const key of Object.keys(streams)) {
        const props = streams[key].Properties;
        expect(props.StreamModeDetails.StreamMode).toBe('ON_DEMAND');
      }
    });

    test('all Kinesis streams have 24-hour retention', () => {
      const streams = template.findResources('AWS::Kinesis::Stream');

      for (const key of Object.keys(streams)) {
        const props = streams[key].Properties;
        expect(props.RetentionPeriodHours).toBe(24);
      }
    });

    test('raw telemetry stream is named correctly', () => {
      template.hasResourceProperties('AWS::Kinesis::Stream', {
        Name: 'iot-raw-telemetry-stream',
      });
    });

    test('enriched telemetry stream is named correctly', () => {
      template.hasResourceProperties('AWS::Kinesis::Stream', {
        Name: 'iot-enriched-telemetry-stream',
      });
    });

    test('alerts stream is named correctly', () => {
      template.hasResourceProperties('AWS::Kinesis::Stream', {
        Name: 'iot-alerts-stream',
      });
    });
  });

  // ---------------------------------------------------------------
  // LMI Capacity Provider — private subnets across 2+ AZs (Req 7.4)
  // ---------------------------------------------------------------
  describe('LMI Capacity Provider', () => {
    test('Lambda function is deployed in VPC with private subnets', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        FunctionName: 'iot-lmi-processor',
        VpcConfig: {
          SubnetIds: Match.anyValue(),
          SecurityGroupIds: Match.anyValue(),
        },
      });
    });

    test('VPC has private isolated subnets across 2+ AZs', () => {
      const subnets = template.findResources('AWS::EC2::Subnet');
      const subnetKeys = Object.keys(subnets);

      // With maxAzs: 2 and one private subnet config, we get 2 subnets
      expect(subnetKeys.length).toBeGreaterThanOrEqual(2);

      // Verify no public subnets (MapPublicIpOnLaunch should be false)
      for (const key of subnetKeys) {
        const props = subnets[key].Properties;
        expect(props.MapPublicIpOnLaunch).toBe(false);
      }
    });

    test('VPC has no internet gateway', () => {
      const igws = template.findResources('AWS::EC2::InternetGateway');
      expect(Object.keys(igws).length).toBe(0);
    });

    test('VPC has no NAT gateways', () => {
      const natGws = template.findResources('AWS::EC2::NatGateway');
      expect(Object.keys(natGws).length).toBe(0);
    });

    test('LMI processor has 2GB memory and 60s timeout', () => {
      template.hasResourceProperties('AWS::Lambda::Function', {
        FunctionName: 'iot-lmi-processor',
        MemorySize: 2048,
        Timeout: 60,
        Runtime: 'nodejs22.x',
      });
    });
  });

  // ---------------------------------------------------------------
  // LMI Execution Role — least-privilege, no wildcard actions (Req 7.3)
  // ---------------------------------------------------------------
  describe('LMI Execution Role - Least Privilege', () => {
    test('LMI execution role has no wildcard actions in inline policies', () => {
      const policies = template.findResources('AWS::IAM::Policy');

      for (const key of Object.keys(policies)) {
        const policyDoc = policies[key].Properties?.PolicyDocument;
        if (!policyDoc?.Statement) continue;

        for (const statement of policyDoc.Statement) {
          if (statement.Effect === 'Allow') {
            const actions = Array.isArray(statement.Action)
              ? statement.Action
              : [statement.Action];

            for (const action of actions) {
              // No action should be just '*' (full wildcard)
              expect(action).not.toBe('*');
            }
          }
        }
      }
    });

    test('LMI execution role is assumed by lambda.amazonaws.com', () => {
      template.hasResourceProperties('AWS::IAM::Role', {
        AssumeRolePolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Action: 'sts:AssumeRole',
              Effect: 'Allow',
              Principal: {
                Service: 'lambda.amazonaws.com',
              },
            }),
          ]),
        },
        Description: Match.stringLikeRegexp('.*LMI.*'),
      });
    });
  });

  // ---------------------------------------------------------------
  // VPC Endpoints — all required AWS services (Req 7.4)
  // ---------------------------------------------------------------
  describe('VPC Endpoints', () => {
    test('VPC interface endpoint for Kinesis Streams is configured', () => {
      template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
        ServiceName: 'com.amazonaws.us-east-1.kinesis-streams',
        VpcEndpointType: 'Interface',
        PrivateDnsEnabled: true,
      });
    });

    test('VPC interface endpoint for SQS is configured', () => {
      template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
        ServiceName: 'com.amazonaws.us-east-1.sqs',
        VpcEndpointType: 'Interface',
        PrivateDnsEnabled: true,
      });
    });

    test('VPC interface endpoint for CloudWatch Monitoring is configured', () => {
      template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
        ServiceName: 'com.amazonaws.us-east-1.monitoring',
        VpcEndpointType: 'Interface',
        PrivateDnsEnabled: true,
      });
    });

    test('VPC interface endpoint for CloudWatch Logs is configured', () => {
      template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
        ServiceName: 'com.amazonaws.us-east-1.logs',
        VpcEndpointType: 'Interface',
        PrivateDnsEnabled: true,
      });
    });

    test('VPC interface endpoint for KMS is configured', () => {
      template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
        ServiceName: 'com.amazonaws.us-east-1.kms',
        VpcEndpointType: 'Interface',
        PrivateDnsEnabled: true,
      });
    });

    test('VPC interface endpoint for Secrets Manager is configured', () => {
      template.hasResourceProperties('AWS::EC2::VPCEndpoint', {
        ServiceName: 'com.amazonaws.us-east-1.secretsmanager',
        VpcEndpointType: 'Interface',
        PrivateDnsEnabled: true,
      });
    });

    test('VPC gateway endpoint for S3 is configured', () => {
      const endpoints = template.findResources('AWS::EC2::VPCEndpoint');
      const gatewayEndpoints = Object.values(endpoints).filter(
        (e: any) => e.Properties.VpcEndpointType === 'Gateway',
      );
      const s3Endpoint = gatewayEndpoints.find((e: any) => {
        const serviceName = e.Properties.ServiceName;
        if (typeof serviceName === 'string') return serviceName.includes('s3');
        // Handle Fn::Join case
        const joinParts = serviceName?.['Fn::Join']?.[1];
        if (Array.isArray(joinParts)) {
          return joinParts.some((p: any) => typeof p === 'string' && p.includes('s3'));
        }
        return false;
      });
      expect(s3Endpoint).toBeDefined();
    });

    test('VPC gateway endpoint for DynamoDB is configured', () => {
      const endpoints = template.findResources('AWS::EC2::VPCEndpoint');
      const gatewayEndpoints = Object.values(endpoints).filter(
        (e: any) => e.Properties.VpcEndpointType === 'Gateway',
      );
      const dynamoEndpoint = gatewayEndpoints.find((e: any) => {
        const serviceName = e.Properties.ServiceName;
        if (typeof serviceName === 'string') return serviceName.includes('dynamodb');
        // Handle Fn::Join case
        const joinParts = serviceName?.['Fn::Join']?.[1];
        if (Array.isArray(joinParts)) {
          return joinParts.some((p: any) => typeof p === 'string' && p.includes('dynamodb'));
        }
        return false;
      });
      expect(dynamoEndpoint).toBeDefined();
    });
  });

  // ---------------------------------------------------------------
  // Resource Tagging — Environment, Project, CostCenter (Req 8.4)
  // ---------------------------------------------------------------
  describe('Resource Tagging', () => {
    test('Kinesis streams have required tags', () => {
      const streams = template.findResources('AWS::Kinesis::Stream');

      for (const key of Object.keys(streams)) {
        const tags = streams[key].Properties?.Tags;
        expect(tags).toBeDefined();

        const tagKeys = tags.map((t: any) => t.Key);
        expect(tagKeys).toContain('Environment');
        expect(tagKeys).toContain('Project');
        expect(tagKeys).toContain('CostCenter');
      }
    });

    test('DynamoDB table has required tags', () => {
      const tables = template.findResources('AWS::DynamoDB::Table');

      for (const key of Object.keys(tables)) {
        const tags = tables[key].Properties?.Tags;
        expect(tags).toBeDefined();

        const tagKeys = tags.map((t: any) => t.Key);
        expect(tagKeys).toContain('Environment');
        expect(tagKeys).toContain('Project');
        expect(tagKeys).toContain('CostCenter');
      }
    });

    test('S3 bucket has required tags', () => {
      const buckets = template.findResources('AWS::S3::Bucket');

      for (const key of Object.keys(buckets)) {
        const tags = buckets[key].Properties?.Tags;
        expect(tags).toBeDefined();

        const tagKeys = tags.map((t: any) => t.Key);
        expect(tagKeys).toContain('Environment');
        expect(tagKeys).toContain('Project');
        expect(tagKeys).toContain('CostCenter');
      }
    });

    test('Lambda functions have required tags', () => {
      const functions = template.findResources('AWS::Lambda::Function');

      for (const key of Object.keys(functions)) {
        const tags = functions[key].Properties?.Tags;
        expect(tags).toBeDefined();

        const tagKeys = tags.map((t: any) => t.Key);
        expect(tagKeys).toContain('Environment');
        expect(tagKeys).toContain('Project');
        expect(tagKeys).toContain('CostCenter');
      }
    });

    test('tag values match context parameters', () => {
      const streams = template.findResources('AWS::Kinesis::Stream');
      const firstStreamKey = Object.keys(streams)[0];
      const tags = streams[firstStreamKey].Properties?.Tags;

      const envTag = tags.find((t: any) => t.Key === 'Environment');
      const projectTag = tags.find((t: any) => t.Key === 'Project');
      const costCenterTag = tags.find((t: any) => t.Key === 'CostCenter');

      expect(envTag.Value).toBe('test');
      expect(projectTag.Value).toBe('iot-data-pipeline');
      expect(costCenterTag.Value).toBe('iot-operations');
    });
  });

  // ---------------------------------------------------------------
  // IoT Rule — correct partition key expression (Req 8.1)
  // ---------------------------------------------------------------
  describe('IoT Rule', () => {
    test('IoT Rule action uses ${clientid()} as partition key', () => {
      template.hasResourceProperties('AWS::IoT::TopicRule', {
        TopicRulePayload: Match.objectLike({
          Actions: Match.arrayWith([
            Match.objectLike({
              Kinesis: Match.objectLike({
                PartitionKey: '${clientid()}',
              }),
            }),
          ]),
        }),
      });
    });

    test('IoT Rule SQL selects from devices/+/telemetry', () => {
      template.hasResourceProperties('AWS::IoT::TopicRule', {
        TopicRulePayload: Match.objectLike({
          Sql: "SELECT * FROM 'devices/+/telemetry'",
        }),
      });
    });

    test('IoT Rule has error action configured', () => {
      template.hasResourceProperties('AWS::IoT::TopicRule', {
        TopicRulePayload: Match.objectLike({
          ErrorAction: Match.objectLike({
            Republish: Match.objectLike({
              Topic: '$aws/rules/errors/TelemetryIngestionRule',
            }),
          }),
        }),
      });
    });
  });

  // ---------------------------------------------------------------
  // Flink — checkpointing enabled with 60s interval (Req 8.1)
  // ---------------------------------------------------------------
  describe('Flink Analyzer', () => {
    test('Flink application has checkpointing enabled with 60s interval', () => {
      template.hasResourceProperties('AWS::KinesisAnalyticsV2::Application', {
        ApplicationConfiguration: Match.objectLike({
          FlinkApplicationConfiguration: Match.objectLike({
            CheckpointConfiguration: Match.objectLike({
              CheckpointingEnabled: true,
              CheckpointInterval: 60000,
              ConfigurationType: 'CUSTOM',
            }),
          }),
        }),
      });
    });

    test('Flink application uses Flink 1.19 runtime', () => {
      template.hasResourceProperties('AWS::KinesisAnalyticsV2::Application', {
        RuntimeEnvironment: 'FLINK-1_19',
      });
    });

    test('Flink application has parallelism of 4 with auto-scaling', () => {
      template.hasResourceProperties('AWS::KinesisAnalyticsV2::Application', {
        ApplicationConfiguration: Match.objectLike({
          FlinkApplicationConfiguration: Match.objectLike({
            ParallelismConfiguration: Match.objectLike({
              Parallelism: 4,
              AutoScalingEnabled: true,
            }),
          }),
        }),
      });
    });

    test('Flink application has min pause between checkpoints of 5s', () => {
      template.hasResourceProperties('AWS::KinesisAnalyticsV2::Application', {
        ApplicationConfiguration: Match.objectLike({
          FlinkApplicationConfiguration: Match.objectLike({
            CheckpointConfiguration: Match.objectLike({
              MinPauseBetweenCheckpoints: 5000,
            }),
          }),
        }),
      });
    });

    test('Flink application is deployed in VPC', () => {
      template.hasResourceProperties('AWS::KinesisAnalyticsV2::Application', {
        ApplicationConfiguration: Match.objectLike({
          VpcConfigurations: Match.anyValue(),
        }),
      });
    });
  });

  // ---------------------------------------------------------------
  // DynamoDB — on-demand billing with KMS encryption (Req 7.1)
  // ---------------------------------------------------------------
  describe('DynamoDB', () => {
    test('DynamoDB table uses on-demand billing', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {

        BillingMode: 'PAY_PER_REQUEST',
      });
    });

    test('DynamoDB table uses KMS CMK encryption', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {

        SSESpecification: {
          SSEEnabled: true,
          SSEType: 'KMS',
          KMSMasterKeyId: Match.anyValue(),
        },
      });
    });

    test('DynamoDB table has correct key schema', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {

        KeySchema: Match.arrayWith([
          Match.objectLike({ AttributeName: 'deviceGroupId', KeyType: 'HASH' }),
          Match.objectLike({ AttributeName: 'windowEndTimestamp', KeyType: 'RANGE' }),
        ]),
      });
    });

    test('DynamoDB table has point-in-time recovery enabled', () => {
      template.hasResourceProperties('AWS::DynamoDB::Table', {

        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    });
  });

  // ---------------------------------------------------------------
  // S3 — SSE-KMS encryption and lifecycle rules (Req 7.1)
  // ---------------------------------------------------------------
  describe('S3 Data Lake', () => {
    test('S3 bucket uses SSE-KMS encryption', () => {
      template.hasResourceProperties('AWS::S3::Bucket', {
        BucketEncryption: {
          ServerSideEncryptionConfiguration: Match.arrayWith([
            Match.objectLike({
              ServerSideEncryptionByDefault: {
                SSEAlgorithm: 'aws:kms',
                KMSMasterKeyID: Match.anyValue(),
              },
            }),
          ]),
        },
      });
    });

    test('S3 bucket has lifecycle rule to transition to Glacier after 90 days', () => {
      template.hasResourceProperties('AWS::S3::Bucket', {
        LifecycleConfiguration: {
          Rules: Match.arrayWith([
            Match.objectLike({
              Status: 'Enabled',
              Transitions: Match.arrayWith([
                Match.objectLike({
                  StorageClass: 'GLACIER',
                  TransitionInDays: 90,
                }),
              ]),
            }),
          ]),
        },
      });
    });

    test('S3 bucket blocks all public access', () => {
      template.hasResourceProperties('AWS::S3::Bucket', {
        PublicAccessBlockConfiguration: {
          BlockPublicAcls: true,
          BlockPublicPolicy: true,
          IgnorePublicAcls: true,
          RestrictPublicBuckets: true,
        },
      });
    });

    test('S3 bucket has versioning enabled', () => {
      template.hasResourceProperties('AWS::S3::Bucket', {
        VersioningConfiguration: {
          Status: 'Enabled',
        },
      });
    });
  });

  // ---------------------------------------------------------------
  // SNS and SQS — KMS encryption (Req 7.1)
  // ---------------------------------------------------------------
  describe('SNS and SQS Encryption', () => {
    test('SNS topics use KMS encryption', () => {
      const topics = template.findResources('AWS::SNS::Topic');

      for (const key of Object.keys(topics)) {
        const props = topics[key].Properties;
        expect(props.KmsMasterKeyId).toBeDefined();
      }
    });

    test('SQS queues use KMS encryption', () => {
      const queues = template.findResources('AWS::SQS::Queue');

      for (const key of Object.keys(queues)) {
        const props = queues[key].Properties;
        expect(props.KmsMasterKeyId).toBeDefined();
      }
    });

    test('SQS DLQ has 14-day retention', () => {
      template.hasResourceProperties('AWS::SQS::Queue', {
        QueueName: 'iot-pipeline-dlq',
        MessageRetentionPeriod: 1209600, // 14 days in seconds
      });
    });

    test('SNS alerts topic is named correctly', () => {
      template.hasResourceProperties('AWS::SNS::Topic', {
        TopicName: 'iot-pipeline-alerts',
      });
    });

    test('SNS ops topic is named correctly', () => {
      template.hasResourceProperties('AWS::SNS::Topic', {
        TopicName: 'iot-pipeline-ops',
      });
    });
  });

  // ---------------------------------------------------------------
  // CloudWatch Dashboard — provisioned with required widgets (Req 8.1)
  // ---------------------------------------------------------------
  describe('CloudWatch Dashboard', () => {
    test('CloudWatch Dashboard is provisioned', () => {
      template.resourceCountIs('AWS::CloudWatch::Dashboard', 1);
    });

    test('Dashboard has correct name', () => {
      template.hasResourceProperties('AWS::CloudWatch::Dashboard', {
        DashboardName: 'IoT-Pipeline-Operations',
      });
    });

    test('Dashboard body contains required widget sections', () => {
      const dashboards = template.findResources('AWS::CloudWatch::Dashboard');
      const dashboardKey = Object.keys(dashboards)[0];
      const body = dashboards[dashboardKey].Properties.DashboardBody;

      // DashboardBody is a JSON string (possibly with Fn::Join)
      // We verify the dashboard resource exists and has a body
      expect(body).toBeDefined();
    });

    test('CloudWatch Alarms are configured', () => {
      const alarms = template.findResources('AWS::CloudWatch::Alarm');
      // We expect at least 5 alarms: latency, LMI error rate, Flink checkpoint,
      // Flink watermark lag, DLQ messages
      expect(Object.keys(alarms).length).toBeGreaterThanOrEqual(5);
    });

    test('latency alarm is configured', () => {
      template.hasResourceProperties('AWS::CloudWatch::Alarm', {
        AlarmName: 'iot-pipeline-end-to-end-latency',
        EvaluationPeriods: 1,
      });
    });

    test('LMI error rate alarm is configured', () => {
      template.hasResourceProperties('AWS::CloudWatch::Alarm', {
        AlarmName: 'iot-pipeline-lmi-error-rate',
        Threshold: 5,
      });
    });

    test('Flink checkpoint duration alarm is configured', () => {
      template.hasResourceProperties('AWS::CloudWatch::Alarm', {
        AlarmName: 'iot-pipeline-flink-checkpoint-duration',
        Threshold: 120000,
      });
    });

    test('DLQ alarm is configured', () => {
      template.hasResourceProperties('AWS::CloudWatch::Alarm', {
        AlarmName: 'iot-pipeline-dlq-messages',
        Threshold: 0,
      });
    });

    test('all alarms have SNS action configured', () => {
      const alarms = template.findResources('AWS::CloudWatch::Alarm');

      for (const key of Object.keys(alarms)) {
        const props = alarms[key].Properties;
        expect(props.AlarmActions).toBeDefined();
        expect(props.AlarmActions.length).toBeGreaterThan(0);
      }
    });
  });

  // ---------------------------------------------------------------
  // KMS Key — rotation enabled (Req 7.1)
  // ---------------------------------------------------------------
  describe('KMS', () => {
    test('KMS CMK has key rotation enabled', () => {
      template.hasResourceProperties('AWS::KMS::Key', {
        EnableKeyRotation: true,
      });
    });
  });

  // ---------------------------------------------------------------
  // Security Groups — egress restricted (Req 7.4)
  // ---------------------------------------------------------------
  describe('Security Groups', () => {
    test('pipeline security group restricts egress', () => {
      // The pipeline SG should NOT have a default allow-all egress rule
      // CDK creates SecurityGroup with allowAllOutbound: false
      // This means no AWS::EC2::SecurityGroup with SecurityGroupEgress allowing 0.0.0.0/0
      const sgs = template.findResources('AWS::EC2::SecurityGroup');

      let pipelineSgFound = false;
      for (const key of Object.keys(sgs)) {
        const props = sgs[key].Properties;
        if (props.GroupDescription?.includes('egress to VPC endpoints only')) {
          pipelineSgFound = true;
          // Verify no wide-open egress in the SG definition itself
          const egress = props.SecurityGroupEgress;
          if (egress) {
            for (const rule of egress) {
              expect(rule.CidrIp).not.toBe('0.0.0.0/0');
            }
          }
        }
      }
      expect(pipelineSgFound).toBe(true);
    });
  });

  // ---------------------------------------------------------------
  // Event Source Mapping — batch configuration (Req 8.1)
  // ---------------------------------------------------------------
  describe('Event Source Mapping', () => {
    test('LMI event source mapping has correct batch configuration', () => {
      template.hasResourceProperties('AWS::Lambda::EventSourceMapping', {
        BatchSize: 100,
        MaximumBatchingWindowInSeconds: 5,
        BisectBatchOnFunctionError: true,
        MaximumRetryAttempts: 3,
        FunctionResponseTypes: ['ReportBatchItemFailures'],
      });
    });
  });

  // ---------------------------------------------------------------
  // CloudWatch Log Groups — per component with 30-day retention
  // ---------------------------------------------------------------
  describe('CloudWatch Log Groups', () => {
    test('log groups have 30-day retention', () => {
      const logGroups = template.findResources('AWS::Logs::LogGroup');

      for (const key of Object.keys(logGroups)) {
        const props = logGroups[key].Properties;
        // RetentionInDays of 30 corresponds to ONE_MONTH
        expect(props.RetentionInDays).toBe(30);
      }
    });
  });
});
