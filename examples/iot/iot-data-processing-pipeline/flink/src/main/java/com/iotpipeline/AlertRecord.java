package com.iotpipeline;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.io.Serializable;
import java.util.List;

/**
 * POJO representing an alert emitted when a threshold rule is breached.
 * Produced by the ThresholdDetector and written to the iot-alerts-stream.
 *
 * Validates: Requirements 4.3
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class AlertRecord implements Serializable {

    private static final long serialVersionUID = 1L;

    private String alertId;
    private String deviceGroupId;
    private String sensorType;
    private String alertType;
    private String severity;
    private Threshold threshold;
    private long windowEndTimestamp;
    private String detectedAt;
    private List<String> affectedDevices;

    public AlertRecord() {
    }

    // --- Getters and Setters ---

    public String getAlertId() {
        return alertId;
    }

    public void setAlertId(String alertId) {
        this.alertId = alertId;
    }

    public String getDeviceGroupId() {
        return deviceGroupId;
    }

    public void setDeviceGroupId(String deviceGroupId) {
        this.deviceGroupId = deviceGroupId;
    }

    public String getSensorType() {
        return sensorType;
    }

    public void setSensorType(String sensorType) {
        this.sensorType = sensorType;
    }

    public String getAlertType() {
        return alertType;
    }

    public void setAlertType(String alertType) {
        this.alertType = alertType;
    }

    public String getSeverity() {
        return severity;
    }

    public void setSeverity(String severity) {
        this.severity = severity;
    }

    public Threshold getThreshold() {
        return threshold;
    }

    public void setThreshold(Threshold threshold) {
        this.threshold = threshold;
    }

    public long getWindowEndTimestamp() {
        return windowEndTimestamp;
    }

    public void setWindowEndTimestamp(long windowEndTimestamp) {
        this.windowEndTimestamp = windowEndTimestamp;
    }

    public String getDetectedAt() {
        return detectedAt;
    }

    public void setDetectedAt(String detectedAt) {
        this.detectedAt = detectedAt;
    }

    public List<String> getAffectedDevices() {
        return affectedDevices;
    }

    public void setAffectedDevices(List<String> affectedDevices) {
        this.affectedDevices = affectedDevices;
    }

    // --- Nested Threshold POJO ---

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Threshold implements Serializable {

        private static final long serialVersionUID = 1L;

        private String metric;
        private double configuredValue;
        private double actualValue;

        public Threshold() {
        }

        public String getMetric() {
            return metric;
        }

        public void setMetric(String metric) {
            this.metric = metric;
        }

        public double getConfiguredValue() {
            return configuredValue;
        }

        public void setConfiguredValue(double configuredValue) {
            this.configuredValue = configuredValue;
        }

        public double getActualValue() {
            return actualValue;
        }

        public void setActualValue(double actualValue) {
            this.actualValue = actualValue;
        }
    }
}
