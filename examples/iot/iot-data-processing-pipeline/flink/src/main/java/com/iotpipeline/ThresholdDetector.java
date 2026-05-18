package com.iotpipeline;

import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.util.Collector;

import java.io.Serializable;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/**
 * Flink FlatMapFunction that evaluates configurable threshold rules against
 * computed statistics from the StatisticsAggregator. When a statistic exceeds
 * a configured threshold, an AlertRecord is emitted.
 *
 * No alert is emitted when all statistics are within bounds.
 *
 * Validates: Requirements 4.3
 */
public class ThresholdDetector implements FlatMapFunction<AnalyticsResult, AlertRecord> {

    private static final long serialVersionUID = 1L;

    private static final DateTimeFormatter ISO_FORMATTER =
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
                    .withZone(ZoneOffset.UTC);

    private final List<ThresholdRule> rules;

    /**
     * Creates a ThresholdDetector with the given threshold rules.
     *
     * @param rules list of threshold rules to evaluate against each AnalyticsResult
     */
    public ThresholdDetector(List<ThresholdRule> rules) {
        this.rules = rules != null ? new ArrayList<>(rules) : Collections.emptyList();
    }

    @Override
    public void flatMap(AnalyticsResult result, Collector<AlertRecord> out) throws Exception {
        if (result == null || result.getStatistics() == null || result.getSensorType() == null) {
            return;
        }

        for (ThresholdRule rule : rules) {
            if (!rule.getSensorType().equals(result.getSensorType())) {
                continue;
            }

            double actualValue = getMetricValue(result.getStatistics(), rule.getMetric());
            if (Double.isNaN(actualValue)) {
                continue;
            }

            if (isThresholdExceeded(actualValue, rule.getThresholdValue(), rule.getOperator())) {
                AlertRecord alert = new AlertRecord();
                alert.setAlertId(UUID.randomUUID().toString());
                alert.setDeviceGroupId(result.getDeviceGroupId());
                alert.setSensorType(result.getSensorType());
                alert.setAlertType("threshold_breach");
                alert.setSeverity(rule.getSeverity());

                AlertRecord.Threshold threshold = new AlertRecord.Threshold();
                threshold.setMetric(rule.getMetric());
                threshold.setConfiguredValue(rule.getThresholdValue());
                threshold.setActualValue(actualValue);
                alert.setThreshold(threshold);

                alert.setWindowEndTimestamp(result.getWindowEndTimestamp());
                alert.setDetectedAt(ISO_FORMATTER.format(Instant.now()));
                alert.setAffectedDevices(Collections.emptyList());

                out.collect(alert);
            }
        }
    }

    /**
     * Extracts the metric value from the statistics based on the metric name.
     *
     * @param statistics the computed statistics
     * @param metric the metric name (average, max, min, stddev)
     * @return the metric value, or NaN if the metric is unknown
     */
    private double getMetricValue(AnalyticsResult.Statistics statistics, String metric) {
        if (metric == null) {
            return Double.NaN;
        }
        switch (metric.toLowerCase()) {
            case "average":
                return statistics.getAverage();
            case "max":
                return statistics.getMax();
            case "min":
                return statistics.getMin();
            case "stddev":
                return statistics.getStddev();
            default:
                return Double.NaN;
        }
    }

    /**
     * Determines whether the actual value exceeds the threshold based on the operator.
     * Currently supports GREATER_THAN (strictly greater than).
     *
     * @param actualValue the computed statistic value
     * @param thresholdValue the configured threshold value
     * @param operator the comparison operator
     * @return true if the threshold is exceeded
     */
    private boolean isThresholdExceeded(double actualValue, double thresholdValue, Operator operator) {
        if (operator == null) {
            return false;
        }
        switch (operator) {
            case GREATER_THAN:
                return actualValue > thresholdValue;
            default:
                return false;
        }
    }

    // --- ThresholdRule inner class ---

    /**
     * Represents a configurable threshold rule.
     * Specifies which sensor type and metric to monitor, the threshold value,
     * the comparison operator, and the severity of the resulting alert.
     */
    public static class ThresholdRule implements Serializable {

        private static final long serialVersionUID = 1L;

        private final String sensorType;
        private final String metric;
        private final double thresholdValue;
        private final Operator operator;
        private final String severity;

        /**
         * Creates a new ThresholdRule.
         *
         * @param sensorType the sensor type this rule applies to (e.g., "temperature")
         * @param metric the statistic to evaluate (average, max, min, stddev)
         * @param thresholdValue the threshold value
         * @param operator the comparison operator
         * @param severity the alert severity (critical, warning, info)
         */
        public ThresholdRule(String sensorType, String metric, double thresholdValue,
                             Operator operator, String severity) {
            this.sensorType = sensorType;
            this.metric = metric;
            this.thresholdValue = thresholdValue;
            this.operator = operator;
            this.severity = severity;
        }

        public String getSensorType() {
            return sensorType;
        }

        public String getMetric() {
            return metric;
        }

        public double getThresholdValue() {
            return thresholdValue;
        }

        public Operator getOperator() {
            return operator;
        }

        public String getSeverity() {
            return severity;
        }
    }

    // --- Operator enum ---

    /**
     * Comparison operators for threshold evaluation.
     */
    public enum Operator {
        GREATER_THAN
    }
}
