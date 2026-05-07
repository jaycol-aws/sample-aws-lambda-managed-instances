#!/bin/bash
# Final benchmark suite for LMI Java blog — v7
#
# Standardized traffic: all modes receive identical load profile.
# Deterministic payloads for run-to-run consistency.
#
# Usage: ./run-final-benchmark.sh <use-case> <mode> [--runs N]
# Example: ./run-final-benchmark.sh 1 standard --runs 10
#          ./run-final-benchmark.sh all all --runs 10

set -euo pipefail

UC_ARG="${1:?Usage: $0 <1|2|3|all> <standard|snapstart|native|lmi|all> [--runs N]}"
MODE_ARG="${2:?Usage: $0 <1|2|3|all> <standard|snapstart|native|lmi|all> [--runs N]}"
NUM_RUNS="${4:-10}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../measurements/final-v7"
mkdir -p "$OUT_DIR"

# Standardized traffic — identical for ALL modes
WARMUP_N=500
WARMUP_C=5
MEASURE_N=2000
MEASURE_C=15
MEASURE_QPS=30

# Deterministic payloads
PAYLOAD_UC1='{"accountId":"ACCT-1","startDate":"2026-01-01","endDate":"2026-01-31"}'
PAYLOAD_UC2='{"deviceId":"DEVICE-1","startTime":"2026-01-01T00:00:00Z","endTime":"2026-01-02T00:00:00Z"}'
PAYLOAD_UC3='{"customerId":"CUST-1","productId":"PROD-1","quantity":2}'

get_stack_name() {
  local UC="$1" MODE="$2"
  case "${UC}-${MODE}" in
    1-standard)  echo "lmi-blog-pdf-standard" ;;
    1-snapstart) echo "lmi-blog-pdf-snapstart" ;;
    1-native)    echo "lmi-blog-pdf-native" ;;
    1-lmi)       echo "lmi-blog-pdf-lmi-v2" ;;
    2-standard)  echo "lmi-blog-etl-standard" ;;
    2-snapstart) echo "lmi-blog-etl-snapstart" ;;
    2-native)    echo "lmi-blog-etl-native" ;;
    2-lmi)       echo "lmi-blog-etl-lmi" ;;
    3-standard)  echo "lmi-blog-api-standard" ;;
    3-snapstart) echo "lmi-blog-api-snapstart" ;;
    3-native)    echo "lmi-blog-api-native" ;;
    3-lmi)       echo "lmi-blog-api-lmi" ;;
    *) echo "" ;;
  esac
}

get_payload() {
  case "$1" in
    1) echo "$PAYLOAD_UC1" ;;
    2) echo "$PAYLOAD_UC2" ;;
    3) echo "$PAYLOAD_UC3" ;;
  esac
}

run_single() {
  local UC="$1" MODE="$2" RUN="$3"
  local STACK
  STACK=$(get_stack_name "$UC" "$MODE")
  if [ -z "$STACK" ]; then
    echo "  ⚠ No stack for UC${UC} ${MODE} — skipping"
    return
  fi

  local API
  API=$(aws cloudformation describe-stacks --stack-name "$STACK" \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text 2>/dev/null)
  if [ -z "$API" ] || [ "$API" = "None" ]; then
    echo "  ⚠ No endpoint for $STACK — skipping"
    return
  fi

  local PAYLOAD
  PAYLOAD=$(get_payload "$UC")
  local OUTFILE="$OUT_DIR/uc${UC}-${MODE}-run${RUN}-$(date +%Y%m%d-%H%M%S).txt"

  echo "  ▶ UC${UC} ${MODE} run ${RUN}/${NUM_RUNS} → $STACK"

  # Phase 1: Warmup (discarded)
  hey -n "$WARMUP_N" -c "$WARMUP_C" -q 20 \
    -m POST -H "Content-Type: application/json" \
    -d "$PAYLOAD" "$API" > /dev/null 2>&1
  sleep 2

  # Phase 2: Measurement
  hey -n "$MEASURE_N" -c "$MEASURE_C" -q "$MEASURE_QPS" \
    -m POST -H "Content-Type: application/json" \
    -d "$PAYLOAD" "$API" > "$OUTFILE" 2>&1

  # Extract key metrics
  local P50 P99 MAX STATUS_200
  P50=$(grep "50%" "$OUTFILE" | awk '{print $3}' | head -1)
  P99=$(grep "99%" "$OUTFILE" | awk '{print $3}' | head -1)
  MAX=$(grep "Slowest:" "$OUTFILE" | awk '{print $2}')
  STATUS_200=$(grep "\[200\]" "$OUTFILE" | awk '{print $2}')
  echo "    p50=${P50}s p99=${P99}s max=${MAX}s 200s=${STATUS_200}"
}

# Build UC and mode lists
if [ "$UC_ARG" = "all" ]; then UCS="1 2 3"; else UCS="$UC_ARG"; fi
if [ "$MODE_ARG" = "all" ]; then MODES="standard snapstart native lmi"; else MODES="$MODE_ARG"; fi

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║  LMI Blog Final Benchmark — v7                              ║"
echo "║  UCs: ${UCS}  Modes: ${MODES}"
echo "║  Runs: ${NUM_RUNS}                                                   ║"
echo "║  Traffic: ${WARMUP_N} warmup + ${MEASURE_N} measure @ ${MEASURE_C}c ${MEASURE_QPS}qps  ║"
echo "║  Payloads: deterministic (ACCT-1, DEVICE-1, CUST-1)         ║"
echo "║  Output: measurements/final-v7/                             ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

for RUN in $(seq 1 "$NUM_RUNS"); do
  echo "━━━ Run $RUN of $NUM_RUNS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  for UC in $UCS; do
    for MODE in $MODES; do
      run_single "$UC" "$MODE" "$RUN"
    done
  done
  if [ "$RUN" -lt "$NUM_RUNS" ]; then
    echo "  ⏳ 10s cooldown between runs..."
    sleep 10
  fi
done

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Complete! Results in: $OUT_DIR/"
echo "  Files: $(ls "$OUT_DIR"/uc*.txt 2>/dev/null | wc -l | tr -d ' ') measurement files"
echo "═══════════════════════════════════════════════════════════════"
