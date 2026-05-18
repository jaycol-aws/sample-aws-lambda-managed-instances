package com.iotpipeline;

import org.apache.flink.util.Collector;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Random;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit and property-based tests for ThresholdDetector.
 *
 * <p><b>Validates: Requirements 4.3</b></p>
 * <p>Property 4: Alert emission correctness —
 * For any set of enriched sensor readings within a processing window and a configured threshold,
 * the Flink threshold detection function SHALL emit an alert record if and only if the computed
 * statistic exceeds the threshold value. No alert SHALL be emitted when all statistics are within bounds.</p>
 */
class ThresholdDetectorTest {

    // --- Helper: List-based Collector for capturing emitted AlertRecords ---

    private static class ListCollector implements Collector<AlertRecord> {
        private final List<AlertRecord> collected = new ArrayList<>();

        @Override
        public void collect(AlertRecord record) {
            collected.add(record);
        }

        @Override
        public void close() {
            // no-op
        }

        public List<AlertRecord> getCollected() {
            return collected;
        }
    }

    // --- Helper: Create an AnalyticsResult with given parameters ---

    private static AnalyticsResult createAnalyticsResult(String deviceGroupId, String sensorType,
                                                          double average, double min, double max,
                                                          double stddev, long count) {
        AnalyticsResult result = new AnalyticsResult();
        result.setDeviceGroupId(deviceGroupId);
        result.setSensorType(sensorType);
        result.setWindowEndTimestamp(System.currentTimeMillis());
        result.setWindowDurationSeconds(60);
        result.setDeviceCount(1);
        result.setAlertsEmitted(0);

        AnalyticsResult.Statistics stats = new AnalyticsResult.Statistics();
        stats.setAverage(average);
        stats.setMin(min);
        stats.setMax(max);
        stats.setStddev(stddev);
        stats.setCount(count);
        result.setStatistics(stats);

        return result;
    }

    // --- Test 1: Alert emitted when average exceeds threshold ---

    @Test
    @DisplayName("Alert emitted when average exceeds threshold")
    void testAlertEmittedWhenAverageExceedsThreshold() throws Exception {
        ThresholdDetector.ThresholdRule rule = new ThresholdDetector.ThresholdRule(
                "temperature", "average", 50.0, ThresholdDetector.Operator.GREATER_THAN, "critical");
        ThresholdDetector detector = new ThresholdDetector(Collections.singletonList(rule));

        AnalyticsResult result = createAnalyticsResult("group-1", "temperature",
                55.0, 40.0, 70.0, 5.0, 10);

        ListCollector collector = new ListCollector();
        detector.flatMap(result, collector);

        assertEquals(1, collector.getCollected().size());
        AlertRecord alert = collector.getCollected().get(0);
        assertEquals("group-1", alert.getDeviceGroupId());
        assertEquals("temperature", alert.getSensorType());
        assertEquals("threshold_breach", alert.getAlertType());
        assertEquals("critical", alert.getSeverity());
        assertEquals("average", alert.getThreshold().getMetric());
        assertEquals(50.0, alert.getThreshold().getConfiguredValue());
        assertEquals(55.0, alert.getThreshold().getActualValue());
    }

    // --- Test 2: No alert when average is below threshold ---

    @Test
    @DisplayName("No alert when average is below threshold")
    void testNoAlertWhenAverageBelowThreshold() throws Exception {
        ThresholdDetector.ThresholdRule rule = new ThresholdDetector.ThresholdRule(
                "temperature", "average", 50.0, ThresholdDetector.Operator.GREATER_THAN, "critical");
        ThresholdDetector detector = new ThresholdDetector(Collections.singletonList(rule));

        AnalyticsResult result = createAnalyticsResult("group-1", "temperature",
                45.0, 30.0, 48.0, 3.0, 10);

        ListCollector collector = new ListCollector();
        detector.flatMap(result, collector);

        assertTrue(collector.getCollected().isEmpty());
    }

    // --- Test 3: No alert when value exactly equals threshold (boundary condition) ---

