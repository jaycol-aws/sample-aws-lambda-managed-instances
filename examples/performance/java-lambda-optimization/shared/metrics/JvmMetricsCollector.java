package com.aws.lmi.shared;

import java.io.IOException;
import java.lang.management.CompilationMXBean;
import java.lang.management.GarbageCollectorMXBean;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.lang.management.MemoryUsage;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Captures JVM metrics and emits them in CloudWatch Embedded Metrics Format (EMF).
 * Thread-safe for LMI multi-concurrency environments.
 */
public class JvmMetricsCollector {

    private static final Logger log = LoggerFactory.getLogger(JvmMetricsCollector.class);
    private static final MemoryMXBean MEMORY_MX = ManagementFactory.getMemoryMXBean();
    private static final CompilationMXBean COMPILATION_MX = ManagementFactory.getCompilationMXBean();

    private static final AtomicBoolean COLD_START = new AtomicBoolean(true);
    private static final AtomicLong INIT_TIMESTAMP = new AtomicLong(System.currentTimeMillis());
    private static final Map<String, Long> PREV_GC_COUNTS = new ConcurrentHashMap<>();

    private final String namespace;
    private final String functionName;

    public JvmMetricsCollector(String namespace, String functionName) {
        this.namespace = namespace;
        this.functionName = functionName;
    }

    /**
     * Emit a complete set of JVM metrics as an EMF structured log line.
     * Call this at the end of each handler invocation.
     */
    public void emitMetrics(long handlerDurationMs) {
        boolean isColdStart = COLD_START.compareAndSet(true, false);
        long coldStartDuration = isColdStart ? System.currentTimeMillis() - INIT_TIMESTAMP.get() : 0;

        MemoryUsage heap = MEMORY_MX.getHeapMemoryUsage();
        MemoryUsage nonHeap = MEMORY_MX.getNonHeapMemoryUsage();

        long totalGcCount = 0;
        long totalGcTime = 0;
        for (GarbageCollectorMXBean gc : ManagementFactory.getGarbageCollectorMXBeans()) {
            totalGcCount += gc.getCollectionCount();
            totalGcTime += gc.getCollectionTime();
        }

        long compilationTime = COMPILATION_MX != null ? COMPILATION_MX.getTotalCompilationTime() : -1;
        int activeThreads = Thread.activeCount();
        long rssBytes = readRssBytes();

        // EMF structured log — CloudWatch parses this automatically
        String emf = String.format(
            "{\"_aws\":{\"Timestamp\":%d,\"CloudWatchMetrics\":[{\"Namespace\":\"%s\","
            + "\"Dimensions\":[[\"FunctionName\",\"ColdStart\"]],"
            + "\"Metrics\":["
            + "{\"Name\":\"HandlerDuration\",\"Unit\":\"Milliseconds\"},"
            + "{\"Name\":\"ColdStartDuration\",\"Unit\":\"Milliseconds\"},"
            + "{\"Name\":\"HeapUsed\",\"Unit\":\"Bytes\"},"
            + "{\"Name\":\"HeapCommitted\",\"Unit\":\"Bytes\"},"
            + "{\"Name\":\"MetaspaceUsed\",\"Unit\":\"Bytes\"},"
            + "{\"Name\":\"GcCount\",\"Unit\":\"Count\"},"
            + "{\"Name\":\"GcTime\",\"Unit\":\"Milliseconds\"},"
            + "{\"Name\":\"ActiveThreads\",\"Unit\":\"Count\"},"
            + "{\"Name\":\"RssBytes\",\"Unit\":\"Bytes\"},"
            + "{\"Name\":\"CompilationTime\",\"Unit\":\"Milliseconds\"}"
            + "]}]},"
            + "\"FunctionName\":\"%s\",\"ColdStart\":\"%s\","
            + "\"HandlerDuration\":%d,\"ColdStartDuration\":%d,"
            + "\"HeapUsed\":%d,\"HeapCommitted\":%d,\"MetaspaceUsed\":%d,"
            + "\"GcCount\":%d,\"GcTime\":%d,"
            + "\"ActiveThreads\":%d,\"RssBytes\":%d,\"CompilationTime\":%d}",
            System.currentTimeMillis(), namespace,
            functionName, String.valueOf(isColdStart),
            handlerDurationMs, coldStartDuration,
            heap.getUsed(), heap.getCommitted(), nonHeap.getUsed(),
            totalGcCount, totalGcTime,
            activeThreads, rssBytes, compilationTime
        );

        // Print to stdout — CloudWatch Logs picks up EMF automatically
        System.out.println(emf);
    }

    /**
     * Read RSS from /proc/self/status (Linux only — returns -1 on macOS/Windows).
     */
    private static long readRssBytes() {
        try {
            Path statusPath = Path.of("/proc/self/status");
            if (!Files.exists(statusPath)) return -1;
            return Files.readAllLines(statusPath).stream()
                .filter(line -> line.startsWith("VmRSS:"))
                .map(line -> line.replaceAll("[^0-9]", ""))
                .mapToLong(Long::parseLong)
                .map(kb -> kb * 1024)
                .findFirst()
                .orElse(-1);
        } catch (IOException e) {
            return -1;
        }
    }
}
