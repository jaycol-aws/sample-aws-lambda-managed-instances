/**
 * Record enrichment logic for the LMI Processor.
 *
 * Converts validated telemetry records into enriched records by:
 * - Converting epoch millis timestamp to UTC ISO-8601 format
 * - Resolving device location metadata via DeviceRegistry interface
 * - Assigning sensor type classification
 * - Generating correlation ID
 * - Adding processing metadata
 *
 * Validates: Requirements 3.4
 */

import { TelemetryRecord, EnrichedRecord, DeviceRegistry } from './types';

/**
 * Sensor type classification mapping.
 * Groups individual sensor types into broader categories.
 */
const SENSOR_CLASSIFICATION_MAP: Record<string, string> = {
  temperature: 'environmental',
  humidity: 'environmental',
  pressure: 'environmental',
  gps: 'location',
  vibration: 'motion',
  proximity: 'motion',
};

/** Pipeline version constant */
const PIPELINE_VERSION = '1.0.0';

/**
 * Get the LMI instance ID from environment or generate a default.
 */
function getLmiInstanceId(): string {
  return process.env.LMI_INSTANCE_ID || 'lmi-default-instance';
}

/**
 * Convert epoch milliseconds to UTC ISO-8601 string.
 */
export function epochMillisToIso8601(epochMillis: number): string {
  return new Date(epochMillis).toISOString();
}

/**
 * Classify a sensor type into a broader category.
 */
export function classifySensorType(sensorType: string): string {
  return SENSOR_CLASSIFICATION_MAP[sensorType] || 'unknown';
}

/**
 * Generate a correlation ID in the format: {deviceId}-{isoTimestamp}-{sequenceNumber}
 */
export function generateCorrelationId(
  deviceId: string,
  isoTimestamp: string,
  sequenceNumber: string,
): string {
  return `${deviceId}-${isoTimestamp}-${sequenceNumber}`;
}

/**
 * Enrichment error thrown when device is not found in the registry.
 */
export class EnrichmentError extends Error {
  public readonly code: string;
  public readonly deviceId: string;

  constructor(message: string, code: string, deviceId: string) {
    super(message);
    this.name = 'EnrichmentError';
    this.code = code;
    this.deviceId = deviceId;
  }
}

/**
 * Enrich a validated telemetry record with location, classification,
 * correlation ID, and processing metadata.
 *
 * @param record - A validated TelemetryRecord
 * @param sequenceNumber - The Kinesis sequence number for this record
 * @param registry - The DeviceRegistry to resolve device metadata
 * @returns The enriched record
 * @throws EnrichmentError if the device is not found in the registry
 */
export async function enrichRecord(
  record: TelemetryRecord,
  sequenceNumber: string,
  registry: DeviceRegistry,
): Promise<EnrichedRecord> {
  // Resolve device metadata from registry
  const deviceEntry = await registry.getDevice(record.deviceId);

  if (!deviceEntry) {
    throw new EnrichmentError(
      `Device not found in registry: ${record.deviceId}`,
      'ENRICHMENT_FAILED',
      record.deviceId,
    );
  }

  // Convert epoch millis to UTC ISO-8601
  const isoTimestamp = epochMillisToIso8601(record.timestamp);

  // Generate correlation ID
  const correlationId = generateCorrelationId(
    record.deviceId,
    isoTimestamp,
    sequenceNumber,
  );

  // Classify sensor type
  const sensorClassification = classifySensorType(record.sensorType);

  // Build enriched record preserving all original reading values
  const enrichedRecord: EnrichedRecord = {
    correlationId,
    deviceId: record.deviceId,
    deviceGroupId: deviceEntry.deviceGroupId,
    timestamp: isoTimestamp,
    originalTimestamp: record.timestamp,
    sensorType: record.sensorType,
    sensorClassification,
    readings: record.readings,
    location: {
      latitude: deviceEntry.location.latitude,
      longitude: deviceEntry.location.longitude,
      facility: deviceEntry.location.facility,
      zone: deviceEntry.location.zone,
    },
    metadata: record.metadata,
    processingMetadata: {
      processedAt: new Date().toISOString(),
      pipelineVersion: PIPELINE_VERSION,
      lmiInstanceId: getLmiInstanceId(),
    },
  };

  return enrichedRecord;
}
