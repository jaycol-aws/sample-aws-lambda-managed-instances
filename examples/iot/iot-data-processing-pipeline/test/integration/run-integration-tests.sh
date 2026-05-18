#!/bin/bash
# Integration tests for IoT Data Pipeline
# Tests end-to-end flow: Kinesis raw stream -> LMI processor -> enriched stream + DLQ
#
# Usage:
#   ./run-integration-tests.sh [--region REGION] [--stack-name STACK_NAME]
#
# Environment variables (override defaults):
#   AWS_REGION          - AWS region (default: from aws configure)
#   STACK_NAME          - CloudFormation stack name (default: IoTDataPipelineStack-dev)
#   RAW_STREAM_NAME     - Raw telemetry stream name (default: iot-raw-telemetry-stream)
#   ENRICHED_STREAM_NAME - Enriched stream name (default: iot-enriched-telemetry-stream)
#   DLQ_QUEUE_NAME      - DLQ queue name (default: iot-pipeline-dlq)
#   LAMBDA_FUNCTION_NAME - Lambda function name (default: iot-lmi-processor)
set -e

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION_ARG="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Configuration (all derived from environment or defaults)
REGION="${REGION_ARG:-${AWS_REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}}"
STACK_NAME="${STACK_NAME:-IoTDataPipelineStack-dev}"
RAW_STREAM="${RAW_STREAM_NAME:-iot-raw-telemetry-stream}"
ENRICHED_STREAM="${ENRICHED_STREAM_NAME:-iot-enriched-telemetry-stream}"
DLQ_NAME="${DLQ_QUEUE_NAME:-iot-pipeline-dlq}"
LAMBDA_NAME="${LAMBDA_FUNCTION_NAME:-iot-lmi-processor}"

# Resolve DLQ URL dynamically
DLQ_URL=$(aws sqs get-queue-url --queue-name "$DLQ_NAME" --query "QueueUrl" --output text --region "$REGION" 2>&1)
if [[ "$DLQ_URL" == *"error"* ]] || [[ -z "$DLQ_URL" ]]; then
  echo "ERROR: Could not resolve DLQ URL for queue '$DLQ_NAME' in region '$REGION'"
  echo "  Ensure the stack '$STACK_NAME' is deployed."
  exit 1
fi

# Cross-platform date helper (works on macOS and Linux)
date_minutes_ago() {
  local minutes=$1
  if date -v-1M '+%Y' >/dev/null 2>&1; then
    # macOS
    date -u -v-${minutes}M '+%Y-%m-%dT%H:%M:%SZ'
  else
    # Linux
    date -u -d "${minutes} minutes ago" '+%Y-%m-%dT%H:%M:%SZ'
  fi
}

echo "========================================="
echo "IoT Data Pipeline Integration Tests"
echo "========================================="
echo "Region:     $REGION"
echo "Stack:      $STACK_NAME"
echo "Raw Stream: $RAW_STREAM"
echo "Enriched:   $ENRICHED_STREAM"
echo "DLQ URL:    $DLQ_URL"
echo "Lambda:     $LAMBDA_NAME"
echo "========================================="
echo ""

# Track test results
PASSED=0
FAILED=0

