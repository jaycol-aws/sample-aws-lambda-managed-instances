#!/bin/bash
# run-benchmark.sh — Full benchmark cycle for one use case across all 4 modes
#
# Usage: ./run-benchmark.sh <use-case-num> [--memory 1024]
# Example: ./run-benchmark.sh 1 --memory 1024
#
# Runs: build → deploy → seed data → load test → collect metrics
# for standard, snapstart, native, and LMI modes.

set -euo pipefail

UC_NUM="${1:?Usage: ./run-benchmark.sh <1|2|3> [--memory N]}"
MEMORY="${3:-1024}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BLOG_DIR="$(dirname "$SCRIPT_DIR")"

case "$UC_NUM" in
  1) UC_DIR="use-case-1-pdf-generation"; PREFIX="lmi-blog-pdf" ;;
  2) UC_DIR="use-case-2-data-aggregation"; PREFIX="lmi-blog-etl" ;;
  3) UC_DIR="use-case-3-api-orchestration"; PREFIX="lmi-blog-api" ;;
  *) echo "Invalid use case: $UC_NUM"; exit 1 ;;
esac

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  LMI Blog Benchmark — Use Case $UC_NUM                       ║"
echo "║  Directory: $UC_DIR"
echo "║  Memory: ${MEMORY}MB                                         ║"
echo "╚═══════════════════════════════════════════════════════════╝"

# ── Phase 1: Deploy Standard Lambda ──────────────────────────────────────────
echo ""
echo "━━━ Phase 1/5: Standard Lambda ━━━"
"$SCRIPT_DIR/deploy-and-measure.sh" "$UC_DIR" template.yaml "${PREFIX}-standard" --memory "$MEMORY"

# ── Phase 2: Seed test data (only needed once — tables shared across modes) ─
echo ""
echo "━━━ Phase 2/5: Seed test data ━━━"
python3 "$SCRIPT_DIR/seed-data.py" --use-case "$UC_NUM" --stack-name "${PREFIX}-standard"

# ── Phase 3: Deploy SnapStart ────────────────────────────────────────────────
echo ""
echo "━━━ Phase 3/5: SnapStart ━━━"
"$SCRIPT_DIR/deploy-and-measure.sh" "$UC_DIR" template.snapstart.yaml "${PREFIX}-snapstart" --memory "$MEMORY"

# ── Phase 4: Deploy GraalVM Native ───────────────────────────────────────────
echo ""
echo "━━━ Phase 4/5: GraalVM Native ━━━"
"$SCRIPT_DIR/deploy-and-measure.sh" "$UC_DIR" template.native.yaml "${PREFIX}-native"

# ── Phase 5: Run load tests ─────────────────────────────────────────────────
echo ""
echo "━━━ Phase 5/5: Load Tests ━━━"
echo "(LMI requires VPC params — deploy separately with template.lmi.yaml)"
echo ""

RESULTS_DIR="$BLOG_DIR/measurements"
mkdir -p "$RESULTS_DIR"

for MODE in standard snapstart native; do
  STACK="${PREFIX}-${MODE}"
  API=$(aws cloudformation describe-stacks --stack-name "$STACK" \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text 2>/dev/null || echo "")

  if [[ -z "$API" || "$API" == "None" ]]; then
    echo "  ⚠ Skipping $MODE — stack not found or no endpoint"
    continue
  fi

  echo "  ▶ Load testing $MODE: $API"
  cd "$BLOG_DIR/$UC_DIR/load-test"
  API_ENDPOINT="$API" artillery run load-test.yml \
    --output "$RESULTS_DIR/${STACK}-artillery.json" 2>&1 | tail -3
  artillery report "$RESULTS_DIR/${STACK}-artillery.json" \
    --output "$RESULTS_DIR/${STACK}-report.html" 2>/dev/null || true
  echo "  ✓ $MODE complete"
done

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  Benchmark complete!                                     ║"
echo "║  Results: $RESULTS_DIR/                                  ║"
echo "║                                                          ║"
echo "║  Next steps:                                             ║"
echo "║  1. Deploy LMI mode with VPC params                     ║"
echo "║  2. Run CloudWatch Logs Insights queries from            ║"
echo "║     shared/cloudwatch-queries.md                         ║"
echo "║  3. Compare X-Ray traces across modes                   ║"
echo "╚═══════════════════════════════════════════════════════════╝"
