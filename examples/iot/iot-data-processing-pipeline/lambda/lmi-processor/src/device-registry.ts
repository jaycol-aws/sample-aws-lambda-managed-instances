/**
 * DeviceRegistry interface + StubDeviceRegistry.
 *
 * The StubDeviceRegistry returns deterministic metadata derived from the device ID.
 * It has NO mutable state and is safe for concurrent access across
 * multiple LMI invocations within the same execution environment.
 *
 * Validates: Requirements 3.4
 */

import { DeviceRegistry, DeviceRegistryEntry } from './types';

const SENSOR_CLASSIFICATIONS: readonly string[] = [
  'environmental',
  'motion',
  'location',
  'industrial',
  'proximity',
  'structural',
] as const;

const FACILITIES: readonly string[] = [
  'facility-alpha',
  'facility-bravo',
  'facility-charlie',
  'facility-delta',
  'facility-echo',
] as const;

/**
 * Simple deterministic hash of a string to a non-negative 32-bit integer.
 * Uses the djb2 algorithm — fast, well-distributed, and purely functional.
 */
function simpleHash(input: string): number {
  let hash = 5381;
  for (let i = 0; i < input.length; i++) {
    // hash * 33 + charCode, kept within 32-bit range
    hash = ((hash << 5) + hash + input.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

/**
 * Stub implementation of DeviceRegistry for development and testing.
 *
 * - Returns deterministic metadata derived solely from the device ID
 * - Always returns a result (never null) for any device ID
 * - Has NO mutable state — all data is computed from the input
 * - Safe for concurrent access (read-only, pure functions)
 */
export class StubDeviceRegistry implements DeviceRegistry {
  async getDevice(deviceId: string): Promise<DeviceRegistryEntry | null> {
    const hash = simpleHash(deviceId);

    return {
      deviceGroupId: `group-${hash % 10}`,
      location: {
        latitude: 40.0 + (hash % 100) / 10,
        longitude: -74.0 + (hash % 100) / 10,
        facility: FACILITIES[hash % FACILITIES.length],
        zone: `zone-${String.fromCharCode(65 + (hash % 26))}`,
      },
      sensorClassification: SENSOR_CLASSIFICATIONS[hash % SENSOR_CLASSIFICATIONS.length],
    };
  }
}
