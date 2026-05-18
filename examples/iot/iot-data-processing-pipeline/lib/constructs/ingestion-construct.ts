import * as iam from 'aws-cdk-lib/aws-iam';
import * as iot from 'aws-cdk-lib/aws-iot';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import { Construct } from 'constructs';

export interface IngestionConstructProps {
  /** Raw telemetry Kinesis stream — IoT Rule writes records here */
  readonly rawTelemetryStream: kinesis.IStream;

  /** IAM role assumed by the IoT Rule to put records into Kinesis */
  readonly iotRuleRole: iam.IRole;
}

/**
 * IoT Core ingestion layer — authenticates devices via X.509 certificates,
 * enforces per-device topic restrictions, and routes telemetry to Kinesis.
 *
 * Device onboarding (X.509 certificate provisioning, thing creation, and
 * certificate attachment) is handled out-of-band — typically via IoT Core
 * fleet provisioning, Just-in-Time Registration (JITR), or a custom
 * registration workflow. This construct defines the IoT policy and topic
 * rule that devices use once they are registered.
 */
export class IngestionConstruct extends Construct {
  /** The IoT Topic Rule that routes telemetry to Kinesis */
  public readonly topicRule: iot.CfnTopicRule;

  /** The IoT Policy that restricts device publish/connect permissions */
  public readonly devicePolicy: iot.CfnPolicy;

  constructor(scope: Construct, id: string, props: IngestionConstructProps) {
    super(scope, id);

    // ---------------------------------------------------------------
    // IoT Policy — restricts each device to its own topic namespace
    // ---------------------------------------------------------------
    // Devices authenticate with X.509 certificates. The policy uses
    // IoT Core policy variables to scope permissions per-thing:
    //   ${iot:Connection.Thing.ThingName} resolves to the thing name
    //   associated with the certificate used to connect.
    this.devicePolicy = new iot.CfnPolicy(this, 'DevicePolicy', {
      policyName: 'IotDeviceTelemetryPolicy',
      policyDocument: {
        Version: '2012-10-17',
        Statement: [
          {
            Effect: 'Allow',
            Action: 'iot:Connect',
            Resource: '*',
            Condition: {
              Bool: {
                'iot:Connection.Thing.IsAttached': 'true',
              },
            },
          },
          {
            Effect: 'Allow',
            Action: 'iot:Publish',
            Resource: {
              'Fn::Sub': 'arn:aws:iot:${AWS::Region}:${AWS::AccountId}:topic/devices/${!iot:Connection.Thing.ThingName}/telemetry',
            },
          },
        ],
      },
    });

    // ---------------------------------------------------------------
    // IoT Topic Rule — routes telemetry from all devices to Kinesis
    // ---------------------------------------------------------------
    // SQL: SELECT * FROM 'devices/+/telemetry'
    //   The '+' wildcard matches any single topic level, so this rule
    //   captures messages from every device publishing to its own
    //   devices/{deviceId}/telemetry topic.
    //
    // Kinesis action:
    //   partitionKey = ${clientid()} ensures records from the same
    //   device land in the same Kinesis shard, preserving per-device
    //   ordering (Req 2.4).
    //
    // Error action:
    //   Republishes failed messages to $aws/rules/errors/TelemetryIngestionRule
    //   so operators can monitor ingestion failures via CloudWatch.
    this.topicRule = new iot.CfnTopicRule(this, 'TelemetryIngestionRule', {
      ruleName: 'TelemetryIngestionRule',
      topicRulePayload: {
        sql: "SELECT * FROM 'devices/+/telemetry'",
        awsIotSqlVersion: '2016-03-23',
        ruleDisabled: false,
        actions: [
          {
            kinesis: {
              streamName: props.rawTelemetryStream.streamName,
              partitionKey: '${clientid()}',
              roleArn: props.iotRuleRole.roleArn,
            },
          },
        ],
        errorAction: {
          republish: {
            topic: '$aws/rules/errors/TelemetryIngestionRule',
            roleArn: props.iotRuleRole.roleArn,
          },
        },
      },
    });
  }
}