    @Test
    @DisplayName("No alert when value exactly equals threshold (strictly greater than)")
    void testNoAlertWhenValueExactlyEqualsThreshold() throws Exception {
        ThresholdDetector.ThresholdRule rule = new ThresholdDetector.ThresholdRule(
                "temperature", "average", 50.0, ThresholdDetector.Operator.GREATER_THAN, "warning");
        ThresholdDetector detector = new ThresholdDetector(Collections.singletonList(rule));

        AnalyticsResult result = createAnalyticsResult("group-1", "temperature",
                50.0, 40.0, 60.0, 4.0, 10);

        ListCollector collector = new ListCollector();
        detector.flatMap(result, collector);

        assertTrue(collector.getCollected().isEmpty(),
                "No alert should be emitted when actual value equals threshold (strictly greater than)");
    }

    // --- Test 4: Alert emitted for max metric exceeding threshold ---

    @Test
    @DisplayName("Alert emitted for max metric exceeding threshold")
    void testAlertEmittedForMaxMetricExceedingThreshold() throws Exception {
        ThresholdDetector.ThresholdRule rule = new ThresholdDetector.ThresholdRule(
                "humidity", "max", 80.0, ThresholdDetector.Operator.GREATER_THAN, "warning");
        ThresholdDetector detector = new ThresholdDetector(Collections.singletonList(rule));

        AnalyticsResult result = createAnalyticsResult("group-2", "humidity",
                60.0, 40.0, 95.0, 10.0, 20);

        ListCollector collector = new ListCollector();
        detector.flatMap(result, collector);

        assertEquals(1, collector.getCollected().size());
        AlertRecord alert = collector.getCollected().get(0);
        assertEquals("max", alert.getThreshold().getMetric());
        assertEquals(80.0, alert.getThreshold().getConfiguredValue());
        assertEquals(95.0, alert.getThreshold().getActualValue());
    }

    // --- Test 5: Alert emitted for stddev metric exceeding threshold ---

    @Test
    @DisplayName("Alert emitted for stddev metric exceeding threshold")
    void testAlertEmittedForStddevMetricExceedingThreshold() throws Exception {
        ThresholdDetector.ThresholdRule rule = new ThresholdDetector.ThresholdRule(
                "vibration", "stddev", 2.0, ThresholdDetector.Operator.GREATER_THAN, "info");
        ThresholdDetector detector = new ThresholdDetector(Collections.singletonList(rule));

        AnalyticsResult result = createAnalyticsResult("group-3", "vibration",
                5.0, 1.0, 10.0, 3.5, 15);

        ListCollector collector = new ListCollector();
        detector.flatMap(result, collector);

        assertEquals(1, collector.getCollected().size());
        AlertRecord alert = collector.getCollected().get(0);
        assertEquals("stddev", alert.getThreshold().getMetric());
        assertEquals(2.0, alert.getThreshold().getConfiguredValue());
        assertEquals(3.5, alert.getThreshold().getActualValue());
        assertEquals("info", alert.getSeverity());
    }

    // --- Test 6: No alert when sensor type doesn't match any rule ---

    @Test
    @DisplayName("No alert when sensor type doesn't match any rule")
    void testNoAlertWhenSensorTypeDoesNotMatch() throws Exception {
        ThresholdDetector.ThresholdRule rule = new ThresholdDetector.ThresholdRule(
                "temperature", "average", 50.0, ThresholdDetector.Operator.GREATER_THAN, "critical");
        ThresholdDetector detector = new ThresholdDetector(Collections.singletonList(rule));

        // Result has sensor type "humidity" which doesn't match the rule's "temperature"
        AnalyticsResult result = createAnalyticsResult("group-1", "humidity",
                99.0, 90.0, 100.0, 5.0, 10);

        ListCollector collector = new ListCollector();
        detector.flatMap(result, collector);

        assertTrue(collector.getCollected().isEmpty(),
                "No alert should be emitted when sensor type doesn't match any rule");
    }

    // --- Test 7: Multiple rules — only matching rules trigger alerts ---

