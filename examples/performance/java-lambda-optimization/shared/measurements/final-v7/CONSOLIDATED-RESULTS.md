# Final Benchmark Results — v7

**Date:** 2026-03-26
**Config:** Standard/SnapStart/Native @ 1024 MB (1 vCPU) | LMI @ 2 GiB/vCPU
**LMI Concurrency:** UC1=3, UC2=5, UC3=10 (production-appropriate per workload)
**LMI JVM:** `-XX:+TieredCompilation -XX:+UseG1GC -Xms512m -Xmx1408m`
**Method:** All via API Gateway. 10 runs × (500 warmup + 2000 measurement @ 15c 30qps)
**Payloads:** Deterministic (ACCT-1, DEVICE-1, CUST-1)
**Total requests:** 240,000 measurement requests (20,000 per mode per UC)


## UC1 PDF (CPU-bound)

| Mode | p50 | p90 | p95 | p99 | max | σ(p50) | runs |
|------|-----|-----|-----|-----|-----|--------|------|
| standard | 229ms | 334ms | 364ms | 471ms | 13920ms | ±18ms | 10x |
| snapstart | 189ms | 323ms | 347ms | 430ms | 2858ms | ±10ms | 10x |
| native | 154ms | 301ms | 323ms | 375ms | 1823ms | ±8ms | 10x |
| lmi | 136ms | 283ms | 309ms | 345ms | 487ms | ±5ms | 10x |


## UC2 ETL (Memory-intensive)

| Mode | p50 | p90 | p95 | p99 | max | σ(p50) | runs |
|------|-----|-----|-----|-----|-----|--------|------|
| standard | 318ms | 673ms | 858ms | 2980ms | 9919ms | ±3ms | 10x |
| snapstart | 305ms | 658ms | 841ms | 2466ms | 4447ms | ±3ms | 10x |
| native | 275ms | 583ms | 795ms | 2138ms | 4328ms | ±30ms | 10x |
| lmi | 260ms | 633ms | 748ms | 1581ms | 3537ms | ±21ms | 10x |


## UC3 API (I/O-bound)

| Mode | p50 | p90 | p95 | p99 | max | σ(p50) | runs |
|------|-----|-----|-----|-----|-----|--------|------|
| standard | 133ms | 283ms | 302ms | 369ms | 7978ms | ±6ms | 10x |
| snapstart | 122ms | 268ms | 294ms | 344ms | 1338ms | ±2ms | 10x |
| native | 118ms | 270ms | 294ms | 337ms | 1332ms | ±2ms | 10x |
| lmi | 111ms | 254ms | 283ms | 307ms | 386ms | ±3ms | 10x |


## LMI Improvement vs Standard

| UC | p50 | p90 | p95 | p99 | max (tail) |
|----|-----|-----|-----|-----|------------|
| UC1 (CPU-bound) | 40% faster | 15% faster | 15% faster | 27% faster | 29× better |
| UC2 (Memory) | 18% faster | 6% faster | 13% faster | 47% faster | 3× better |
| UC3 (I/O) | 16% faster | 10% faster | 6% faster | 17% faster | 21× better |


## Error Rates

| Mode | UC1 | UC2 | UC3 |
|------|-----|-----|-----|
| standard | 0.2% | 0.5% | 0.2% |
| snapstart | 0.2% | 0.4% | 0.2% |
| native | 0.8% | 5.3% | 3.3% |
| lmi | 0.7% | 0.3% | 0.2% |


## Methodology Notes

- All modes receive identical traffic: 500 warmup (discarded) + 2000 measurement @ 15 concurrency
- LMI fleet pre-warmed with 200 requests before benchmark start for JIT C2 compilation
- LMI concurrency varies by use case (3/5/10) — tuned for production-appropriate handling
- Standard/SnapStart/Native all at 1024 MB; LMI at 2 GiB/vCPU (minimum `ExecutionEnvironmentMemoryGiBPerVCpu` setting)
- GraalVM Native has higher error rates on UC2/UC3 due to reflection/serialization edge cases
- σ(p50) = standard deviation of p50 across 10 runs — measures consistency