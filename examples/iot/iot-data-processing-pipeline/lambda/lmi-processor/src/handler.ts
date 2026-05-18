/**
 * Lambda batch handler for the LMI Processor.
 *
 * Processes Kinesis event batches:
 * - Decodes base64 Kinesis records and parses JSON
 * - Validates each record against the telemetry schema
 * - Enriches valid records and writes them to the enriched Kinesis stream
 * - Writes failed records to SQS DLQ with error details
 * - Returns batchItemFailures for partial batch response
 * - Emits structured JSON logs with correlation ID for every record
 * - Publishes custom CloudWatch metrics
 *
 * Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.7, 3.8, 6.2, 6.4
 */

import { KinesisClient, PutRecordCommand } from '@aws-sdk/client-kinesis';
import { SQSClient, SendMessageCommand } from '@aws-sdk/client-sqs';
import { CloudWatchClient, PutMetricDataCommand } from '@aws-sdk/client-cloudwatch';
import {
  KinesisEvent,
  KinesisEventRecord,
  KinesisStreamHandlerResponse,
  DLQRecord,
  DeviceRegistry,
} from './types';
import { validateTelemetryRecord } from './validator';
import { enrichRecord, generateCorrelationId, epochMillisToIso8601, EnrichmentError } from './enricher';
import { StubDeviceRegistry } from './device-registry';

// --- Configuration from environment variables ---

const ENRICHED_STREAM_NAME = process.env.ENRICHED_STREAM_NAME || 'iot-enriched-telemetry-stream';
const DLQ_URL = process.env.DLQ_URL || '';
const SOURCE_STREAM = process.env.SOURCE_STREAM || 'iot-raw-telemetry-stream';
const METRICS_NAMESPACE = process.env.METRICS_NAMESPACE || 'IoTPipeline/LMI';

// --- AWS SDK clients (initialized once per execution environment) ---

const kinesisClient = new KinesisClient({});
const sqsClient = new SQSClient({});
const cloudWatchClient = new CloudWatchClient({});

// --- Device registry (initialized once per execution environment, safe for concurrent access) ---

const deviceRegistry: DeviceRegistry = new StubDeviceRegistry();

// --- Structured logging ---

export interface StructuredLog {
  level: string;
  message: string;
  correlationId: string;
  deviceId?: string | null;
  sequenceNumber?: string;
  timestamp: string;
  [key: string]: unknown;
}

export function emitLog(log: StructuredLog): void {
  process.stdout.write(JSON.stringify(log) + '\n');
}

// --- CloudWatch metrics publishing ---

async function publishMetrics(
  recordsProcessed: number,
  recordsFailed: number,
  averageProcessingDurationMs: number,
): Promise<void> {
  try {
    await cloudWatchClient.send(
      new PutMetricDataCommand({
        Namespace: METRICS_NAMESPACE,
        MetricData: [
          {
            MetricName: 'RecordsProcessed',
            Value: recordsProcessed,
            Unit: 'Count',
            Timestamp: new Date(),
          },
          {
            MetricName: 'RecordsFailed',
            Value: recordsFailed,
            Unit: 'Count',
            Timestamp: new Date(),
          },
          {
            MetricName: 'AverageProcessingDuration',
            Value: averageProcessingDurationMs,
            Unit: 'Milliseconds',
            Timestamp: new Date(),
          },
        ],
      }),
    );
  } catch (error) {
    // Log metric publishing failure but don't fail the batch
    emitLog({
      level: 'ERROR',
      message: 'Failed to publish CloudWatch metrics',
      correlationId: 'system',
      timestamp: new Date().toISOString(),
      error: String(error),
    });
  }
}

// --- DLQ record writing ---

async function writeToDLQ(dlqRecord: DLQRecord): Promise<void> {
  if (!DLQ_URL) {
    emitLog({
      level: 'WARN',
      message: 'DLQ_URL not configured, skipping DLQ write',
      correlationId: dlqRecord.correlationId,
      timestamp: new Date().toISOString(),
    });
    return;
  }

  await sqsClient.send(
    new SendMessageCommand({
      QueueUrl: DLQ_URL,
      MessageBody: JSON.stringify(dlqRecord),
    }),
  );
}

// --- Write enriched record to Kinesis ---

async function writeToEnrichedStream(
  enrichedRecord: Record<string, unknown>,
  partitionKey: string,
): Promise<void> {
  await kinesisClient.send(
    new PutRecordCommand({
      StreamName: ENRICHED_STREAM_NAME,
      Data: Buffer.from(JSON.stringify(enrichedRecord)),
      PartitionKey: partitionKey,
    }),
  );
}

// --- Process a single Kinesis record ---

interface ProcessResult {
  success: boolean;
  sequenceNumber: string;
}

