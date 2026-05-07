# Shared Metrics Utilities

Reusable EMF (Embedded Metrics Format) instrumentation for capturing JVM performance data across all use cases.

## Usage

Add as a dependency in each use case's `pom.xml` via `<module>` or copy the classes directly.

## Metrics Emitted

| Metric | Unit | Description |
|--------|------|-------------|
| `ColdStartDuration` | Milliseconds | Time from init to first response |
| `WarmLatency` | Milliseconds | Handler execution time (warm) |
| `HeapUsed` | Bytes | JVM heap used after GC |
| `HeapCommitted` | Bytes | JVM heap committed |
| `MetaspaceUsed` | Bytes | Metaspace usage |
| `GcPauseTime` | Milliseconds | Last GC pause duration |
| `GcCount` | Count | Total GC collections |
| `ActiveThreads` | Count | Current active thread count |
| `RssBytes` | Bytes | Resident set size (Linux only) |
| `CompilationTime` | Milliseconds | Total JIT compilation time |
