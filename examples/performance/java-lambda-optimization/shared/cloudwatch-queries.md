# Amazon CloudWatch Logs Insights Queries — Complete Reference
#
# These queries cover every metric dimension identified in the gap analysis
# against existing Lambda Java blogs (AWS Compute Blog, Kazulkin series,
# AWS GC Field Notes, AWS Priming blog).
#
# Usage: Copy any query into CloudWatch Logs Insights, select the appropriate
# log group(s), and adjust the time range to cover your load test window.

# ═══════════════════════════════════════════════════════════════════════════════
# 1. COLD START vs WARM START — Standard Lambda
# ═══════════════════════════════════════════════════════════════════════════════
# Source: aws-samples/serverless-java-frameworks-samples, Kazulkin series

## 1a. Latency percentiles split by cold/warm
```
filter @type = "REPORT"
| fields greatest(@initDuration, 0) + @duration as duration,
        ispresent(@initDuration) as coldStart
| stats count(*) as invocations,
        pct(duration, 50) as p50,
        pct(duration, 75) as p75,
        pct(duration, 90) as p90,
        pct(duration, 95) as p95,
        pct(duration, 99) as p99,
        max(duration) as max
  by coldStart
```

## 1b. Billed duration and max memory used (cost analysis)
```
filter @type = "REPORT"
| fields @billedDuration as billedMs,
        @maxMemoryUsed / 1048576 as maxMemoryMB,
        @memorySize / 1048576 as allocatedMemoryMB,
        @maxMemoryUsed / @memorySize * 100 as memoryUtilizationPct,
        ispresent(@initDuration) as coldStart
| stats count(*) as invocations,
        avg(billedMs) as avgBilledMs,
        sum(billedMs) as totalBilledMs,
        avg(maxMemoryMB) as avgMaxMemoryMB,
        max(maxMemoryMB) as peakMemoryMB,
        avg(memoryUtilizationPct) as avgMemoryUtilPct
  by coldStart
```

## 1c. Init duration breakdown (cold starts only)
```
filter @type = "REPORT"
| filter ispresent(@initDuration)
| stats count(*) as coldStarts,
        pct(@initDuration, 50) as initP50,
        pct(@initDuration, 90) as initP90,
        pct(@initDuration, 99) as initP99,
        max(@initDuration) as initMax,
        pct(@duration, 50) as handlerP50,
        pct(@duration, 90) as handlerP90
```

# ═══════════════════════════════════════════════════════════════════════════════
# 2. SNAPSTART — Restore Duration
# ═══════════════════════════════════════════════════════════════════════════════
# Source: AWS Priming blog (Apr 2025), Kazulkin series

## 2a. SnapStart cold start (restore + first invocation)
```
filter @message like "REPORT"
| filter @message not like "RESTORE_REPORT"
| filter @message like "Restore Duration"
| parse @message "Restore Duration:* ms" as restoreTime
| fields @duration + restoreTime as totalColdDuration
| stats count(*) as coldStarts,
        pct(totalColdDuration, 50) as p50,
        pct(totalColdDuration, 90) as p90,
        pct(totalColdDuration, 99) as p99,
        max(totalColdDuration) as max,
        avg(restoreTime) as avgRestoreMs
```

## 2b. SnapStart warm start
```
filter @message like "REPORT"
| filter @message not like "RESTORE_REPORT"
| filter @message not like "Restore Duration"
| fields @duration as duration
| stats count(*) as warmStarts,
        pct(duration, 50) as p50,
        pct(duration, 90) as p90,
        pct(duration, 99) as p99,
        max(duration) as max
```

# ═══════════════════════════════════════════════════════════════════════════════
# 3. EMF METRICS — JVM Performance (all modes)
# ═══════════════════════════════════════════════════════════════════════════════
# Source: Our custom EMF instrumentation

## 3a. JIT compilation progression over time (LMI key metric)
```
filter CompilationTime > 0
| stats avg(CompilationTime) as avgCompTimeMs,
        max(CompilationTime) as maxCompTimeMs,
        avg(HandlerDuration) as avgLatencyMs,
        pct(HandlerDuration, 99) as p99LatencyMs,
        avg(ActiveThreads) as avgThreads
  by bin(1m)
| sort @timestamp asc
```

## 3b. GC pressure over time
```
filter GcCount >= 0
| stats avg(GcTime) as avgGcTimeMs,
        max(GcTime) as maxGcTimeMs,
        avg(GcCount) as avgGcCount,
        avg(HeapUsed) / 1048576 as avgHeapUsedMB,
        avg(HeapCommitted) / 1048576 as avgHeapCommittedMB,
        avg(HeapMax) / 1048576 as avgHeapMaxMB
  by bin(1m)
| sort @timestamp asc
```

## 3c. Memory warmup curve (heap growth over invocation lifetime)
```
filter HandlerDuration > 0
| stats avg(HandlerDuration) as avgLatencyMs,
        pct(HandlerDuration, 99) as p99LatencyMs,
        avg(HeapUsed) / 1048576 as avgHeapMB,
        avg(MetaspaceUsed) / 1048576 as avgMetaspaceMB,
        avg(HeapCommitted) / 1048576 as avgCommittedMB
  by bin(1m)
| sort @timestamp asc
```

