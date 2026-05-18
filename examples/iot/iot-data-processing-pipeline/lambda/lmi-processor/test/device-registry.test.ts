import { StubDeviceRegistry } from '../src/device-registry';

describe('StubDeviceRegistry', () => {
  const registry = new StubDeviceRegistry();

  it('returns deterministic metadata for the same device ID', async () => {
    const result1 = await registry.getDevice('sensor-001');
    const result2 = await registry.getDevice('sensor-001');
    expect(result1).toEqual(result2);
  });

  it('returns a non-null result for any device ID', async () => {
    const result = await registry.getDevice('any-device-id');
    expect(result).not.toBeNull();
  });

  it('returns different metadata for different device IDs', async () => {
    const a = await registry.getDevice('device-aaa');
    const b = await registry.getDevice('device-zzz');
    // At least one field should differ for distinct IDs
    const identical =
      a!.deviceGroupId === b!.deviceGroupId &&
      a!.location.latitude === b!.location.latitude &&
      a!.sensorClassification === b!.sensorClassification;
    expect(identical).toBe(false);
  });

  it('returns a valid DeviceRegistryEntry shape', async () => {
    const entry = await registry.getDevice('test-device');
    expect(entry).toBeDefined();
    expect(entry!.deviceGroupId).toMatch(/^group-\d$/);
    expect(typeof entry!.location.latitude).toBe('number');
    expect(typeof entry!.location.longitude).toBe('number');
    expect(entry!.location.facility).toMatch(/^facility-/);
    expect(entry!.location.zone).toMatch(/^zone-[A-Z]$/);
    expect(typeof entry!.sensorClassification).toBe('string');
    expect(entry!.sensorClassification.length).toBeGreaterThan(0);
  });

  it('produces latitude in a reasonable range', async () => {
    const entry = await registry.getDevice('geo-device');
    expect(entry!.location.latitude).toBeGreaterThanOrEqual(40.0);
    expect(entry!.location.latitude).toBeLessThan(50.0);
  });

  it('produces longitude in a reasonable range', async () => {
    const entry = await registry.getDevice('geo-device');
    expect(entry!.location.longitude).toBeGreaterThanOrEqual(-74.0);
    expect(entry!.location.longitude).toBeLessThan(-64.0);
  });
});


describe('StubDeviceRegistry — deterministic metadata', () => {
  const registry = new StubDeviceRegistry();

  it('returns consistent deviceGroupId for the same device across multiple calls', async () => {
    const calls = await Promise.all(
      Array.from({ length: 10 }, () => registry.getDevice('consistency-test')),
    );
    const groupIds = calls.map((c) => c!.deviceGroupId);
    expect(new Set(groupIds).size).toBe(1);
  });

  it('returns consistent location for the same device across multiple calls', async () => {
    const calls = await Promise.all(
      Array.from({ length: 5 }, () => registry.getDevice('location-test')),
    );
    for (const call of calls) {
      expect(call!.location).toEqual(calls[0]!.location);
    }
  });

  it('deviceGroupId follows group-N pattern where N is 0-9', async () => {
    const deviceIds = ['a', 'b', 'c', 'device-100', 'sensor-xyz', 'test-999'];
    for (const id of deviceIds) {
      const entry = await registry.getDevice(id);
      expect(entry!.deviceGroupId).toMatch(/^group-[0-9]$/);
    }
  });

  it('facility is one of the known facilities', async () => {
    const knownFacilities = [
      'facility-alpha',
      'facility-bravo',
      'facility-charlie',
      'facility-delta',
      'facility-echo',
    ];
    const deviceIds = ['dev-1', 'dev-2', 'dev-3', 'dev-4', 'dev-5'];
    for (const id of deviceIds) {
      const entry = await registry.getDevice(id);
      expect(knownFacilities).toContain(entry!.location.facility);
    }
  });

  it('zone follows zone-{A-Z} pattern', async () => {
    const deviceIds = ['zone-test-1', 'zone-test-2', 'zone-test-3'];
    for (const id of deviceIds) {
      const entry = await registry.getDevice(id);
      expect(entry!.location.zone).toMatch(/^zone-[A-Z]$/);
    }
  });

  it('sensorClassification is one of the known classifications', async () => {
    const knownClassifications = [
      'environmental',
      'motion',
      'location',
      'industrial',
      'proximity',
      'structural',
    ];
    const deviceIds = ['class-1', 'class-2', 'class-3', 'class-4', 'class-5', 'class-6'];
    for (const id of deviceIds) {
      const entry = await registry.getDevice(id);
      expect(knownClassifications).toContain(entry!.sensorClassification);
    }
  });
});
