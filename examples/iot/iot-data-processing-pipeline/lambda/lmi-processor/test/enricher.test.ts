/**
 * Unit tests for the enricher module.
 *
 * Tests:
 * - Valid telemetry record produces correct enriched output for each sensor type
 * - Correlation ID format
 * - Timestamp conversion edge cases (epoch 0, year 2038)
 * - Device not found in registry produces EnrichmentError with ENRICHMENT_FAILED code
 * - Sensor classification mapping
 *
 * Validates: Requirements 3.4, 3.5
 */

import {
  enrichRecord,
  generateCorrelationId,
  epochMillisToIso8601,
  classifySensorType,
  EnrichmentError,
} from '../src/enricher';
import { StubDeviceRegistry } from '../src/device-registry';
import { TelemetryRecord, DeviceRegistry, DeviceRegistryEntry } from '../src/types';

// --- Helpers ---

function makeValidRecord(overrides: Partial<TelemetryRecord> = {}): TelemetryRecord {
  return {
    deviceId: 'sensor-001',
    timestamp: 1700000000000,
    sensorType: 'temperature',
    readings: { value: 23.5, unit: 'celsius' },
    ...overrides,
  };
}

// --- Tests ---

describe('epochMillisToIso8601', () => {
  it('converts epoch 0 to 1970-01-01T00:00:00.000Z', () => {
    expect(epochMillisToIso8601(0)).toBe('1970-01-01T00:00:00.000Z');
  });

  it('converts year 2038 timestamp correctly', () => {
    // 2145916800000 ms = 2038-01-01T00:00:00.000Z
    expect(epochMillisToIso8601(2145916800000)).toBe('2038-01-01T00:00:00.000Z');
  });

  it('converts a known timestamp', () => {
    // 1700000000000 ms = 2023-11-14T22:13:20.000Z
    expect(epochMillisToIso8601(1700000000000)).toBe('2023-11-14T22:13:20.000Z');
  });

  it('handles timestamps beyond year 2038 (no 32-bit overflow)', () => {
    // 2147483648000 ms = 2038-01-19T03:14:08.000Z (the classic 32-bit overflow moment)
    expect(epochMillisToIso8601(2147483648000)).toBe('2038-01-19T03:14:08.000Z');
  });
});

describe('generateCorrelationId', () => {
  it('produces format {deviceId}-{isoTimestamp}-{sequenceNumber}', () => {
    const result = generateCorrelationId('dev-123', '2023-11-14T22:13:20.000Z', '00001');
    expect(result).toBe('dev-123-2023-11-14T22:13:20.000Z-00001');
  });

  it('handles device IDs with special characters', () => {
    const result = generateCorrelationId('device/zone-A', '2023-01-01T00:00:00.000Z', '999');
    expect(result).toBe('device/zone-A-2023-01-01T00:00:00.000Z-999');
  });

  it('handles empty sequence number', () => {
    const result = generateCorrelationId('dev', '2023-01-01T00:00:00.000Z', '');
    expect(result).toBe('dev-2023-01-01T00:00:00.000Z-');
  });
});

describe('classifySensorType', () => {
  it('classifies temperature as environmental', () => {
    expect(classifySensorType('temperature')).toBe('environmental');
  });

  it('classifies humidity as environmental', () => {
    expect(classifySensorType('humidity')).toBe('environmental');
  });

  it('classifies pressure as environmental', () => {
    expect(classifySensorType('pressure')).toBe('environmental');
  });

  it('classifies gps as location', () => {
    expect(classifySensorType('gps')).toBe('location');
  });

  it('classifies vibration as motion', () => {
    expect(classifySensorType('vibration')).toBe('motion');
  });

  it('classifies proximity as motion', () => {
    expect(classifySensorType('proximity')).toBe('motion');
  });

  it('returns unknown for unrecognized sensor type', () => {
    expect(classifySensorType('wind')).toBe('unknown');
  });
});

