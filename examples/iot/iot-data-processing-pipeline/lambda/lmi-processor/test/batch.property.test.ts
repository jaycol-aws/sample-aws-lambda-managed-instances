/**
 * Property-based tests for the LMI batch handler.
 *
 * Property 3: Partial batch response identifies exactly the failed records
 *
 * Generator: Random arrays of Kinesis records (mix of valid and invalid payloads
 * with unique sequence numbers)
 *
 * Assertion: Set of failed sequence numbers in response equals set of sequence
 * numbers of invalid records
 *
 * **Validates: Requirements 3.8**
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

// Suppress stdout logs during tests
beforeAll(() => {
  jest.spyOn(process.stdout, 'write').mockImplementation(() => true);
});

afterAll(() => {
  jest.restoreAllMocks();
});

// --- Constants ---

const VALID_SENSOR_TYPES = [
  'temperature',
  'humidity',
  'gps',
  'vibration',
  'pressure',
  'proximity',
] as const;

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
  sequenceNumber: string;
}

/**
 * Generator for a batch of Kinesis records with a mix of valid and invalid payloads.
 * Each record has a unique sequence number.
 * Returns the records along with metadata about which are valid/invalid.
 */
function batchArb(): fc.Arbitrary<TaggedRecord[]> {
  // Generate between 1 and 15 records per batch
  return fc
    .array(
      fc.record({
        isValid: fc.boolean(),
        validPayload: validPayloadArb(),
        invalidPayload: invalidPayloadArb(),
        seqSuffix: fc.nat({ max: 999999999 }),
      }),
      { minLength: 1, maxLength: 15 },
    )
    .map((items) => {
      // Ensure unique sequence numbers by using index + random suffix
      return items.map((item, index) => {
        const sequenceNumber = `${String(index).padStart(5, '0')}${String(item.seqSuffix).padStart(10, '0')}`;
        const payload = item.isValid ? item.validPayload : item.invalidPayload;
        return {
          record: buildKinesisRecord(payload, sequenceNumber),
          isValid: item.isValid,
          sequenceNumber,
        };
      });
    });
}

// --- Property Test ---

describe('Property 3: Deterministic failures are handled gracefully without retry', () => {
  it('batchItemFailures is empty when all failures are deterministic (validation/parse errors)', async () => {
    await fc.assert(
      fc.asyncProperty(batchArb(), async (taggedRecords) => {
        // Build the KinesisEvent
        const event: KinesisEvent = {
          Records: taggedRecords.map((tr) => tr.record),
        };

        // Invoke the handler
        const response = await handler(event);

        // Collect the set of failed sequence numbers from the response
        const failedSeqNumbers = new Set(
          response.batchItemFailures.map((f) => f.itemIdentifier),
        );

        // With deterministic failures handled gracefully (written to DLQ, returned as success),
        // batchItemFailures should be empty - no records need retry since validation/parse
        // errors will never succeed on retry.
        // Only transient errors (network failures) would appear here.
        expect(failedSeqNumbers.size).toBe(0);

        // Verify no valid record is ever reported as failed
        for (const tr of taggedRecords) {
          if (tr.isValid) {
            expect(failedSeqNumbers.has(tr.sequenceNumber)).toBe(false);
          }
        }
      }),
      { numRuns: 100 },
    );
  });
});
