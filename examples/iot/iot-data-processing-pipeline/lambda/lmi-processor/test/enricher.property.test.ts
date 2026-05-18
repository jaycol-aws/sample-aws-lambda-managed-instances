/**
 * Property-based tests for record enrichment logic.
 *
 * Property 2: Enrichment correctness — valid records produce correct enriched format
 *
 * Generator: Random valid TelemetryRecord objects
 * Assertion: Output has ISO-8601 timestamp, non-empty location fields,
 * sensor classification, correct correlation ID pattern, original readings preserved
 *
 * **Validates: Requirements 3.4**
 */

import * as fc from 'fast-check';
import { enrichRecord } from '../src/enricher';
import { StubDeviceRegistry } from '../src/device-registry';
import { TelemetryRecord } from '../src/types';

const VALID_SENSOR_TYPES = [
  'temperature',
  'humidity',
  'gps',
  'vibration',
  'pressure',
  'proximity',
] as const;

const SENSOR_CLASSIFICATION_MAP: Record<string, string> = {
  temperature: 'environmental',
  humidity: 'environmental',
  pressure: 'environmental',
  gps: 'location',
  vibration: 'motion',
  proximity: 'motion',
};

/** ISO-8601 regex pattern for validation */
const ISO_8601_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{3}Z$/;

/** Correlation ID pattern: {deviceId}-{isoTimestamp}-{sequenceNumber} */
function expectedCorrelationId(
  deviceId: string,
  timestamp: number,
  sequenceNumber: string,
): string {
  const isoTimestamp = new Date(timestamp).toISOString();
  return `${deviceId}-${isoTimestamp}-${sequenceNumber}`;
}

// --- Generator for valid TelemetryRecord objects ---

function validTelemetryRecordArb(): fc.Arbitrary<TelemetryRecord> {
  return fc.record({
    deviceId: fc.string({ minLength: 1, maxLength: 64 }).filter((s) => s.trim().length > 0),
    timestamp: fc.integer({ min: 0, max: 4102444800000 }), // epoch 0 to ~2100
    sensorType: fc.constantFrom(...VALID_SENSOR_TYPES),
    readings: fc.record({
      value: fc.double({ min: -1e6, max: 1e6, noNaN: true, noDefaultInfinity: true }),
      unit: fc.string({ minLength: 1, maxLength: 16 }),
    }),
    metadata: fc.option(
      fc.record({
        firmwareVersion: fc.option(fc.string({ minLength: 1, maxLength: 16 }), { nil: undefined }),
        batteryLevel: fc.option(fc.integer({ min: 0, max: 100 }), { nil: undefined }),
        signalStrength: fc.option(fc.integer({ min: -120, max: 0 }), { nil: undefined }),
      }),
      { nil: undefined },
    ),
  }) as fc.Arbitrary<TelemetryRecord>;
}

/** Generator for Kinesis-style sequence numbers */
function sequenceNumberArb(): fc.Arbitrary<string> {
  return fc.stringOf(fc.constantFrom('0', '1', '2', '3', '4', '5', '6', '7', '8', '9'), {
    minLength: 1,
    maxLength: 20,
  });
}

describe('Property 2: Enrichment correctness — valid records produce correct enriched format', () => {
  const registry = new StubDeviceRegistry();

  it('produces enriched records with ISO-8601 timestamp, non-empty location, sensor classification, correct correlation ID, and preserved readings', async () => {
    await fc.assert(
      fc.asyncProperty(
        validTelemetryRecordArb(),
        sequenceNumberArb(),
        async (record, sequenceNumber) => {
          const enriched = await enrichRecord(record, sequenceNumber, registry);

          // 1. Output has ISO-8601 timestamp
          expect(enriched.timestamp).toMatch(ISO_8601_PATTERN);
          expect(enriched.timestamp).toBe(new Date(record.timestamp).toISOString());

          // 2. Non-empty location fields
          expect(typeof enriched.location.latitude).toBe('number');
          expect(typeof enriched.location.longitude).toBe('number');
          expect(enriched.location.facility.length).toBeGreaterThan(0);
          expect(enriched.location.zone.length).toBeGreaterThan(0);

          // 3. Sensor classification is correct
          const expectedClassification = SENSOR_CLASSIFICATION_MAP[record.sensorType];
          expect(enriched.sensorClassification).toBe(expectedClassification);

          // 4. Correct correlation ID pattern: {deviceId}-{isoTimestamp}-{sequenceNumber}
          const expectedCorrId = expectedCorrelationId(
            record.deviceId,
            record.timestamp,
            sequenceNumber,
          );
          expect(enriched.correlationId).toBe(expectedCorrId);

          // 5. Original readings preserved without modification
          expect(enriched.readings.value).toBe(record.readings.value);
          expect(enriched.readings.unit).toBe(record.readings.unit);

          // Additional structural checks
          expect(enriched.deviceId).toBe(record.deviceId);
          expect(enriched.originalTimestamp).toBe(record.timestamp);
          expect(enriched.sensorType).toBe(record.sensorType);
          expect(enriched.processingMetadata.pipelineVersion).toBe('1.0.0');
          expect(enriched.processingMetadata.processedAt).toMatch(ISO_8601_PATTERN);
          expect(enriched.processingMetadata.lmiInstanceId.length).toBeGreaterThan(0);
          expect(enriched.deviceGroupId.length).toBeGreaterThan(0);
        },
      ),
      { numRuns: 100 },
    );
  });
});
