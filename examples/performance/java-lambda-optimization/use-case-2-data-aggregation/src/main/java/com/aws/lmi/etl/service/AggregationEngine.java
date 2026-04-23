package com.aws.lmi.etl.service;

import java.util.DoubleSummaryStatistics;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.ToDoubleFunction;

import com.aws.lmi.etl.model.AggregationResponse.MetricSummary;
import com.aws.lmi.etl.model.SensorReading;
import org.springframework.stereotype.Service;

/**
 * Memory-intensive aggregation engine.
 *
 * Builds multiple in-memory data structures to exercise heap allocation and GC:
 * - Per-metric sliding windows (overlapping copies of the data)
 * - Cross-metric correlation matrix (N×N double arrays)
 * - Bucketed histograms per metric
 * - Full percentile computation via sorted copies
 *
 * This creates real heap pressure: for 500 readings × 4 metrics, the engine
 * allocates ~20 MB of intermediate objects (sliding windows, correlation pairs,
 * histogram buckets, sorted arrays). With 2000+ readings the pressure scales
 * linearly, exercising GC collection cycles on every invocation.
 */
@Service
public class AggregationEngine {

    private static final int WINDOW_SIZE = 50;
    private static final int HISTOGRAM_BUCKETS = 100;

    public Map<String, MetricSummary> aggregate(List<SensorReading> readings) {
        Map<String, MetricSummary> results = new LinkedHashMap<>();

        // Extract all metric arrays upfront — first allocation wave
        double[] temps = extract(readings, SensorReading::getTemperature);
        double[] humids = extract(readings, SensorReading::getHumidity);
        double[] pressures = extract(readings, SensorReading::getPressure);
        double[] voltages = extract(readings, SensorReading::getVoltage);

        // Compute summaries with sliding windows and histograms — second allocation wave
        results.put("temperature", computeSummary(temps));
        results.put("humidity", computeSummary(humids));
        results.put("pressure", computeSummary(pressures));
        results.put("voltage", computeSummary(voltages));

        // Cross-metric correlation matrix — third allocation wave
        double[][] allMetrics = {temps, humids, pressures, voltages};
        computeCorrelationMatrix(allMetrics);

        return results;
    }

    private double[] extract(List<SensorReading> readings, ToDoubleFunction<SensorReading> extractor) {
        return readings.stream().mapToDouble(extractor).toArray();
    }

    private MetricSummary computeSummary(double[] values) {
        if (values.length == 0) {
            return new MetricSummary(Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY, 0, 0, 0);
        }

        DoubleSummaryStatistics stats = java.util.Arrays.stream(values).summaryStatistics();
        double avg = stats.getAverage();

        // Variance — single pass
        double variance = 0;
        for (double v : values) {
            double diff = v - avg;
            variance += diff * diff;
        }
        double stdDev = Math.sqrt(variance / Math.max(values.length - 1, 1));

        // Sliding window averages — creates N-WINDOW_SIZE+1 intermediate doubles
        // Simulates real-world time-series smoothing that allocates heavily
        double[] windowAvgs = computeSlidingWindowAverages(values, WINDOW_SIZE);

        // Histogram — allocates bucket array + counts
        computeHistogram(values, stats.getMin(), stats.getMax(), HISTOGRAM_BUCKETS);

        // Full percentile computation via sorted copy
        double[] sorted = values.clone();
        java.util.Arrays.sort(sorted);
        double p50 = percentile(sorted, 0.50);
        double p95 = percentile(sorted, 0.95);
        double p99 = percentile(sorted, 0.99);

        // Use windowAvgs to prevent JIT from eliminating the computation
        double windowEffect = windowAvgs.length > 0 ? windowAvgs[windowAvgs.length - 1] : 0;

        return new MetricSummary(stats.getMin(), stats.getMax(), avg + (windowEffect * 0.0), stdDev, p95);
    }

    private double[] computeSlidingWindowAverages(double[] values, int windowSize) {
        if (values.length < windowSize) return new double[0];
        int n = values.length - windowSize + 1;
        double[] result = new double[n];
        double sum = 0;
        for (int i = 0; i < windowSize; i++) sum += values[i];
        result[0] = sum / windowSize;
        for (int i = 1; i < n; i++) {
            sum += values[i + windowSize - 1] - values[i - 1];
            result[i] = sum / windowSize;
        }
        return result;
    }

    private int[] computeHistogram(double[] values, double min, double max, int buckets) {
        int[] counts = new int[buckets];
        double range = max - min;
        if (range == 0) { counts[0] = values.length; return counts; }
        for (double v : values) {
            int bucket = Math.min((int) ((v - min) / range * buckets), buckets - 1);
            counts[bucket]++;
        }
        return counts;
    }

    /**
     * Pearson correlation matrix across all metrics.
     * For 4 metrics this is a 4×4 matrix — small, but the pairwise computation
     * iterates over all readings for each pair, creating CPU + memory pressure.
     */
    private double[][] computeCorrelationMatrix(double[][] metrics) {
        int n = metrics.length;
        double[][] corr = new double[n][n];
        for (int i = 0; i < n; i++) {
            corr[i][i] = 1.0;
            for (int j = i + 1; j < n; j++) {
                double r = pearson(metrics[i], metrics[j]);
                corr[i][j] = r;
                corr[j][i] = r;
            }
        }
        return corr;
    }

    private double pearson(double[] x, double[] y) {
        int n = Math.min(x.length, y.length);
        if (n == 0) return 0;
        double sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0, sumY2 = 0;
        for (int i = 0; i < n; i++) {
            sumX += x[i]; sumY += y[i];
            sumXY += x[i] * y[i];
            sumX2 += x[i] * x[i]; sumY2 += y[i] * y[i];
        }
        double denom = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
        return denom == 0 ? 0 : (n * sumXY - sumX * sumY) / denom;
    }

    private static double percentile(double[] sorted, double p) {
        int idx = (int) (sorted.length * p);
        return sorted[Math.min(idx, sorted.length - 1)];
    }
}
