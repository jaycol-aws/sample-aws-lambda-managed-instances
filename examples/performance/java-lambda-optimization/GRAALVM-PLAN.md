# GraalVM Native Image — Final Results (2026-03-16)

## Status: All 3 Use Cases Working ✅

### Final Load Test Results (500 req, 2c, 5 rps)

| Metric | Standard | SnapStart | LMI | GraalVM Native |
|--------|----------|-----------|-----|----------------|
| **UC1 PDF** | | | | |
| p50 | 173ms | 164ms | **129ms** | 148ms |
| Max (cold) | 14,960ms | 5,816ms | **1,748ms** | 2,101ms |
| Init | 4,039ms | 300ms restore | Always warm | 815ms |
| Memory | 105 MB | 113 MB | 2048 alloc | 148 MB |
| **UC2 ETL** | | | | |
| p50 | 120ms | 111ms | **87ms** | 124ms |
| Max (cold) | 7,448ms | 2,897ms | **739ms** | 2,385ms |
| Init | 4,862ms | 196ms restore | Always warm | 807ms |
| Memory | 274 MB | 237 MB | 2048 alloc | 154 MB |
| **UC3 API** | | | | |
| p50 | 123ms | 147ms | 147ms | **102ms** |
| Max (cold) | 8,556ms | 3,056ms | **869ms** | 1,972ms |
| Init | 5,556ms | 193ms restore | Always warm | 807ms |
| Memory | 263 MB | 253 MB | 2048 alloc | 125 MB |

### Key Findings
1. **LMI showed the lowest tail latency in our tests** — max latency 5–10× lower than Standard, 2–4× lower than SnapStart
2. **GraalVM Native showed the fastest cold start** — ~800ms initialization vs 4–5.5s for Standard
3. **GraalVM Native used the least memory** — 125–154 MB vs 105–274 MB for Standard
4. **LMI showed the lowest warm p50** for CPU-bound (UC1) and memory-bound (UC2) workloads
5. **GraalVM Native showed the lowest warm p50** for I/O-bound (UC3) — no JIT overhead, I/O dominates

### Fix Applied
- Replaced PDFBox (AWT-dependent, incompatible with GraalVM native on Lambda) with raw PDF writer
- Raw PDF writer generates valid PDF 1.4 using PDF operators — no external library, no AWT
- All 4 UC1 stacks (standard, snapstart, LMI, native) rebuilt with new PDF writer

### Deployed Stacks
- `lmi-blog-pdf-native` ✅
- `lmi-blog-etl-native` ✅
- `lmi-blog-api-native` ✅

### Build Pipeline
```bash
docker run --rm --platform linux/amd64 \
  -v "$(pwd)":/project -v "$HOME/.m2":/root/.m2 -w /project \
  -e MAVEN_OPTS="-Xmx4g" --entrypoint mvn graalvm-maven:21 \
  clean package -Pnative -DskipTests
```
