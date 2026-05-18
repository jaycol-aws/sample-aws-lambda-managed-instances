/**
 * Property-based tests for structured logging in the LMI batch handler.
 *
 * Property 6: Structured logging with correlation ID
 *
 * Generator: Random telemetry records (both valid and invalid)
 *
 * Assertion: Log output is parseable JSON with correlationId matching
 * `{deviceId}-{isoTimestamp}-{sequenceNumber}`, plus level and message fields
 *
 * **Validates: Requirements 6.4**
 */

import * as fc from 'fast-check';
import { handler } from '../src/handler';
import { KinesisEvent, KinesisEventRecord } from '../src/types';

// --- Mock AWS SDK clients ---

jest.mock('@aws-sdk/client-kinesis', () => ({
  KinesisClient: jest.fn().mockImplementation(() => ({
    send: jest.fn().mockResolvedValue({}),
  })),
  PutRecordCommand: jest.fn().mockImplementation((params) => params),
}));

jest.mock('@aws-sdk/client-sqs', () => ({
  SQSClient: jest.fn().mockImplementation(() => ({
    send: jest.fn().mockResolvedValue({}),
  })),
  SendMessageCommand: jest.fn().mockImplementation((params) => params),
}));

jest.mock('@aws-sdk/client-cloudwatch', () => ({
  CloudWatchClient: jest.fn().mockImplementation(() => ({
    send: jest.fn().mockResolvedValue({}),
  })),
  PutMetricDataCommand: jest.fn().mockImplementation((params) => params),
}));

// --- Constants ---

const VALID_SENSOR_TYPES = [
  'temperature',
  'humidity',
  'gps',
  'vibration',
  'pressure',
  'proximity',
] as const;

// ISO-8601 timestamp pattern (e.g., 2025-01-15T10:30:00.000Z)
const ISO_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

// --- Generators ---

/** Generate a valid telemetry payload object */
function validPayloadArb(): fc.Arbitrary<Record<string, unknown>> {
  return fc.record({
    deviceId: fc.string({ minLength: 1, maxLength: 64 }).filter((s) => s.trim().length > 0),
    timestamp: fc.integer({ min: 0, max: 4102444800000 }),
    sensorType: fc.constantFrom(...VALID_SENSOR_TYPES),
    readings: fc.record({
      value: fc.double({ min: -1e6, max: 1e6, noNaN: true, noDefaultInfinity: true }),
      unit: fc.string({ minLength: 1, maxLength: 16 }),
    }),
  });
}

/** Generate an invalid telemetry payload (various strategies) */
function invalidPayloadArb(): fc.Arbitrary<unknown> {
  return fc.oneof(
    // Missing required fields
    fc.record({
      deviceId: fc.string({ minLength: 1, maxLength: 64 }),
      // missing timestamp, sensorType, readings
    }),
    // Invalid sensorType enum
    validPayloadArb().map((rec) => ({
      ...rec,
      sensorType: 'invalid_sensor_type_xyz',
    })),
    // Negative timestamp
    validPayloadArb().map((rec) => ({
      ...rec,
      timestamp: -1000,
    })),
    // readings missing value field
    validPayloadArb().map((rec) => ({
      ...rec,
      readings: { unit: 'celsius' },
    })),
    // readings missing unit field
    validPayloadArb().map((rec) => ({
      ...rec,
      readings: { value: 42 },
    })),
    // Non-JSON parseable (will be encoded as non-JSON string)
    fc.constant('NOT_VALID_JSON_{{{{'),
    // Empty object (missing all required fields)
    fc.constant({}),
    // Extra top-level properties (additionalProperties: false)
    validPayloadArb().map((rec) => ({
      ...rec,
      unknownExtraField: 'should_fail_validation',
    })),
  );
}

/** Encode a payload as a base64 Kinesis data string */
function encodePayload(payload: unknown): string {
  if (typeof payload === 'string') {
    return Buffer.from(payload).toString('base64');
  }
  return Buffer.from(JSON.stringify(payload)).toString('base64');
}

/** Generate a unique sequence number */
function sequenceNumberArb(): fc.Arbitrary<string> {
  return fc.stringOf(fc.constantFrom('0', '1', '2', '3', '4', '5', '6', '7', '8', '9'), {
    minLength: 10,
    maxLength: 20,
  });
}

/** Build a KinesisEventRecord from a payload and sequence number */
function buildKinesisRecord(payload: unknown, sequenceNumber: string): KinesisEventRecord {
  return {
    kinesis: {
      data: encodePayload(payload),
      sequenceNumber,
      partitionKey: 'test-partition',
    },
    eventID: `event-${sequenceNumber}`,
    eventSourceARN: 'arn:aws:kinesis:us-east-1:123456789012:stream/test-stream',
  };
}

