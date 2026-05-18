package com.iotpipeline;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.io.Serializable;

/**
 * Flink POJO representing the output of the StatisticsAggregator.
 * Contains per-device-group, per-sensor-type statistics for a tumbling window.
 * Written to DynamoDB via the IdempotentDynamoDBSink.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class AnalyticsResult implements Serializable {

    private static final long serialVersionUID = 1L;

    private String deviceGroupId;
    private long windowEndTimestamp;
    private long windowDurationSeconds;
    private String sensorType;
    private Statistics statistics;
    private int deviceCount;
    private int alertsEmitted;

    public AnalyticsResult() {
    }

    // --- Getters and Setters ---

    public String getDeviceGroupId() {
        return deviceGroupId;
    }

    public void setDeviceGroupId(String deviceGroupId) {
        this.deviceGroupId = deviceGroupId;
    }

    public long getWindowEndTimestamp() {
        return windowEndTimestamp;
    }

    public void setWindowEndTimestamp(long windowEndTimestamp) {
        this.windowEndTimestamp = windowEndTimestamp;
    }

    public long getWindowDurationSeconds() {
        return windowDurationSeconds;
    }

    public void setWindowDurationSeconds(long windowDurationSeconds) {
        this.windowDurationSeconds = windowDurationSeconds;
    }

    public String getSensorType() {
        return sensorType;
    }

    public void setSensorType(String sensorType) {
        this.sensorType = sensorType;
    }

    public Statistics getStatistics() {
        return statistics;
    }

    public void setStatistics(Statistics statistics) {
        this.statistics = statistics;
    }

    public int getDeviceCount() {
        return deviceCount;
    }

    public void setDeviceCount(int deviceCount) {
        this.deviceCount = deviceCount;
    }

    public int getAlertsEmitted() {
        return alertsEmitted;
    }

    public void setAlertsEmitted(int alertsEmitted) {
        this.alertsEmitted = alertsEmitted;
    }

    // --- Nested Statistics POJO ---

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Statistics implements Serializable {

        private static final long serialVersionUID = 1L;

        private double average;
        private double min;
        private double max;
        private double stddev;
        private long count;

        public Statistics() {
        }

        public double getAverage() {
            return average;
        }

        public void setAverage(double average) {
            this.average = average;
        }

        public double getMin() {
            return min;
        }

        public void setMin(double min) {
            this.min = min;
        }

        public double getMax() {
            return max;
        }

        public void setMax(double max) {
            this.max = max;
        }

        public double getStddev() {
            return stddev;
        }

        public void setStddev(double stddev) {
            this.stddev = stddev;
        }

        public long getCount() {
            return count;
        }

        public void setCount(long count) {
            this.count = count;
        }
    }
}