    @Test
    @DisplayName("Multiple rules — only matching rules trigger alerts")
    void testMultipleRulesOnlyMatchingTrigger() throws Exception {
        List<ThresholdDetector.ThresholdRule> rules = Arrays.asList(
                new ThresholdDetector.ThresholdRule(
                        "temperature", "average", 50.0, ThresholdDetector.Operator.GREATER_THAN, "critical"),
                new ThresholdDetector.ThresholdRule(
                        "temperature", "max", 100.0, ThresholdDetector.Operator.GREATER_THAN, "warning"),
                new ThresholdDetector.ThresholdRule(
                        "humidity", "average", 70.0, ThresholdDetector.Operator.GREATER_THAN, "info")
        );
        ThresholdDetector detector = new ThresholdDetector(rules);

        // Temperature result: average=55 (exceeds 50), max=90 (does NOT exceed 100)
        AnalyticsResult tempResult = createAnalyticsResult("group-1", "temperature",
                55.0, 30.0, 90.0, 8.0, 10);

        ListCollector collector = new ListCollector();
        detector.flatMap(tempResult, collector);

        // Only the average rule should trigger, not the max rule
        assertEquals(1, collector.getCollected().size());
        assertEquals("average", collector.getCollected().get(0).getThreshold().getMetric());
        assertEquals("critical", collector.getCollected().get(0).getSeverity());
    }

    // --- Test 8: Random test (100 iterations) — alert emitted iff actual > threshold ---

    @Test
    @DisplayName("Property 4: Random test (100 iterations) — alert emitted iff actual > threshold")
    void testRandomAlertEmissionCorrectness() throws Exception {
        Random random = new Random(42L); // Fixed seed for reproducibility
        String[] metrics = {"average", "max", "min", "stddev"};
        String[] sensorTypes = {"temperature", "humidity", "vibration", "pressure", "proximity"};

        for (int iteration = 0; iteration < 100; iteration++) {
            // Generate random threshold and actual values
            double thresholdValue = (random.nextDouble() - 0.3) * 200.0; // Range: -60 to 140
            double average = (random.nextDouble() - 0.3) * 200.0;
            double min = average - random.nextDouble() * 50.0;
            double max = average + random.nextDouble() * 50.0;
            double stddev = random.nextDouble() * 20.0;

            String metric = metrics[random.nextInt(metrics.length)];
            String sensorType = sensorTypes[random.nextInt(sensorTypes.length)];

            ThresholdDetector.ThresholdRule rule = new ThresholdDetector.ThresholdRule(
                    sensorType, metric, thresholdValue, ThresholdDetector.Operator.GREATER_THAN, "warning");
            ThresholdDetector detector = new ThresholdDetector(Collections.singletonList(rule));

            AnalyticsResult result = createAnalyticsResult("group-random", sensorType,
                    average, min, max, stddev, 10);

            ListCollector collector = new ListCollector();
            detector.flatMap(result, collector);

            // Determine expected actual value based on metric
            double actualValue;
            switch (metric) {
                case "average":
                    actualValue = average;
                    break;
                case "max":
                    actualValue = max;
                    break;
                case "min":
                    actualValue = min;
                    break;
                case "stddev":
                    actualValue = stddev;
                    break;
                default:
                    actualValue = Double.NaN;
            }

            boolean shouldAlert = actualValue > thresholdValue;

            if (shouldAlert) {
                assertEquals(1, collector.getCollected().size(),
                        "Iteration " + iteration + ": Expected alert for metric=" + metric
                                + " actual=" + actualValue + " > threshold=" + thresholdValue);
                AlertRecord alert = collector.getCollected().get(0);
                assertEquals(metric, alert.getThreshold().getMetric());
                assertEquals(thresholdValue, alert.getThreshold().getConfiguredValue());
                assertEquals(actualValue, alert.getThreshold().getActualValue(), 1e-10);
            } else {
                assertTrue(collector.getCollected().isEmpty(),
                        "Iteration " + iteration + ": Expected no alert for metric=" + metric
                                + " actual=" + actualValue + " <= threshold=" + thresholdValue);
            }
        }
    }
}
