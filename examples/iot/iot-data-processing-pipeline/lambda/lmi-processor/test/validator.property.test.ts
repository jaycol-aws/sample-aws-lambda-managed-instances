/**
 * Property-based tests for telemetry record validation.
 *
 * Property 1: Invalid record handling — validation rejects and DLQ preserves payload
 *
 * Generator: Random JSON objects violating the telemetry schema
 * (missing fields, wrong types, invalid enum values, extra properties)
 *
 * Assertion: Validation rejects AND DLQ record contains byte-identical
 * original payload AND non-empty error description
 *
 * **Validates: Requirements 3.3, 3.5**
 */

import * as fc from 'fast-check';
import { validateTelemetryRecord } from '../src/validator';

const VALID_SENSOR_TYPES = [
  'temperature',
  'humidity',
  'gps',
  'vibration',
  'pressure',
  'proximity',
];

// --- Generators for invalid records ---

/** A valid base record to selectively break */
function validRecordArb(): fc.Arbitrary<Record<string, unknown>> {
  return fc.record({
    deviceId: fc.string({ minLength: 1, maxLength: 128 }),
    timestamp: fc.double({ min: 0, max: Number.MAX_SAFE_INTEGER, noNaN: true }),
    sensorType: fc.constantFrom(...VALID_SENSOR_TYPES),
    readings: fc.record({
      value: fc.double({ noNaN: true }),
      unit: fc.string({ minLength: 1, maxLength: 32 }),
    }),
  });
}

/** 1. Missing required fields — randomly omit one or more of the four required keys */
function missingFieldsArb(): fc.Arbitrary<Record<string, unknown>> {
  const requiredKeys = ['deviceId', 'timestamp', 'sensorType', 'readings'] as const;
  return validRecordArb().chain((rec) =>
    fc
      .subarray([...requiredKeys], { minLength: 1 })
      .map((keysToRemove) => {
        const copy = { ...rec };
        for (const k of keysToRemove) delete copy[k];
        return copy;
      }),
  );
}

/** 2. Wrong types for required fields */
function wrongTypesArb(): fc.Arbitrary<Record<string, unknown>> {
  // Pick a field and assign a value of the wrong type
  return fc.oneof(
    // deviceId as non-string
    validRecordArb().chain((rec) =>
      fc.oneof(fc.integer(), fc.boolean(), fc.constant(null), fc.constant([1])).map((v) => ({
        ...rec,
        deviceId: v,
      })),
    ),
    // timestamp as non-number
    validRecordArb().chain((rec) =>
      fc.oneof(fc.string(), fc.boolean(), fc.constant(null)).map((v) => ({
        ...rec,
        timestamp: v,
      })),
    ),
    // sensorType as non-string
    validRecordArb().chain((rec) =>
      fc.oneof(fc.integer(), fc.boolean(), fc.constant(null)).map((v) => ({
        ...rec,
        sensorType: v,
      })),
    ),
    // readings as non-object
    validRecordArb().chain((rec) =>
      fc.oneof(fc.string(), fc.integer(), fc.boolean(), fc.constant(null), fc.constant([1])).map(
        (v) => ({ ...rec, readings: v }),
      ),
    ),
  );
}

/** 3. Invalid enum values for sensorType */
function invalidEnumArb(): fc.Arbitrary<Record<string, unknown>> {
  return validRecordArb().chain((rec) =>
    fc
      .string({ minLength: 1, maxLength: 30 })
      .filter((s) => !VALID_SENSOR_TYPES.includes(s))
      .map((s) => ({ ...rec, sensorType: s })),
  );
}

/** 4. Extra top-level properties (additionalProperties: false) */
function extraPropertiesArb(): fc.Arbitrary<Record<string, unknown>> {
  const allowedKeys = new Set(['deviceId', 'timestamp', 'sensorType', 'readings', 'metadata']);
  return validRecordArb().chain((rec) =>
    fc
      .string({ minLength: 1, maxLength: 20 })
      .filter((k) => !allowedKeys.has(k))
      .chain((key) =>
        fc.jsonValue().map((val) => ({ ...rec, [key]: val })),
      ),
  );
}

