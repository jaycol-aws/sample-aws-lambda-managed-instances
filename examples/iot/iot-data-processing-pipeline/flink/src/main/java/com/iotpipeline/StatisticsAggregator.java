package com.iotpipeline;

import org.apache.flink.streaming.api.functions.windowing.ProcessWindowFunction;
import org.apache.flink.streaming.api.windowing.windows.TimeWindow;
import org.apache.flink.util.Collector;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Flink ProcessWindowFunction that aggregates enriched telemetry records
 * within a tumbling window, grouped by device group ID.
 *
 * For each sensor type present in the window, computes:
 * - Average (arithmetic mean)
 * - Min
 * - Max
 * - Standard deviation (population)
 * - Count
 *
 * Also tracks unique device IDs contributing to the window (deviceCount).
 * alertsEmitted is set to 0 here; threshold detection happens separately.
 *
 * Validates: Requirements 4.2, 4.4
 */
public class StatisticsAggregator
        extends ProcessWindowFunction<EnrichedRecord, AnalyticsResult, String, TimeWindow> {

    private static final long serialVersionUID = 1L;

    /** Default window duration in seconds. */
    private static final long DEFAULT_WINDOW_DURATION_SECONDS = 60;

    private final long windowDurationSeconds;

    /**
     * Creates a StatisticsAggregator with the default window duration (60 seconds).
     */
    public StatisticsAggregator() {
        this(DEFAULT_WINDOW_DURATION_SECONDS);
    }

    /**
     * Creates a StatisticsAggregator with a configurable window duration.
     *
     * @param windowDurationSeconds the window duration in seconds
     */
    public StatisticsAggregator(long windowDurationSeconds) {
        this.windowDurationSeconds = windowDurationSeconds;
    }

    @Override
    public void process(String deviceGroupId,
                        ProcessWindowFunction<EnrichedRecord, AnalyticsResult, String, TimeWindow>.Context context,
                        Iterable<EnrichedRecord> elements,
                        Collector<AnalyticsResult> out) {

        TimeWindow window = context.window();

        // Group readings by sensor type and track unique devices
        Map<String, List<Double>> readingsBySensorType = new HashMap<>();
        Map<String, Set<String>> devicesBySensorType = new HashMap<>();

        for (EnrichedRecord record : elements) {
            String sensorType = record.getSensorType();
            if (sensorType == null || record.getReadings() == null) {
                continue;
            }

            double value = record.getReadings().getValue();

            readingsBySensorType
                    .computeIfAbsent(sensorType, k -> new ArrayList<>())
                    .add(value);

            devicesBySensorType
                    .computeIfAbsent(sensorType, k -> new HashSet<>())
                    .add(record.getDeviceId());
        }

        // Compute statistics for each sensor type and emit an AnalyticsResult
        for (Map.Entry<String, List<Double>> entry : readingsBySensorType.entrySet()) {
            String sensorType = entry.getKey();
            List<Double> values = entry.getValue();

            if (values.isEmpty()) {
                continue;
            }

            AnalyticsResult.Statistics stats = computeStatistics(values);

            Set<String> devices = devicesBySensorType.getOrDefault(sensorType, new HashSet<>());

            AnalyticsResult result = new AnalyticsResult();
            result.setDeviceGroupId(deviceGroupId);
            result.setWindowEndTimestamp(window.getEnd());
            result.setWindowDurationSeconds(windowDurationSeconds);
            result.setSensorType(sensorType);
            result.setStatistics(stats);
            result.setDeviceCount(devices.size());
            result.setAlertsEmitted(0);

            out.collect(result);
        }
    }

    /**
     * Computes statistics (average, min, max, population stddev, count) for a list of values.
     *
     * @param values non-empty list of numeric readings
     * @return computed statistics
     */
    static AnalyticsResult.Statistics computeStatistics(List<Double> values) {
        long count = values.size();
        double sum = 0.0;
        double min = Double.MAX_VALUE;
        double max = -Double.MAX_VALUE;

        for (double v : values) {
            sum += v;
            if (v < min) {
                min = v;
            }
            if (v > max) {
                max = v;
            }
        }

        double average = sum / count;

        // Population standard deviation: sqrt(sum((x - mean)^2) / n)
        double varianceSum = 0.0;
        for (double v : values) {
            double diff = v - average;
            varianceSum += diff * diff;
        }
        double stddev = Math.sqrt(varianceSum / count);

        AnalyticsResult.Statistics stats = new AnalyticsResult.Statistics();
        stats.setAverage(average);
        stats.setMin(min);
        stats.setMax(max);
        stats.setStddev(stddev);
        stats.setCount(count);

        return stats;
    }
}