describe('enrichRecord', () => {
  const registry = new StubDeviceRegistry();

  it('produces correct enriched output for temperature sensor', async () => {
    const record = makeValidRecord({ sensorType: 'temperature' });
    const enriched = await enrichRecord(record, '12345', registry);

    expect(enriched.deviceId).toBe('sensor-001');
    expect(enriched.sensorType).toBe('temperature');
    expect(enriched.sensorClassification).toBe('environmental');
    expect(enriched.timestamp).toBe('2023-11-14T22:13:20.000Z');
    expect(enriched.originalTimestamp).toBe(1700000000000);
    expect(enriched.readings.value).toBe(23.5);
    expect(enriched.readings.unit).toBe('celsius');
    expect(enriched.correlationId).toBe('sensor-001-2023-11-14T22:13:20.000Z-12345');
    expect(enriched.location).toBeDefined();
    expect(enriched.location.facility.length).toBeGreaterThan(0);
    expect(enriched.processingMetadata.pipelineVersion).toBe('1.0.0');
  });

  it('produces correct enriched output for humidity sensor', async () => {
    const record = makeValidRecord({ sensorType: 'humidity', readings: { value: 65, unit: 'percent' } });
    const enriched = await enrichRecord(record, '100', registry);

    expect(enriched.sensorType).toBe('humidity');
    expect(enriched.sensorClassification).toBe('environmental');
    expect(enriched.readings.value).toBe(65);
    expect(enriched.readings.unit).toBe('percent');
  });

  it('produces correct enriched output for gps sensor', async () => {
    const record = makeValidRecord({ sensorType: 'gps', readings: { value: 40.7128, unit: 'degrees' } });
    const enriched = await enrichRecord(record, '200', registry);

    expect(enriched.sensorType).toBe('gps');
    expect(enriched.sensorClassification).toBe('location');
  });

  it('produces correct enriched output for vibration sensor', async () => {
    const record = makeValidRecord({ sensorType: 'vibration', readings: { value: 2.5, unit: 'm/s2' } });
    const enriched = await enrichRecord(record, '300', registry);

    expect(enriched.sensorType).toBe('vibration');
    expect(enriched.sensorClassification).toBe('motion');
  });

  it('produces correct enriched output for pressure sensor', async () => {
    const record = makeValidRecord({ sensorType: 'pressure', readings: { value: 1013.25, unit: 'hPa' } });
    const enriched = await enrichRecord(record, '400', registry);

    expect(enriched.sensorType).toBe('pressure');
    expect(enriched.sensorClassification).toBe('environmental');
  });

  it('produces correct enriched output for proximity sensor', async () => {
    const record = makeValidRecord({ sensorType: 'proximity', readings: { value: 0.5, unit: 'meters' } });
    const enriched = await enrichRecord(record, '500', registry);

    expect(enriched.sensorType).toBe('proximity');
    expect(enriched.sensorClassification).toBe('motion');
  });

  it('preserves original readings without modification', async () => {
    const record = makeValidRecord({
      readings: { value: -42.7, unit: 'custom-unit', secondary: { x: 1, y: 2 } },
    });
    const enriched = await enrichRecord(record, '600', registry);

    expect(enriched.readings.value).toBe(-42.7);
    expect(enriched.readings.unit).toBe('custom-unit');
    expect(enriched.readings.secondary).toEqual({ x: 1, y: 2 });
  });

  it('preserves metadata when present', async () => {
    const record = makeValidRecord({
      metadata: { firmwareVersion: '2.0.1', batteryLevel: 85, signalStrength: -70 },
    });
    const enriched = await enrichRecord(record, '700', registry);

    expect(enriched.metadata).toEqual({ firmwareVersion: '2.0.1', batteryLevel: 85, signalStrength: -70 });
  });

  it('handles epoch 0 timestamp correctly', async () => {
    const record = makeValidRecord({ timestamp: 0 });
    const enriched = await enrichRecord(record, '800', registry);

    expect(enriched.timestamp).toBe('1970-01-01T00:00:00.000Z');
    expect(enriched.originalTimestamp).toBe(0);
    expect(enriched.correlationId).toContain('1970-01-01T00:00:00.000Z');
  });

  it('handles year 2038 timestamp correctly', async () => {
    const record = makeValidRecord({ timestamp: 2145916800000 });
    const enriched = await enrichRecord(record, '900', registry);

    expect(enriched.timestamp).toBe('2038-01-01T00:00:00.000Z');
    expect(enriched.originalTimestamp).toBe(2145916800000);
  });

  it('throws EnrichmentError when device not found in registry', async () => {
    // Create a mock registry that returns null
    const nullRegistry: DeviceRegistry = {
      async getDevice(): Promise<DeviceRegistryEntry | null> {
        return null;
      },
    };

    const record = makeValidRecord({ deviceId: 'unknown-device' });

    await expect(enrichRecord(record, '1000', nullRegistry)).rejects.toThrow(EnrichmentError);

    try {
      await enrichRecord(record, '1000', nullRegistry);
    } catch (error) {
      expect(error).toBeInstanceOf(EnrichmentError);
      const enrichmentError = error as EnrichmentError;
      expect(enrichmentError.code).toBe('ENRICHMENT_FAILED');
      expect(enrichmentError.deviceId).toBe('unknown-device');
      expect(enrichmentError.message).toContain('unknown-device');
    }
  });

  it('includes deviceGroupId from registry in enriched record', async () => {
    const record = makeValidRecord();
    const enriched = await enrichRecord(record, '1100', registry);

    expect(enriched.deviceGroupId).toMatch(/^group-\d$/);
  });

  it('includes location from registry in enriched record', async () => {
    const record = makeValidRecord();
    const enriched = await enrichRecord(record, '1200', registry);

    expect(typeof enriched.location.latitude).toBe('number');
    expect(typeof enriched.location.longitude).toBe('number');
    expect(enriched.location.facility).toMatch(/^facility-/);
    expect(enriched.location.zone).toMatch(/^zone-[A-Z]$/);
  });
});
