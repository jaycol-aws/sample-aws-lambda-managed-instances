package com.aws.lmi.etl.model;

import java.util.Map;

public class AggregationResponse {

    private String deviceId;
    private int recordCount;
    private Map<String, MetricSummary> metrics;
    private String outputS3Key;
    private long processingTimeMs;

    public AggregationResponse() {}

    public AggregationResponse(String deviceId, int recordCount, Map<String, MetricSummary> metrics,
                               String outputS3Key, long processingTimeMs) {
        this.deviceId = deviceId;
        this.recordCount = recordCount;
        this.metrics = metrics;
        this.outputS3Key = outputS3Key;
        this.processingTimeMs = processingTimeMs;
    }

    public String getDeviceId() { return deviceId; }
    public void setDeviceId(String deviceId) { this.deviceId = deviceId; }
    public int getRecordCount() { return recordCount; }
    public void setRecordCount(int recordCount) { this.recordCount = recordCount; }
    public Map<String, MetricSummary> getMetrics() { return metrics; }
    public void setMetrics(Map<String, MetricSummary> metrics) { this.metrics = metrics; }
    public String getOutputS3Key() { return outputS3Key; }
    public void setOutputS3Key(String outputS3Key) { this.outputS3Key = outputS3Key; }
    public long getProcessingTimeMs() { return processingTimeMs; }
    public void setProcessingTimeMs(long processingTimeMs) { this.processingTimeMs = processingTimeMs; }

    public static class MetricSummary {
        private double min;
        private double max;
        private double avg;
        private double stdDev;
        private double p95;

        public MetricSummary() {}

        public MetricSummary(double min, double max, double avg, double stdDev, double p95) {
            this.min = min;
            this.max = max;
            this.avg = avg;
            this.stdDev = stdDev;
            this.p95 = p95;
        }

        public double getMin() { return min; }
        public void setMin(double min) { this.min = min; }
        public double getMax() { return max; }
        public void setMax(double max) { this.max = max; }
        public double getAvg() { return avg; }
        public void setAvg(double avg) { this.avg = avg; }
        public double getStdDev() { return stdDev; }
        public void setStdDev(double stdDev) { this.stdDev = stdDev; }
        public double getP95() { return p95; }
        public void setP95(double p95) { this.p95 = p95; }
    }
}