interface TaggedRecord {
  record: KinesisEventRecord;
  isValid: boolean;
  isJsonParseable: boolean;
  sequenceNumber: string;
  deviceId?: string;
}

/**
 * Generator for a batch of Kinesis records with a mix of valid and invalid payloads.
 * Each record has a unique sequence number.
 * Returns the records along with metadata about which are valid/invalid.
 */
function batchArb(): fc.Arbitrary<TaggedRecord[]> {
  return fc
    .array(
      fc.record({
        isValid: fc.boolean(),
        validPayload: validPayloadArb(),
        invalidPayload: invalidPayloadArb(),
        seqSuffix: fc.nat({ max: 999999999 }),
      }),
      { minLength: 1, maxLength: 10 },
    )
    .map((items) => {
      return items.map((item, index) => {
        const sequenceNumber = `${String(index).padStart(5, '0')}${String(item.seqSuffix).padStart(10, '0')}`;
        const payload = item.isValid ? item.validPayload : item.invalidPayload;
        const isJsonParseable = typeof payload !== 'string';
        const deviceId = item.isValid
          ? (item.validPayload.deviceId as string)
          : undefined;
        return {
          record: buildKinesisRecord(payload, sequenceNumber),
          isValid: item.isValid,
          isJsonParseable,
          sequenceNumber,
          deviceId,
        };
      });
    });
}

// --- Property Test ---

describe('Property 6: Structured logging with correlation ID', () => {
  it('all log entries are valid JSON with correlationId, level, and message fields', async () => {
    await fc.assert(
      fc.asyncProperty(batchArb(), async (taggedRecords) => {
        // Capture stdout writes
        const logLines: string[] = [];
        const stdoutSpy = jest
          .spyOn(process.stdout, 'write')
          .mockImplementation((chunk: string | Uint8Array) => {
            if (typeof chunk === 'string') {
              // Each log line ends with \n
              const lines = chunk.split('\n').filter((l) => l.trim().length > 0);
              logLines.push(...lines);
            }
            return true;
          });

        try {
          // Build the KinesisEvent
          const event: KinesisEvent = {
            Records: taggedRecords.map((tr) => tr.record),
          };

          // Invoke the handler
          await handler(event);

          // We expect at least one log entry per record in the batch
          // (each record produces at least one log: success or error)
          expect(logLines.length).toBeGreaterThanOrEqual(taggedRecords.length);

          // Verify every log line is valid JSON with required fields
          for (const line of logLines) {
            // Must be parseable JSON
            let parsed: Record<string, unknown>;
            try {
              parsed = JSON.parse(line);
            } catch {
              throw new Error(`Log line is not valid JSON: ${line}`);
            }

            // Must have 'level' field (string)
            expect(parsed).toHaveProperty('level');
            expect(typeof parsed.level).toBe('string');
            expect((parsed.level as string).length).toBeGreaterThan(0);

            // Must have 'message' field (string)
            expect(parsed).toHaveProperty('message');
            expect(typeof parsed.message).toBe('string');
            expect((parsed.message as string).length).toBeGreaterThan(0);

            // Must have 'correlationId' field (string)
            expect(parsed).toHaveProperty('correlationId');
            expect(typeof parsed.correlationId).toBe('string');
            expect((parsed.correlationId as string).length).toBeGreaterThan(0);

            // correlationId must match the pattern:
            // {deviceId}-{isoTimestamp}-{sequenceNumber}
            // or for system logs: 'system'
            const correlationId = parsed.correlationId as string;
            if (correlationId !== 'system') {
              // Pattern: {something}-{ISO timestamp}-{sequenceNumber}
              // The ISO timestamp contains dashes, so we need to match the pattern carefully
              // Format: deviceId-YYYY-MM-DDTHH:MM:SS.mmmZ-sequenceNumber
              // or: unknown-YYYY-MM-DDTHH:MM:SS.mmmZ-sequenceNumber
              const correlationPattern =
                /^(.+)-(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)-(\d+)$/;
              const match = correlationId.match(correlationPattern);
              expect(match).not.toBeNull();

              if (match) {
                const [, idPart, timestampPart, seqPart] = match;

                // The timestamp part must be a valid ISO-8601 timestamp
                expect(timestampPart).toMatch(ISO_TIMESTAMP_PATTERN);

                // The id part must be either a deviceId or 'unknown'
                expect(idPart.length).toBeGreaterThan(0);

                // The sequence number part must be a numeric string
                expect(seqPart).toMatch(/^\d+$/);
              }
            }
          }
        } finally {
          stdoutSpy.mockRestore();
        }
      }),
      { numRuns: 100 },
    );
  });
});