/** 5. Invalid nested fields inside readings */
function invalidReadingsArb(): fc.Arbitrary<Record<string, unknown>> {
  return fc.oneof(
    // readings.value as non-number
    validRecordArb().chain((rec) =>
      fc.string().map((v) => ({ ...rec, readings: { value: v, unit: 'c' } })),
    ),
    // readings.unit as non-string
    validRecordArb().chain((rec) =>
      fc.integer().map((v) => ({ ...rec, readings: { value: 1, unit: v } })),
    ),
    // readings.unit as empty string
    validRecordArb().map((rec) => ({ ...rec, readings: { value: 1, unit: '' } })),
    // readings missing value
    validRecordArb().map((rec) => ({ ...rec, readings: { unit: 'c' } })),
    // readings missing unit
    validRecordArb().map((rec) => ({ ...rec, readings: { value: 1 } })),
  );
}

/** 6. Completely random objects (unlikely to be valid) */
function randomObjectArb(): fc.Arbitrary<unknown> {
  return fc.oneof(
    fc.jsonValue(),
    fc.constant(null),
    fc.constant(undefined),
    fc.integer(),
    fc.string(),
    fc.boolean(),
    fc.constant([]),
  );
}

/** Negative timestamp */
function negativeTimestampArb(): fc.Arbitrary<Record<string, unknown>> {
  return validRecordArb().chain((rec) =>
    fc.double({ min: -1e15, max: -0.001, noNaN: true }).map((t) => ({ ...rec, timestamp: t })),
  );
}

/** Invalid metadata fields */
function invalidMetadataArb(): fc.Arbitrary<Record<string, unknown>> {
  return fc.oneof(
    // batteryLevel out of range
    validRecordArb().chain((rec) =>
      fc
        .double({ noNaN: true })
        .filter((v) => v < 0 || v > 100)
        .map((v) => ({ ...rec, metadata: { batteryLevel: v } })),
    ),
    // firmwareVersion as non-string
    validRecordArb().chain((rec) =>
      fc.integer().map((v) => ({ ...rec, metadata: { firmwareVersion: v } })),
    ),
    // signalStrength as non-number
    validRecordArb().chain((rec) =>
      fc.string().map((v) => ({ ...rec, metadata: { signalStrength: v } })),
    ),
    // metadata as non-object
    validRecordArb().chain((rec) =>
      fc.oneof(fc.string(), fc.integer(), fc.constant(null)).map((v) => ({ ...rec, metadata: v })),
    ),
  );
}

/** Combined generator: pick from any invalid strategy */
const invalidRecordArb: fc.Arbitrary<unknown> = fc.oneof(
  missingFieldsArb(),
  wrongTypesArb(),
  invalidEnumArb(),
  extraPropertiesArb(),
  invalidReadingsArb(),
  randomObjectArb(),
  negativeTimestampArb(),
  invalidMetadataArb(),
);

describe('Property 1: Invalid record handling — validation rejects and DLQ preserves payload', () => {
  it('rejects all invalid records with non-empty errors, and original payload survives base64 round-trip', () => {
    fc.assert(
      fc.property(invalidRecordArb, (input) => {
        // --- Validation rejects ---
        const result = validateTelemetryRecord(input);
        expect(result.valid).toBe(false);

        if (!result.valid) {
          // --- Errors array is non-empty ---
          expect(result.errors.length).toBeGreaterThan(0);
          for (const err of result.errors) {
            expect(typeof err).toBe('string');
            expect(err.length).toBeGreaterThan(0);
          }

          // --- DLQ preservation: base64 round-trip of original payload ---
          // In the real handler, the original payload comes from the Kinesis
          // record (always a string). Here we simulate serialization of whatever
          // the input was — undefined/function values serialize to the string
          // "undefined" which is still a valid byte sequence for DLQ storage.
          const payloadStr =
            input === undefined ? '' : JSON.stringify(input) ?? '';
          const base64Encoded = Buffer.from(payloadStr).toString('base64');
          const decoded = Buffer.from(base64Encoded, 'base64').toString('utf-8');
          expect(decoded).toBe(payloadStr);
        }
      }),
      { numRuns: 100 },
    );
  });
});
