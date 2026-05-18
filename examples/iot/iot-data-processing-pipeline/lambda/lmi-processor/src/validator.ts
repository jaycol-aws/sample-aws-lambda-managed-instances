/**
 * Manual JSON schema validation for IoT telemetry records.
 * No external schema library used — keeps the Lambda bundle small.
 *
 * Validates: Requirements 3.3
 */

import { TelemetryRecord } from './types';

const VALID_SENSOR_TYPES = [
  'temperature',
  'humidity',
  'gps',
  'vibration',
  'pressure',
  'proximity',
] as const;

const ALLOWED_TOP_LEVEL_KEYS = new Set([
  'deviceId',
  'timestamp',
  'sensorType',
  'readings',
  'metadata',
]);

export type ValidationResult =
  | { valid: true; record: TelemetryRecord }
  | { valid: false; errors: string[] };

export function validateTelemetryRecord(data: unknown): ValidationResult {
  const errors: string[] = [];

  if (data === null || data === undefined || typeof data !== 'object' || Array.isArray(data)) {
    return { valid: false, errors: ['payload: must be a non-null object'] };
  }

  const record = data as Record<string, unknown>;

  // Check for additional top-level properties
  for (const key of Object.keys(record)) {
    if (!ALLOWED_TOP_LEVEL_KEYS.has(key)) {
      errors.push(`${key}: unknown property is not allowed`);
    }
  }

  // deviceId: required, string, minLength 1, maxLength 128
  if (!('deviceId' in record)) {
    errors.push('deviceId: required field is missing');
  } else if (typeof record.deviceId !== 'string') {
    errors.push('deviceId: must be a string');
  } else if (record.deviceId.length < 1) {
    errors.push('deviceId: must be a non-empty string');
  } else if (record.deviceId.length > 128) {
    errors.push('deviceId: must be at most 128 characters');
  }

  // timestamp: required, number, minimum 0
  if (!('timestamp' in record)) {
    errors.push('timestamp: required field is missing');
  } else if (typeof record.timestamp !== 'number') {
    errors.push('timestamp: must be a number');
  } else if (record.timestamp < 0) {
    errors.push('timestamp: must be >= 0');
  }

  // sensorType: required, string, enum
  if (!('sensorType' in record)) {
    errors.push('sensorType: required field is missing');
  } else if (typeof record.sensorType !== 'string') {
    errors.push('sensorType: must be a string');
  } else if (!(VALID_SENSOR_TYPES as readonly string[]).includes(record.sensorType)) {
    errors.push(
      `sensorType: must be one of ${VALID_SENSOR_TYPES.join(', ')}`,
    );
  }

  // readings: required, object with required value (number) and unit (string, minLength 1)
  if (!('readings' in record)) {
    errors.push('readings: required field is missing');
  } else if (
    record.readings === null ||
    typeof record.readings !== 'object' ||
    Array.isArray(record.readings)
  ) {
    errors.push('readings: must be a non-null object');
  } else {
    const readings = record.readings as Record<string, unknown>;

    if (!('value' in readings)) {
      errors.push('readings.value: required field is missing');
    } else if (typeof readings.value !== 'number') {
      errors.push('readings.value: must be a number');
    }

    if (!('unit' in readings)) {
      errors.push('readings.unit: required field is missing');
    } else if (typeof readings.unit !== 'string') {
      errors.push('readings.unit: must be a string');
    } else if (readings.unit.length < 1) {
      errors.push('readings.unit: must be a non-empty string');
    }

    // secondary is optional, but if present must be an object
    if ('secondary' in readings && readings.secondary !== undefined) {
      if (
        readings.secondary === null ||
        typeof readings.secondary !== 'object' ||
        Array.isArray(readings.secondary)
      ) {
        errors.push('readings.secondary: must be an object');
      }
    }
  }

  // metadata: optional, but if present must be an object with correct field types
  if ('metadata' in record && record.metadata !== undefined) {
    if (
      record.metadata === null ||
      typeof record.metadata !== 'object' ||
      Array.isArray(record.metadata)
    ) {
      errors.push('metadata: must be a non-null object');
    } else {
      const metadata = record.metadata as Record<string, unknown>;

      if ('firmwareVersion' in metadata && metadata.firmwareVersion !== undefined) {
        if (typeof metadata.firmwareVersion !== 'string') {
          errors.push('metadata.firmwareVersion: must be a string');
        }
      }

      if ('batteryLevel' in metadata && metadata.batteryLevel !== undefined) {
        if (typeof metadata.batteryLevel !== 'number') {
          errors.push('metadata.batteryLevel: must be a number');
        } else if (metadata.batteryLevel < 0 || metadata.batteryLevel > 100) {
          errors.push('metadata.batteryLevel: must be between 0 and 100');
        }
      }

      if ('signalStrength' in metadata && metadata.signalStrength !== undefined) {
        if (typeof metadata.signalStrength !== 'number') {
          errors.push('metadata.signalStrength: must be a number');
        }
      }
    }
  }

  if (errors.length > 0) {
    return { valid: false, errors };
  }

  return { valid: true, record: record as unknown as TelemetryRecord };
}