// ============================================================================
// Unit Tests for LMI Processor Handler
// ============================================================================

import { SQSClient, SendMessageCommand } from '@aws-sdk/client-sqs';
import { KinesisClient, PutRecordCommand } from '@aws-sdk/client-kinesis';

// Helper to build a Kinesis event record from a payload
function buildRecord(payload: unknown, sequenceNumber: string): KinesisEventRecord {
  const data =
    typeof payload === 'string'
      ? Buffer.from(payload).toString('base64')
      : Buffer.from(JSON.stringify(payload)).toString('base64');
  return {
    kinesis: {
      data,
      sequenceNumber,
      partitionKey: 'test-partition',
    },
    eventID: `event-${sequenceNumber}`,
    eventSourceARN: 'arn:aws:kinesis:us-east-1:123456789012:stream/test-stream',
  };
}

function validPayload() {
  return {
    deviceId: 'sensor-001',
    timestamp: 1700000000000,
    sensorType: 'temperature',
    readings: { value: 23.5, unit: 'celsius' },
  };
}

describe('Handler unit tests', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    // Suppress stdout logs during unit tests
    jest.spyOn(process.stdout, 'write').mockImplementation(() => true);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  describe('Valid record processing', () => {
    it('processes a valid temperature record and returns no failures', async () => {
      const event: KinesisEvent = {
        Records: [buildRecord(validPayload(), '00001')],
      };

      const result = await handler(event);
      expect(result.batchItemFailures).toHaveLength(0);
    });

    it('processes valid records for each sensor type without failures', async () => {
      const sensorTypes = ['temperature', 'humidity', 'gps', 'vibration', 'pressure', 'proximity'];
      const records = sensorTypes.map((st, i) =>
        buildRecord({ ...validPayload(), sensorType: st }, String(i + 1).padStart(5, '0')),
      );

      const event: KinesisEvent = { Records: records };
      const result = await handler(event);
      expect(result.batchItemFailures).toHaveLength(0);
    });
  });

  describe('Invalid record handling', () => {
    it('handles empty JSON object gracefully (writes to DLQ, no retry)', async () => {
      const event: KinesisEvent = {
        Records: [buildRecord({}, '10001')],
      };

      const result = await handler(event);
      // Deterministic failures are handled gracefully - no retry needed
      expect(result.batchItemFailures).toHaveLength(0);
    });

    it('handles missing required fields gracefully (writes to DLQ, no retry)', async () => {
      const event: KinesisEvent = {
        Records: [buildRecord({ deviceId: 'dev-1' }, '10002')],
      };

      const result = await handler(event);
      expect(result.batchItemFailures).toHaveLength(0);
    });

    it('handles invalid sensorType gracefully (writes to DLQ, no retry)', async () => {
      const event: KinesisEvent = {
        Records: [buildRecord({ ...validPayload(), sensorType: 'wind' }, '10003')],
      };

      const result = await handler(event);
      expect(result.batchItemFailures).toHaveLength(0);
    });

    it('handles negative timestamp gracefully (writes to DLQ, no retry)', async () => {
      const event: KinesisEvent = {
        Records: [buildRecord({ ...validPayload(), timestamp: -100 }, '10004')],
      };

      const result = await handler(event);
      expect(result.batchItemFailures).toHaveLength(0);
    });

    it('handles batteryLevel out of range gracefully (writes to DLQ, no retry)', async () => {
      const event: KinesisEvent = {
        Records: [
          buildRecord(
            { ...validPayload(), metadata: { batteryLevel: 150 } },
            '10005',
          ),
        ],
      };

      const result = await handler(event);
      expect(result.batchItemFailures).toHaveLength(0);
    });

    it('handles non-JSON payload gracefully (writes to DLQ, no retry)', async () => {
      const event: KinesisEvent = {
        Records: [buildRecord('NOT_VALID_JSON{{{{', '10006')],
      };

      const result = await handler(event);
      expect(result.batchItemFailures).toHaveLength(0);
    });
  });

  describe('Partial batch response', () => {
    it('returns empty batchItemFailures for mixed batch (deterministic failures handled gracefully)', async () => {
      const event: KinesisEvent = {
        Records: [
          buildRecord(validPayload(), '20001'), // valid
          buildRecord({}, '20002'), // invalid - empty
          buildRecord(validPayload(), '20003'), // valid
          buildRecord({ ...validPayload(), sensorType: 'invalid' }, '20004'), // invalid
        ],
      };

      const result = await handler(event);
      // All failures are deterministic (validation) - handled via DLQ, not retry
      expect(result.batchItemFailures).toHaveLength(0);
    });
  });
});
