import * as cdk from 'aws-cdk-lib';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as kms from 'aws-cdk-lib/aws-kms';
import { Construct } from 'constructs';

export interface KinesisConstructProps {
  /** KMS customer-managed key for encrypting all Kinesis streams */
  readonly encryptionKey: kms.IKey;
}

export class KinesisConstruct extends Construct {
  /** Raw telemetry stream — ingests records from IoT Core via IoT Rule action.
   *  Partition key: IoT device identifier (set at write time by IoT Rule using ${clientid()}).
   */
  public readonly rawTelemetryStream: kinesis.Stream;

  /** Enriched telemetry stream — receives validated/enriched records from LMI processor.
   *  Partition key: device group identifier (set at write time by LMI processor to optimize Flink window aggregation).
   */
  public readonly enrichedTelemetryStream: kinesis.Stream;

  /** Alerts stream — receives threshold-breach alerts emitted by Flink analyzer. */
  public readonly alertsStream: kinesis.Stream;

  constructor(scope: Construct, id: string, props: KinesisConstructProps) {
    super(scope, id);

    const streamDefaults: Partial<kinesis.StreamProps> = {
      streamMode: kinesis.StreamMode.ON_DEMAND,
      retentionPeriod: cdk.Duration.hours(24),
      encryption: kinesis.StreamEncryption.KMS,
      encryptionKey: props.encryptionKey,
    };

    // Req 2.1, 2.2, 7.1 — Raw telemetry buffer between IoT Core and LMI processor
    this.rawTelemetryStream = new kinesis.Stream(this, 'RawTelemetryStream', {
      streamName: 'iot-raw-telemetry-stream',
      ...streamDefaults,
    });

    // Req 2.1, 2.2, 7.1 — Enriched records for Flink consumption
    this.enrichedTelemetryStream = new kinesis.Stream(this, 'EnrichedTelemetryStream', {
      streamName: 'iot-enriched-telemetry-stream',
      ...streamDefaults,
    });

    // Req 2.1, 2.2, 7.1 — Dedicated alerts stream for threshold-breach alerts
    this.alertsStream = new kinesis.Stream(this, 'AlertsStream', {
      streamName: 'iot-alerts-stream',
      ...streamDefaults,
    });
  }
}
