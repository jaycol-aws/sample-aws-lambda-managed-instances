#!/usr/bin/env bash
# Load test the lmi-candidate-test Lambda to generate CloudWatch metrics
# that the LMI Candidate Finder will detect.
#
# Usage: ./load-test.sh [TOTAL_INVOCATIONS] [CONCURRENCY]
#   Defaults: 500 invocations, 10 concurrent

set -euo pipefail

FUNCTION_NAME="lmi-candidate-test"
TOTAL=${1:-500}
CONCURRENCY=${2:-10}
REGION=${AWS_DEFAULT_REGION:-us-east-1}
PAYLOAD='{"iterations": 50000}'

echo "🚀 Load testing $FUNCTION_NAME"
echo "   Region: $REGION | Total: $TOTAL | Concurrency: $CONCURRENCY"
echo ""

# Check function exists
if ! aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" &>/dev/null; then
    echo "❌ Function $FUNCTION_NAME not found in $REGION"
    echo "   Deploy first: cd test-function && sam build && sam deploy --guided"
    exit 1
fi

SUCCESS=0
FAIL=0
START=$(date +%s)

invoke_batch() {
    local result
    result=$(aws lambda invoke \
        --function-name "$FUNCTION_NAME" \
        --region "$REGION" \
        --payload "$PAYLOAD" \
        --cli-binary-format raw-in-base64-out \
        /dev/stdout 2>/dev/null | head -1)
    if echo "$result" | grep -q '"statusCode": 200'; then
        return 0
    else
        return 1
    fi
}

echo "Sending invocations..."
SENT=0
while [ $SENT -lt $TOTAL ]; do
    # Launch batch of concurrent invocations
    PIDS=()
    BATCH_SIZE=$((TOTAL - SENT < CONCURRENCY ? TOTAL - SENT : CONCURRENCY))
    for ((i=0; i<BATCH_SIZE; i++)); do
        invoke_batch &
        PIDS+=($!)
    done

    # Wait for batch
    for pid in "${PIDS[@]}"; do
        if wait "$pid" 2>/dev/null; then
            SUCCESS=$((SUCCESS + 1))
        else
            FAIL=$((FAIL + 1))
        fi
    done

    SENT=$((SENT + BATCH_SIZE))
    printf "\r   Progress: %d/%d (✅ %d ❌ %d)" "$SENT" "$TOTAL" "$SUCCESS" "$FAIL"
done

END=$(date +%s)
ELAPSED=$((END - START))

echo ""
echo ""
echo "✅ Load test complete"
echo "   Duration: ${ELAPSED}s | Success: $SUCCESS | Failed: $FAIL"
echo "   Effective RPS: $(echo "scale=1; $TOTAL / $ELAPSED" | bc)"
echo ""
echo "⏳ Wait 2-3 minutes for CloudWatch metrics to populate, then run:"
echo "   python lmi_candidate_finder.py --region $REGION --days 1"
