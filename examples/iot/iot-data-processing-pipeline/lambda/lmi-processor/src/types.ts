/**
 * Shared TypeScript types for the LMI Processor.
 *
 * Validates: Requirements 3.3, 3.4
 */

// --- Telemetry Record (raw from IoT device) ---

export interface TelemetryRecord {
  deviceId: string;
  timestamp: number; // Unix epoch milliseconds
  sensorType:
    | 'temperature'
    | 'humidity'
    | 'gps'
    | 'vibration'
    | 'pressure'
    | 'proximity';
  readings: {
    value: number;
    unit: string;
    secondary?: Record<string, unknown>;
  };
  metadata?: {
    firmwareVersion?: string;
    batteryLevel?: number; // 0-100
    signalStrength?: number; // RSSI in dBm
  };
}

// --- Enriched Record (output of LMI Processor) ---

export interface EnrichedRecord {
  correlationId: string; // "{deviceId}-{isoTimestamp}-{sequenceNumber}"
  deviceId: string;
  deviceGroupId: string;
  timestamp: string; // UTC ISO-8601
  originalTimestamp: number;
  sensorType: string;
  sensorClassification: string;
  readings: {
    value: number;
    unit: string;
    secondary?: Record<string, unknown>;
  };
  location: {
    latitude: number;
    longitude: number;
    facility: string;
    zone: string;
  };
  metadata?: {
    firmwareVersion?: string;
    batteryLevel?: number;
    signalStrength?: number;
  };
  processingMetadata: {
    processedAt: string; // UTC ISO-8601
    pipelineVersion: string;
    lmiInstanceId: string;
  };
}

// --- DLQ Record (failed validation → SQS) ---

export interface DLQRecord {
  originalPayload: string; // Base64-encoded original record
  errorDescription: string;
  errorCode: string; // "SCHEMA_VALIDATION_FAILED" | "PARSE_ERROR" | "ENRICHMENT_FAILED"
  deviceId: string | null;
  correlationId: string;
  failedAt: string; // UTC ISO-8601
  sourceStream: string;
  sequenceNumber: string;
}

// --- Device Registry ---

export interface DeviceRegistryEntry {
  deviceGroupId: string;
  location: {
    latitude: number;
    longitude: number;
    facility: string;
    zone: string;
  };
  sensorClassification: string;
}

export interface DeviceRegistry {
  getDevice(deviceId: string): Promise<DeviceRegistryEntry | null>;
}

// --- Kinesis Event types for Lambda handler ---

export interface KinesisEventRecord {
  kinesis: {
    data: string;
    sequenceNumber: string;
    partitionKey: string;
  };
  eventID: string;
  eventSourceARN: string;
}

export interface KinesisEvent {
  Records: KinesisEventRecord[];
}

export interface BatchItemFailure {
  itemIdentifier: string;
}

export interface KinesisStreamHandlerResponse {
  batchItemFailures: BatchItemFailure[];
}
