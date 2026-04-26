#!/bin/bash
# Phased load test for LMI Java blog benchmarks
# Phase 1: Warmup (discarded) — 500 requests to warm JIT, connection pools, caches
# Phase 2: Measurement (recorded) — 2000 requests at 10 concurrency for final data
#
# Usage: ./run-phased-benchmark.sh <stack-name> <use-case-number>
# Example: ./run-phased-benchmark.sh lmi-blog-pdf-standard 1

set -euo pipefail

STACK_NAME="${1:?Usage: $0 <stack-name> <use-case-number>}"
UC="${2:?Usage: $0 <stack-name> <use-case-number>}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUT_DIR="$(dirname "$0")/../measurements/final-v2"
mkdir -p "$OUT_DIR"

# Get API endpoint
API_ENDPOINT=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text)

if [ -z "$API_ENDPOINT" ]; then
  echo "ERROR: Could not find ApiEndpoint for stack $STACK_NAME"
  exit 1
fi

echo "═══════════════════════════════════════════════════════════"
echo "Phased Benchmark: $STACK_NAME"
echo "Endpoint: $API_ENDPOINT"
echo "═══════════════════════════════════════════════════════════"

# Build payload based on use case
case "$UC" in
  1) PAYLOAD='{"accountId":"ACCT-'"$((RANDOM % 50 + 1))"'","startDate":"2026-01-01","endDate":"2026-01-31"}' ;;
  2) PAYLOAD='{"deviceId":"DEVICE-'"$((RANDOM % 20 + 1))"'","startTime":"2026-01-01T00:00:00Z","endTime":"2026-01-02T00:00:00Z"}' ;;
  3) PAYLOAD='{"customerId":"CUST-'"$((RANDOM % 100 + 1))"'","productId":"PROD-'"$((RANDOM % 100 + 1))"'","quantity":'"$((RANDOM % 5 + 1))"'}' ;;
  *) echo "ERROR: use-case must be 1, 2, or 3"; exit 1 ;;
esac

# ── Phase 1: Warmup (discarded) ──────────────────────────────────────────
echo ""
echo "Phase 1: Warmup — 500 requests @ 5 concurrency (results discarded)"
hey -n 500 -c 5 -q 20 \
  -m POST \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "$API_ENDPOINT" > /dev/null 2>&1
echo "  ✓ Warmup complete — JIT, connection pools, and caches are hot"

# Brief pause to let GC settle
sleep 3

# ── Phase 2: Measurement ─────────────────────────────────────────────────
OUTFILE="$OUT_DIR/uc${UC}-${STACK_NAME##*-}-${TIMESTAMP}.txt"
echo ""
echo "Phase 2: Measurement — 2000 requests @ 10 concurrency"
hey -n 2000 -c 10 -q 25 \
  -m POST \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "$API_ENDPOINT" > "$OUTFILE" 2>&1

echo "  ✓ Results saved to: $OUTFILE"
echo ""
echo "── Summary ──"
head -20 "$OUTFILE"
echo ""
echo "── Latency Distribution ──"
grep -A8 "Latency distribution:" "$OUTFILE"
