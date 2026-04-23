# Final Benchmark Results — v6

**Config:** Standard/SnapStart/Native @ 1024 MB (1 vCPU) | LMI @ 2 GiB/vCPU, conc=5
**Method:** All via API Gateway. 3 runs × (500 warmup + 2000 @ 15c)


## UC1 PDF (CPU-bound)

| Mode | p50 | p90 | p95 | p99 | max |
|------|-----|-----|-----|-----|-----|
| standard | 166ms | 248ms | 377ms | 1185ms | 14377ms |
| snapstart | 146ms | 209ms | 248ms | 335ms | 5760ms |
| native | 125ms | 193ms | 237ms | 563ms | 1800ms |
| lmi | 112ms | 179ms | 191ms | 327ms | 657ms |

## UC2 ETL (Memory-intensive)

| Mode | p50 | p90 | p95 | p99 | max |
|------|-----|-----|-----|-----|-----|
| standard | 218ms | 585ms | 763ms | 2457ms | 9585ms |
| snapstart | 207ms | 582ms | 768ms | 2371ms | 4541ms |
| lmi | 193ms | 593ms | 703ms | 1455ms | 4076ms |

## UC3 API (I/O-bound)

| Mode | p50 | p90 | p95 | p99 | max |
|------|-----|-----|-----|-----|-----|
| standard | 109ms | 178ms | 194ms | 327ms | 9079ms |
| snapstart | 96ms | 177ms | 186ms | 432ms | 1993ms |
| native | 98ms | 177ms | 186ms | 377ms | 1138ms |
| lmi | 92ms | 177ms | 184ms | 307ms | 593ms |