async function processRecord(record: KinesisEventRecord): Promise<ProcessResult> {
  const sequenceNumber = record.kinesis.sequenceNumber;
  const rawData = record.kinesis.data;
  let correlationId = `unknown-unknown-${sequenceNumber}`;
  let deviceId: string | null = null;

  try {
    // Step 1: Decode base64
    const decodedPayload = Buffer.from(rawData, 'base64').toString('utf-8');

    // Step 2: Parse JSON
    let parsedData: unknown;
    try {
      parsedData = JSON.parse(decodedPayload);
    } catch {
      // Parse error — send to DLQ
      correlationId = `unknown-${new Date().toISOString()}-${sequenceNumber}`;

      emitLog({
        level: 'ERROR',
        message: 'Failed to parse JSON payload',
        correlationId,
        sequenceNumber,
        deviceId: null,
        timestamp: new Date().toISOString(),
        errorCode: 'PARSE_ERROR',
      });

      const dlqRecord: DLQRecord = {
        originalPayload: rawData,
        errorDescription: 'Failed to parse JSON payload',
        errorCode: 'PARSE_ERROR',
        deviceId: null,
        correlationId,
        failedAt: new Date().toISOString(),
        sourceStream: SOURCE_STREAM,
        sequenceNumber,
      };

      await writeToDLQ(dlqRecord);
      // Parse failure is deterministic - return success so ESM advances.
      return { success: true, sequenceNumber };
    }

    // Extract deviceId if possible for correlation
    if (
      parsedData !== null &&
      typeof parsedData === 'object' &&
      'deviceId' in parsedData &&
      typeof (parsedData as Record<string, unknown>).deviceId === 'string'
    ) {
      deviceId = (parsedData as Record<string, unknown>).deviceId as string;
    }

    // Step 3: Validate against schema
    const validationResult = validateTelemetryRecord(parsedData);

    if (!validationResult.valid) {
      // Generate correlation ID with available info
      const isoTimestamp = deviceId
        ? epochMillisToIso8601(
            typeof (parsedData as Record<string, unknown>).timestamp === 'number'
              ? ((parsedData as Record<string, unknown>).timestamp as number)
              : Date.now(),
          )
        : new Date().toISOString();

      correlationId = generateCorrelationId(
        deviceId || 'unknown',
        isoTimestamp,
        sequenceNumber,
      );

      emitLog({
        level: 'ERROR',
        message: 'Record failed schema validation',
        correlationId,
        sequenceNumber,
        deviceId,
        timestamp: new Date().toISOString(),
        errorCode: 'SCHEMA_VALIDATION_FAILED',
        validationErrors: validationResult.errors,
      });

      const dlqRecord: DLQRecord = {
        originalPayload: rawData,
        errorDescription: validationResult.errors.join('; '),
        errorCode: 'SCHEMA_VALIDATION_FAILED',
        deviceId,
        correlationId,
        failedAt: new Date().toISOString(),
        sourceStream: SOURCE_STREAM,
        sequenceNumber,
      };

      await writeToDLQ(dlqRecord);
      // Validation failure is deterministic - record will never pass.
      // Return success so ESM advances past it (record is safely in DLQ).
      return { success: true, sequenceNumber };
    }

    // Step 4: Enrich valid record
    const telemetryRecord = validationResult.record;
    const enrichedRecord = await enrichRecord(telemetryRecord, sequenceNumber, deviceRegistry);

    correlationId = enrichedRecord.correlationId;

    // Step 5: Write enriched record to Kinesis enriched stream
    await writeToEnrichedStream(
      enrichedRecord as unknown as Record<string, unknown>,
      enrichedRecord.deviceGroupId,
    );

    emitLog({
      level: 'INFO',
      message: 'Record processed successfully',
      correlationId,
      sequenceNumber,
      deviceId: telemetryRecord.deviceId,
      timestamp: new Date().toISOString(),
      deviceGroupId: enrichedRecord.deviceGroupId,
      sensorType: telemetryRecord.sensorType,
    });

    return { success: true, sequenceNumber };
  } catch (error) {
    // Handle enrichment errors specifically
    if (error instanceof EnrichmentError) {
      deviceId = error.deviceId;
      const isoTimestamp = new Date().toISOString();
      correlationId = generateCorrelationId(deviceId, isoTimestamp, sequenceNumber);

      emitLog({
        level: 'ERROR',
        message: 'Record enrichment failed',
        correlationId,
        sequenceNumber,
        deviceId,
        timestamp: isoTimestamp,
        errorCode: error.code,
        errorMessage: error.message,
      });

      const dlqRecord: DLQRecord = {
        originalPayload: rawData,
        errorDescription: error.message,
        errorCode: error.code,
        deviceId,
        correlationId,
        failedAt: isoTimestamp,
        sourceStream: SOURCE_STREAM,
        sequenceNumber,
      };

      await writeToDLQ(dlqRecord);
      // Enrichment failure (device not found) is deterministic - return success.
      return { success: true, sequenceNumber };
    }

    // Unexpected error — report as failure (transient, should be retried)
    emitLog({
      level: 'ERROR',
      message: 'Unexpected error processing record',
      correlationId,
      sequenceNumber,
      deviceId,
      timestamp: new Date().toISOString(),
      error: String(error),
    });

    return { success: false, sequenceNumber };
  }
}

// --- Main Lambda handler ---

export async function handler(event: KinesisEvent): Promise<KinesisStreamHandlerResponse> {
  const batchStartTime = Date.now();
  const results: ProcessResult[] = [];

  // Process each record in the batch
  for (const record of event.Records) {
    const result = await processRecord(record);
    results.push(result);
  }

  const batchEndTime = Date.now();
  const totalDurationMs = batchEndTime - batchStartTime;
  const recordsProcessed = results.filter((r) => r.success).length;
  const recordsFailed = results.filter((r) => !r.success).length;
  const averageProcessingDurationMs =
    results.length > 0 ? totalDurationMs / results.length : 0;

  // Publish CloudWatch metrics
  await publishMetrics(recordsProcessed, recordsFailed, averageProcessingDurationMs);

  // Build partial batch response with failed record sequence numbers
  const batchItemFailures = results
    .filter((r) => !r.success)
    .map((r) => ({ itemIdentifier: r.sequenceNumber }));

  return { batchItemFailures };
}