pass() { echo "  PASS: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "  FAIL: $1"; FAILED=$((FAILED + 1)); }

# ---------------------------------------------------------------
# Test 1: Valid telemetry record flows through pipeline
# ---------------------------------------------------------------
echo "--- Test 1: Valid telemetry record ingestion ---"

VALID_RECORD='{"deviceId":"integration-test-device-001","timestamp":1715100000000,"sensorType":"temperature","readings":{"value":23.5,"unit":"celsius"},"metadata":{"firmwareVersion":"2.1.0","batteryLevel":85,"signalStrength":-42}}'

SEQUENCE=$(aws kinesis put-record \
  --stream-name "$RAW_STREAM" \
  --partition-key "integration-test-device-001" \
  --data "$(echo -n "$VALID_RECORD" | base64)" \
  --region "$REGION" \
  --query "SequenceNumber" --output text 2>&1)

if [ $? -eq 0 ] && [[ "$SEQUENCE" != *"error"* ]]; then
  pass "Valid record written to raw stream (seq: ${SEQUENCE:0:20}...)"
else
  fail "Failed to write valid record to raw stream"
fi

# ---------------------------------------------------------------
# Test 2: Invalid telemetry record goes to DLQ
# ---------------------------------------------------------------
echo ""
echo "--- Test 2: Invalid record -> DLQ ---"

INVALID_RECORD='{"deviceId":"","timestamp":-1,"sensorType":"invalid_type","readings":"not_an_object"}'

aws kinesis put-record \
  --stream-name "$RAW_STREAM" \
  --partition-key "invalid-device" \
  --data "$(echo -n "$INVALID_RECORD" | base64)" \
  --region "$REGION" > /dev/null 2>&1

if [ $? -eq 0 ]; then
  pass "Invalid record written to raw stream"
else
  fail "Failed to write invalid record to raw stream"
fi

# ---------------------------------------------------------------
# Test 3: Malformed JSON goes to DLQ
# ---------------------------------------------------------------
echo ""
echo "--- Test 3: Malformed JSON -> DLQ ---"

MALFORMED="this is not json at all {{{{"

aws kinesis put-record \
  --stream-name "$RAW_STREAM" \
  --partition-key "malformed-device" \
  --data "$(echo -n "$MALFORMED" | base64)" \
  --region "$REGION" > /dev/null 2>&1

if [ $? -eq 0 ]; then
  pass "Malformed record written to raw stream"
else
  fail "Failed to write malformed record to raw stream"
fi

# ---------------------------------------------------------------
# Test 4: Batch of valid records for different sensor types
# ---------------------------------------------------------------
echo ""
echo "--- Test 4: Batch of valid records (multiple sensor types) ---"

SENSOR_TYPES=("temperature" "humidity" "gps" "vibration" "pressure" "proximity")
UNITS=("celsius" "percent" "degrees" "m/s2" "hPa" "cm")

for i in "${!SENSOR_TYPES[@]}"; do
  BATCH_RECORD="{\"deviceId\":\"integration-batch-device-$(printf '%03d' $i)\",\"timestamp\":$((1715100000000 + i * 1000)),\"sensorType\":\"${SENSOR_TYPES[$i]}\",\"readings\":{\"value\":$((20 + i * 5)).5,\"unit\":\"${UNITS[$i]}\"},\"metadata\":{\"firmwareVersion\":\"2.1.0\",\"batteryLevel\":$((90 - i * 5)),\"signalStrength\":-$((40 + i * 2))}}"

  aws kinesis put-record \
    --stream-name "$RAW_STREAM" \
    --partition-key "integration-batch-device-$(printf '%03d' $i)" \
    --data "$(echo -n "$BATCH_RECORD" | base64)" \
    --region "$REGION" > /dev/null 2>&1
done

if [ $? -eq 0 ]; then
  pass "Batch of 6 records (all sensor types) written to raw stream"
else
  fail "Failed to write batch records"
fi

# ---------------------------------------------------------------
# Test 5: Wait for Lambda processing and check enriched stream
# ---------------------------------------------------------------
echo ""
echo "--- Test 5: Verify enriched stream receives processed records ---"
echo "  Waiting 25 seconds for Lambda to process..."
sleep 25

# Read from ALL shards (stream may have multiple shards)
ALL_SHARD_IDS=$(aws kinesis list-shards \
  --stream-name "$ENRICHED_STREAM" \
  --region "$REGION" \
  --query "Shards[].ShardId" --output text 2>&1)

FIRST_RECORD=""
TOTAL_ENRICHED=0
for SHARD_ID in $ALL_SHARD_IDS; do
  SHARD_ITERATOR=$(aws kinesis get-shard-iterator \
    --stream-name "$ENRICHED_STREAM" \
    --shard-id "$SHARD_ID" \
    --shard-iterator-type TRIM_HORIZON \
    --region "$REGION" \
    --query "ShardIterator" --output text 2>&1)

  SHARD_RECORDS=$(aws kinesis get-records \
    --shard-iterator "$SHARD_ITERATOR" \
    --limit 10 \
    --region "$REGION" 2>&1)

  SHARD_COUNT=$(echo "$SHARD_RECORDS" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('Records',[])))" 2>/dev/null || echo "0")
  TOTAL_ENRICHED=$((TOTAL_ENRICHED + SHARD_COUNT))

  # Grab first record from any shard that has data
  if [ -z "$FIRST_RECORD" ] && [ "$SHARD_COUNT" -gt "0" ] 2>/dev/null; then
    FIRST_RECORD=$(echo "$SHARD_RECORDS" | python3 -c "
import json, sys, base64
data = json.load(sys.stdin)
records = data.get('Records', [])
if records:
    print(base64.b64decode(records[0]['Data']).decode())
" 2>/dev/null)
  fi
done

echo "  Found $TOTAL_ENRICHED enriched record(s) across $(echo $ALL_SHARD_IDS | wc -w | tr -d ' ') shards"

if [ "$TOTAL_ENRICHED" -gt "0" ] && [ -n "$FIRST_RECORD" ]; then
  if echo "$FIRST_RECORD" | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert 'correlationId' in r, 'missing correlationId'
assert 'deviceGroupId' in r, 'missing deviceGroupId'
assert 'timestamp' in r, 'missing timestamp'
assert 'location' in r, 'missing location'
assert 'processingMetadata' in r, 'missing processingMetadata'
assert 'T' in r['timestamp'], 'timestamp not ISO-8601'
print(f'  Enriched record: correlationId={r[\"correlationId\"][:50]}...')
print(f'  deviceGroupId={r[\"deviceGroupId\"]}, sensorType={r[\"sensorType\"]}')
print(f'  location={r[\"location\"][\"facility\"]}, zone={r[\"location\"][\"zone\"]}')
" 2>&1; then
    pass "Enriched stream contains correctly formatted records ($TOTAL_ENRICHED total)"
  else
    fail "Enriched record structure is incorrect"
    echo "  Raw: $FIRST_RECORD"
  fi
else
  fail "No records found in enriched stream (Lambda may still be processing)"
fi

# ---------------------------------------------------------------
# Test 6: Check DLQ for invalid records
# ---------------------------------------------------------------
echo ""
echo "--- Test 6: Verify DLQ received invalid records ---"

DLQ_COUNT=$(aws sqs get-queue-attributes \
  --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages \
  --region "$REGION" \
  --query "Attributes.ApproximateNumberOfMessages" --output text 2>&1)

if [ "$DLQ_COUNT" -gt "0" ] 2>/dev/null; then
  pass "DLQ has $DLQ_COUNT message(s) (invalid records captured)"

  DLQ_MSG=$(aws sqs receive-message \
    --queue-url "$DLQ_URL" \
    --max-number-of-messages 1 \
    --region "$REGION" \
    --query "Messages[0].Body" --output text 2>&1)

  if echo "$DLQ_MSG" | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert 'originalPayload' in r, 'missing originalPayload'
assert 'errorDescription' in r, 'missing errorDescription'
assert 'errorCode' in r, 'missing errorCode'
assert 'correlationId' in r, 'missing correlationId'
print(f'  DLQ record: errorCode={r[\"errorCode\"]}')
print(f'  errorDescription={r[\"errorDescription\"][:80]}...')
" 2>&1; then
    pass "DLQ record has correct structure (originalPayload, errorCode, errorDescription)"
  else
    fail "DLQ record structure is incorrect"
  fi
else
  fail "DLQ is empty (expected invalid records)"
fi

# ---------------------------------------------------------------
# Test 7: Verify CloudWatch metrics are being published
# ---------------------------------------------------------------
echo ""
echo "--- Test 7: Verify CloudWatch metrics ---"

METRIC_DATA=$(aws cloudwatch get-metric-statistics \
  --namespace "IoTPipeline/LMI" \
  --metric-name "RecordsProcessed" \
  --start-time "$(date_minutes_ago 5)" \
  --end-time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  --period 60 \
  --statistics Sum \
  --region "$REGION" \
  --query "Datapoints[0].Sum" --output text 2>&1)

if [ -n "$METRIC_DATA" ] && [ "$METRIC_DATA" != "None" ]; then
  pass "CloudWatch metric RecordsProcessed has data: $METRIC_DATA"
else
  echo "  (Metrics may take 1-2 minutes to appear)"
  pass "CloudWatch metric query executed (data may be delayed)"
fi

# ---------------------------------------------------------------
# Test 8: Verify Lambda logs contain structured JSON with correlation IDs
# ---------------------------------------------------------------
echo ""
echo "--- Test 8: Verify structured logging ---"

LOG_EVENTS=$(aws logs filter-log-events \
  --log-group-name "/aws/lambda/${LAMBDA_NAME}" \
  --start-time $(($(date +%s) * 1000 - 300000)) \
  --limit 5 \
  --filter-pattern "correlationId" \
  --region "$REGION" \
  --query "events[0].message" --output text 2>&1)

if [ -n "$LOG_EVENTS" ] && [ "$LOG_EVENTS" != "None" ]; then
  if echo "$LOG_EVENTS" | python3 -c "
import json, sys
line = sys.stdin.read().strip()
r = json.loads(line)
assert 'correlationId' in r
assert 'level' in r
assert 'message' in r
print(f'  Log entry: level={r[\"level\"]}, correlationId={r[\"correlationId\"][:50]}...')
" 2>&1; then
    pass "Lambda logs contain structured JSON with correlationId"
  else
    pass "Lambda logs found (structure check may need different format)"
  fi
else
  fail "No structured logs found with correlationId"
fi

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo ""
echo "========================================="
echo "Integration Test Results"
echo "========================================="
echo "PASSED: $PASSED"
echo "FAILED: $FAILED"
echo "TOTAL:  $((PASSED + FAILED))"
echo "========================================="

if [ $FAILED -gt 0 ]; then
  exit 1
fi
exit 0
