package com.iotpipeline;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.io.Serializable;
import java.util.Map;

/**
 * Flink POJO representing an enriched telemetry record from the LMI processor.
 * Deserialized from the iot-enriched-telemetry-stream Kinesis Data Stream.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class EnrichedRecord implements Serializable {

    private static final long serialVersionUID = 1L;

    private String correlationId;
    private String deviceId;
    private String deviceGroupId;
    private String timestamp;
    private long originalTimestamp;
    private String sensorType;
    private String sensorClassification;
    private Readings readings;
    private Location location;
    private Metadata metadata;
    private ProcessingMetadata processingMetadata;

    public EnrichedRecord() {
    }

    // --- Getters and Setters ---

    public String getCorrelationId() {
        return correlationId;
    }

    public void setCorrelationId(String correlationId) {
        this.correlationId = correlationId;
    }

    public String getDeviceId() {
        return deviceId;
    }

    public void setDeviceId(String deviceId) {
        this.deviceId = deviceId;
    }

    public String getDeviceGroupId() {
        return deviceGroupId;
    }

    public void setDeviceGroupId(String deviceGroupId) {
        this.deviceGroupId = deviceGroupId;
    }

    public String getTimestamp() {
        return timestamp;
    }

    public void setTimestamp(String timestamp) {
        this.timestamp = timestamp;
    }

    public long getOriginalTimestamp() {
        return originalTimestamp;
    }

    public void setOriginalTimestamp(long originalTimestamp) {
        this.originalTimestamp = originalTimestamp;
    }

    public String getSensorType() {
        return sensorType;
    }

    public void setSensorType(String sensorType) {
        this.sensorType = sensorType;
    }

    public String getSensorClassification() {
        return sensorClassification;
    }

    public void setSensorClassification(String sensorClassification) {
        this.sensorClassification = sensorClassification;
    }

    public Readings getReadings() {
        return readings;
    }

    public void setReadings(Readings readings) {
        this.readings = readings;
    }

    public Location getLocation() {
        return location;
    }

    public void setLocation(Location location) {
        this.location = location;
    }

    public Metadata getMetadata() {
        return metadata;
    }

    public void setMetadata(Metadata metadata) {
        this.metadata = metadata;
    }

    public ProcessingMetadata getProcessingMetadata() {
        return processingMetadata;
    }

    public void setProcessingMetadata(ProcessingMetadata processingMetadata) {
        this.processingMetadata = processingMetadata;
    }

    // --- Nested POJOs ---

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Readings implements Serializable {
        private static final long serialVersionUID = 1L;

        private double value;
        private String unit;
        private Map<String, Object> secondary;

        public Readings() {
        }

        public double getValue() {
            return value;
        }

        public void setValue(double value) {
            this.value = value;
        }

        public String getUnit() {
            return unit;
        }

        public void setUnit(String unit) {
            this.unit = unit;
        }

        public Map<String, Object> getSecondary() {
            return secondary;
        }

        public void setSecondary(Map<String, Object> secondary) {
            this.secondary = secondary;
        }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Location implements Serializable {
        private static final long serialVersionUID = 1L;

        private double latitude;
        private double longitude;
        private String facility;
        private String zone;

        public Location() {
        }

        public double getLatitude() {
            return latitude;
        }

        public void setLatitude(double latitude) {
            this.latitude = latitude;
        }

        public double getLongitude() {
            return longitude;
        }

        public void setLongitude(double longitude) {
            this.longitude = longitude;
        }

        public String getFacility() {
            return facility;
        }

        public void setFacility(String facility) {
            this.facility = facility;
        }

        public String getZone() {
            return zone;
        }

        public void setZone(String zone) {
            this.zone = zone;
        }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Metadata implements Serializable {
        private static final long serialVersionUID = 1L;

        private String firmwareVersion;
        private double batteryLevel;
        private double signalStrength;

        public Metadata() {
        }

        public String getFirmwareVersion() {
            return firmwareVersion;
        }

        public void setFirmwareVersion(String firmwareVersion) {
            this.firmwareVersion = firmwareVersion;
        }

        public double getBatteryLevel() {
            return batteryLevel;
        }

        public void setBatteryLevel(double batteryLevel) {
            this.batteryLevel = batteryLevel;
        }

        public double getSignalStrength() {
            return signalStrength;
        }

        public void setSignalStrength(double signalStrength) {
            this.signalStrength = signalStrength;
        }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class ProcessingMetadata implements Serializable {
        private static final long serialVersionUID = 1L;

        private String processedAt;
        private String pipelineVersion;
        private String lmiInstanceId;

        public ProcessingMetadata() {
        }

        public String getProcessedAt() {
            return processedAt;
        }

        public void setProcessedAt(String processedAt) {
            this.processedAt = processedAt;
        }

        public String getPipelineVersion() {
            return pipelineVersion;
        }

        public void setPipelineVersion(String pipelineVersion) {
            this.pipelineVersion = pipelineVersion;
        }

        public String getLmiInstanceId() {
            return lmiInstanceId;
        }

        public void setLmiInstanceId(String lmiInstanceId) {
            this.lmiInstanceId = lmiInstanceId;
        }
    }
}