## 3d. Cold vs warm EMF comparison
```
filter HandlerDuration > 0
| stats count(*) as invocations,
        avg(HandlerDuration) as avgLatencyMs,
        pct(HandlerDuration, 50) as p50,
        pct(HandlerDuration, 99) as p99,
        avg(HeapUsed) / 1048576 as avgHeapMB,
        avg(GcTime) as avgGcTimeMs,
        avg(CompilationTime) as avgCompTimeMs
  by ColdStart
```

## 3e. Thread utilization (LMI multi-concurrency)
```
filter ActiveThreads > 0
| stats avg(ActiveThreads) as avgThreads,
        max(ActiveThreads) as maxThreads,
        pct(ActiveThreads, 95) as p95Threads,
        avg(HandlerDuration) as avgLatencyMs
  by bin(1m)
| sort @timestamp asc
```

# ═══════════════════════════════════════════════════════════════════════════════
# 4. GC ACTIVITY DETAIL — Matches AWS GC Field Notes blog
# ═══════════════════════════════════════════════════════════════════════════════
# Requires JAVA_TOOL_OPTIONS: -Xlog:gc:stderr:time,tags (Java 21)

## 4a. GC events from JVM logs (Java 21 unified logging)
```
filter @message like "[gc]"
| parse @message "[*][gc] GC(*) * (*) *->*(*) *ms" as ts, gcNum, gcType, gcCause, heapBefore, heapAfter, heapSize, gcDuration
| stats count(*) as gcEvents,
        avg(gcDuration) as avgGcDurationMs,
        max(gcDuration) as maxGcDurationMs,
        sum(gcDuration) as totalGcDurationMs
  by gcType
```

## 4b. Heap before/after GC over time
```
filter @message like "[gc]" and @message like "Pause"
| parse @message "[*][gc] GC(*) * (*) *M->*M(*M) *ms" as ts, gcNum, gcType, gcCause, heapBeforeMB, heapAfterMB, heapSizeMB, gcDurationMs
| stats avg(heapBeforeMB) as avgHeapBeforeMB,
        avg(heapAfterMB) as avgHeapAfterMB,
        avg(heapSizeMB) as avgHeapSizeMB,
        avg(gcDurationMs) as avgPauseMs
  by bin(5m)
| sort @timestamp asc
```

# ═══════════════════════════════════════════════════════════════════════════════
# 5. END-TO-END LATENCY (API Gateway → Lambda → Response)
# ═══════════════════════════════════════════════════════════════════════════════
# Requires API Gateway access logging enabled

## 5a. End-to-end from API Gateway logs
# (Enable access logging on API Gateway stage with format:
#  { "requestId":"$context.requestId", "ip":"$context.identity.sourceIp",
#    "requestTime":"$context.requestTime", "httpMethod":"$context.httpMethod",
#    "status":"$context.status", "responseLength":"$context.responseLength",
#    "integrationLatency":"$context.integrationLatency",
#    "responseLatency":"$context.responseLatency" })
```
filter ispresent(responseLatency)
| stats count(*) as requests,
        pct(responseLatency, 50) as e2eP50,
        pct(responseLatency, 90) as e2eP90,
        pct(responseLatency, 99) as e2eP99,
        avg(integrationLatency) as avgIntegrationMs,
        avg(responseLatency - integrationLatency) as avgApiGwOverheadMs
```

# ═══════════════════════════════════════════════════════════════════════════════
# 6. CROSS-MODE COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════
# Run against multiple log groups simultaneously

## 6a. Side-by-side cold start comparison across deployment modes
```
filter @type = "REPORT"
| parse @log /\d+:\/aws\/lambda\/(?<functionName>[^\s]+)/
| fields greatest(@initDuration, 0) + @duration as duration,
        ispresent(@initDuration) as coldStart
| stats count(*) as invocations,
        pct(duration, 50) as p50,
        pct(duration, 90) as p90,
        pct(duration, 99) as p99,
        max(duration) as max
  by functionName, coldStart
| sort by functionName, coldStart
```

## 6b. Cost efficiency comparison (billed GB-seconds)
```
filter @type = "REPORT"
| parse @log /\d+:\/aws\/lambda\/(?<functionName>[^\s]+)/
| fields @billedDuration * @memorySize / 1024 as billedGBms,
        ispresent(@initDuration) as coldStart
| stats count(*) as invocations,
        sum(billedGBms) / 1000 as totalGBseconds,
        avg(billedGBms) / 1000 as avgGBsecondsPerInvocation
  by functionName
| sort by totalGBseconds asc
```

# ═══════════════════════════════════════════════════════════════════════════════
# 7. USE-CASE SPECIFIC
# ═══════════════════════════════════════════════════════════════════════════════

## 7a. UC2 — Record count vs latency correlation (memory pressure)
```
filter RecordCount > 0
| stats avg(HandlerDuration) as avgLatencyMs,
        pct(HandlerDuration, 99) as p99LatencyMs,
        avg(HeapUsed) / 1048576 as avgHeapMB,
        avg(GcTime) as avgGcTimeMs
  by RecordCount
| sort by RecordCount asc
```
