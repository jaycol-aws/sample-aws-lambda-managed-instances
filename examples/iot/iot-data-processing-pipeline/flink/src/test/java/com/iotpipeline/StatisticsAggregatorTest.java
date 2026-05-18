package com.iotpipeline;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import java.util.stream.Collectors;
import java.util.stream.IntStream;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Unit and property-based tests for StatisticsAggregator.computeStatistics().
 *
 * <p><b>Validates: Requirements 4.4</b></p>
 * <p>Property 5: Statistical computation correctness —
 * For any non-empty array of numeric sensor readings, the statistics computation function
 * SHALL produce an average, min, max, standard deviation, and count that match the values
 * computed by a reference implementation (using standard mathematical definitions:
 * arithmetic mean, population standard deviation).</p>
 */
class StatisticsAggregatorTest {

    private static final double TOLERANCE = 1e-10;

    // --- Known input tests ---

    @Test
    @DisplayName("Known input [1,2,3,4,5]: avg=3.0, min=1.0, max=5.0, stddev=sqrt(2.0), count=5")
    void testKnownInputOneThroughFive() {
        List<Double> values = Arrays.asList(1.0, 2.0, 3.0, 4.0, 5.0);

        AnalyticsResult.Statistics stats = StatisticsAggregator.computeStatistics(values);

        assertEquals(3.0, stats.getAverage(), TOLERANCE);
        assertEquals(1.0, stats.getMin(), TOLERANCE);
        assertEquals(5.0, stats.getMax(), TOLERANCE);
        assertEquals(Math.sqrt(2.0), stats.getStddev(), TOLERANCE);
        assertEquals(5, stats.getCount());
    }

    @Test
    @DisplayName("Single element [42.0]: avg=42.0, min=42.0, max=42.0, stddev=0.0, count=1")
    void testSingleElement() {
        List<Double> values = Collections.singletonList(42.0);

        AnalyticsResult.Statistics stats = StatisticsAggregator.computeStatistics(values);

        assertEquals(42.0, stats.getAverage(), TOLERANCE);
        assertEquals(42.0, stats.getMin(), TOLERANCE);
        assertEquals(42.0, stats.getMax(), TOLERANCE);
        assertEquals(0.0, stats.getStddev(), TOLERANCE);
        assertEquals(1, stats.getCount());
    }

    @Test
    @DisplayName("Two elements [0, 10]: avg=5.0, min=0.0, max=10.0, stddev=5.0, count=2")
    void testTwoElements() {
        List<Double> values = Arrays.asList(0.0, 10.0);

        AnalyticsResult.Statistics stats = StatisticsAggregator.computeStatistics(values);

        assertEquals(5.0, stats.getAverage(), TOLERANCE);
        assertEquals(0.0, stats.getMin(), TOLERANCE);
        assertEquals(10.0, stats.getMax(), TOLERANCE);
        assertEquals(5.0, stats.getStddev(), TOLERANCE);
        assertEquals(2, stats.getCount());
    }

    @Test
    @DisplayName("Negative values [-5, -3, -1, 1, 3, 5]: avg=0.0, min=-5.0, max=5.0")
    void testNegativeValues() {
        List<Double> values = Arrays.asList(-5.0, -3.0, -1.0, 1.0, 3.0, 5.0);

        AnalyticsResult.Statistics stats = StatisticsAggregator.computeStatistics(values);

        assertEquals(0.0, stats.getAverage(), TOLERANCE);
        assertEquals(-5.0, stats.getMin(), TOLERANCE);
        assertEquals(5.0, stats.getMax(), TOLERANCE);
        assertEquals(6, stats.getCount());

        // Reference stddev: sqrt(sum((x - 0)^2) / 6) = sqrt((25+9+1+1+9+25)/6) = sqrt(70/6)
        double expectedStddev = Math.sqrt(70.0 / 6.0);
        assertEquals(expectedStddev, stats.getStddev(), TOLERANCE);
    }

    // --- Property-based test with random arrays ---

    @Test
    @DisplayName("Property 5: Random arrays (100 iterations) match reference implementation within tolerance")
    void testRandomArraysMatchReferenceImplementation() {
        Random random = new Random(12345L); // Fixed seed for reproducibility

        for (int iteration = 0; iteration < 100; iteration++) {
            // Generate random non-empty array (1 to 100 elements)
            int size = random.nextInt(100) + 1;
            List<Double> values = IntStream.range(0, size)
                    .mapToDouble(i -> (random.nextDouble() - 0.5) * 2000.0) // Range: -1000 to 1000
                    .boxed()
                    .collect(Collectors.toList());

            AnalyticsResult.Statistics stats = StatisticsAggregator.computeStatistics(values);

            // Reference implementation
            double expectedAvg = referenceAverage(values);
            double expectedMin = referenceMin(values);
            double expectedMax = referenceMax(values);
            double expectedStddev = referenceStddev(values);
            long expectedCount = values.size();

            assertEquals(expectedCount, stats.getCount(),
                    "Count mismatch at iteration " + iteration);
            assertEquals(expectedAvg, stats.getAverage(), TOLERANCE,
                    "Average mismatch at iteration " + iteration + " for values of size " + size);
            assertEquals(expectedMin, stats.getMin(), TOLERANCE,
                    "Min mismatch at iteration " + iteration);
            assertEquals(expectedMax, stats.getMax(), TOLERANCE,
                    "Max mismatch at iteration " + iteration);
            assertEquals(expectedStddev, stats.getStddev(), TOLERANCE,
                    "Stddev mismatch at iteration " + iteration + " for values of size " + size);
        }
    }

    // --- Reference implementation ---

    private static double referenceAverage(List<Double> values) {
        double sum = 0.0;
        for (double v : values) {
            sum += v;
        }
        return sum / values.size();
    }

    private static double referenceMin(List<Double> values) {
        double min = Double.MAX_VALUE;
        for (double v : values) {
            if (v < min) {
                min = v;
            }
        }
        return min;
    }

    private static double referenceMax(List<Double> values) {
        double max = -Double.MAX_VALUE;
        for (double v : values) {
            if (v > max) {
                max = v;
            }
        }
        return max;
    }

    /**
     * Population standard deviation: sqrt(sum((x - mean)^2) / n)
     */
    private static double referenceStddev(List<Double> values) {
        double mean = referenceAverage(values);
        double varianceSum = 0.0;
        for (double v : values) {
            double diff = v - mean;
            varianceSum += diff * diff;
        }
        return Math.sqrt(varianceSum / values.size());
    }
}
