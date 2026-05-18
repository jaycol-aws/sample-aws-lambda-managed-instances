#!/bin/bash
# Stress Test for IoT Data Pipeline
# Sends configurable number of records via PutRecords, waits, then verifies
# enriched stream output and DLQ capture.
#
# Usage:
#   ./stress-test-v2.sh [--region REGION] [--records N] [--batch-size N]
#
# Environment variables (override defaults):
#   AWS_REGION           - AWS region (default: from aws configure)
#   RAW_STREAM_NAME      - Raw telemetry stream (default: iot-raw-telemetry-stream)
#   ENRICHED_STREAM_NAME - Enriched stream (default: iot-enriched-telemetry-stream)
#   DLQ_QUEUE_NAME       - DLQ queue name (default: iot-pipeline-dlq)
#   LAMBDA_FUNCTION_NAME - Lambda function name (default: iot-lmi-processor)
#   TOTAL_RECORDS        - Number of records to send (default: 1000)
#   BATCH_SIZE           - Records per PutRecords call (default: 100)
#   NUM_DEVICES          - Simulated device count (default: 200)
#   INVALID_PERCENT      - Percentage of invalid records (default: 10)
#   PROCESSING_WAIT      - Seconds to wait for Lambda processing (default: 30)
set -e

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION_ARG="$2"; shift 2 ;;
    --records) TOTAL_ARG="$2"; shift 2 ;;
    --batch-size) BATCH_ARG="$2"; shift 2 ;;
    --devices) DEVICES_ARG="$2"; shift 2 ;;
    --invalid-percent) INVALID_ARG="$2"; shift 2 ;;
    --wait) WAIT_ARG="$2"; shift 2 ;;
    --help) echo "Usage: $0 [--region R] [--records N] [--batch-size N] [--devices N] [--invalid-percent N] [--wait N]"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Configuration
REGION="${REGION_ARG:-${AWS_REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}}"
RAW_STREAM="${RAW_STREAM_NAME:-iot-raw-telemetry-stream}"
ENRICHED_STREAM="${ENRICHED_STREAM_NAME:-iot-enriched-telemetry-stream}"
DLQ_NAME="${DLQ_QUEUE_NAME:-iot-pipeline-dlq}"
LAMBDA_NAME="${LAMBDA_FUNCTION_NAME:-iot-lmi-processor}"
TOTAL="${TOTAL_ARG:-${TOTAL_RECORDS:-1000}}"
BATCH="${BATCH_ARG:-${BATCH_SIZE:-100}}"
DEVICES="${DEVICES_ARG:-${NUM_DEVICES:-200}}"
INVALID_PCT="${INVALID_ARG:-${INVALID_PERCENT:-10}}"
WAIT_SECS="${WAIT_ARG:-${PROCESSING_WAIT:-30}}"

SENSOR_TYPES=("temperature" "humidity" "gps" "vibration" "pressure" "proximity")

# Resolve DLQ URL dynamically
DLQ_URL=$(aws sqs get-queue-url --queue-name "$DLQ_NAME" --query "QueueUrl" --output text --region "$REGION" 2>&1)
if [[ "$DLQ_URL" == *"error"* ]] || [[ -z "$DLQ_URL" ]]; then
  echo "ERROR: Could not resolve DLQ URL for queue '$DLQ_NAME' in region '$REGION'"
  exit 1
fi

# Cross-platform date helper
date_minutes_ago() {
  local minutes=$1
  if date -v-1M '+%Y' >/dev/null 2>&1; then
    date -u -v-${minutes}M '+%Y-%m-%dT%H:%M:%SZ'
  else
    date -u -d "${minutes} minutes ago" '+%Y-%m-%dT%H:%M:%SZ'
  fi
}

echo "=============================================="
echo "IoT Data Pipeline Stress Test"
echo "=============================================="
echo "Region:     $REGION"
echo "Records:    $TOTAL | Batch: $BATCH | ~${INVALID_PCT}% invalid"
echo "Devices:    $DEVICES"
echo "Raw Stream: $RAW_STREAM"
echo "Enriched:   $ENRICHED_STREAM"
echo "DLQ:        $DLQ_NAME"
echo "=============================================="

START=$(date +%s)
VALID_COUNT=0
INVALID_COUNT=0

# Send records
for ((batch_start=0; batch_start<TOTAL; batch_start+=BATCH)); do
  RECORDS="["
  FIRST=true

  for ((i=0; i<BATCH && (batch_start+i)<TOTAL; i++)); do
    DEVICE="device-$(printf '%04d' $(( (batch_start+i) % DEVICES )))"
    SENSOR="${SENSOR_TYPES[$(( (batch_start+i) % 6 ))]}"
    TS=$(( $(date +%s) * 1000 + batch_start + i ))
    VAL=$(echo "scale=1; $(( RANDOM % 50 + 10 )).$(( RANDOM % 9 ))" | bc)

    if [ $(( (batch_start+i) % (100 / INVALID_PCT) )) -eq 0 ] && [ $INVALID_PCT -gt 0 ]; then
      # Invalid record: bad sensorType
      DATA="{\"deviceId\":\"$DEVICE\",\"timestamp\":$TS,\"sensorType\":\"bad_type\",\"readings\":{\"value\":$VAL,\"unit\":\"c\"}}"
      INVALID_COUNT=$((INVALID_COUNT + 1))
    else
      DATA="{\"deviceId\":\"$DEVICE\",\"timestamp\":$TS,\"sensorType\":\"$SENSOR\",\"readings\":{\"value\":$VAL,\"unit\":\"celsius\"},\"metadata\":{\"batteryLevel\":85,\"signalStrength\":-45}}"
      VALID_COUNT=$((VALID_COUNT + 1))
    fi

    ENCODED=$(echo -n "$DATA" | base64)

    if [ "$FIRST" = true ]; then FIRST=false; else RECORDS+=","; fi
    RECORDS+="{\"Data\":\"$ENCODED\",\"PartitionKey\":\"$DEVICE\"}"
  done

  RECORDS+="]"

  FAILED=$(aws kinesis put-records --stream-name "$RAW_STREAM" --records "$RECORDS" --region "$REGION" --query "FailedRecordCount" --output text 2>&1)

  if [ "$FAILED" != "0" ]; then
    echo "  WARNING: $FAILED records failed in batch at offset $batch_start"
  fi

  echo -ne "  Sent $((batch_start + BATCH)) / $TOTAL\r"
done

SEND_END=$(date +%s)
echo ""
echo ""
echo "Send complete in $((SEND_END - START))s"
echo "  Valid: $VALID_COUNT | Invalid: $INVALID_COUNT"
echo ""

# Wait for processing
echo "Waiting ${WAIT_SECS}s for Lambda to process all records..."
sleep "$WAIT_SECS"

# Count enriched records
echo ""
echo "Counting enriched stream records..."
ENRICHED_TOTAL=0
SHARDS=$(aws kinesis list-shards --stream-name "$ENRICHED_STREAM" --region "$REGION" --query "Shards[].ShardId" --output text)

for SHARD in $SHARDS; do
  ITER=$(aws kinesis get-shard-iterator --stream-name "$ENRICHED_STREAM" --shard-id "$SHARD" --shard-iterator-type AT_TIMESTAMP --timestamp "$(date_minutes_ago 2)" --region "$REGION" --query "ShardIterator" --output text)

  for ((page=0; page<20; page++)); do
    RESULT=$(aws kinesis get-records --shard-iterator "$ITER" --limit 10000 --region "$REGION" 2>&1)
    COUNT=$(echo "$RESULT" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('Records',[])))")
    ENRICHED_TOTAL=$((ENRICHED_TOTAL + COUNT))
    ITER=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('NextShardIterator',''))")
    if [ "$COUNT" -eq 0 ] || [ -z "$ITER" ]; then break; fi
  done
done

# Count DLQ messages
DLQ_COUNT=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names ApproximateNumberOfMessages --region "$REGION" --query "Attributes.ApproximateNumberOfMessages" --output text)

# Check Lambda errors
LAMBDA_ERRORS=$(aws cloudwatch get-metric-statistics --namespace "AWS/Lambda" --metric-name "Errors" --dimensions Name=FunctionName,Value="$LAMBDA_NAME" --start-time "$(date_minutes_ago 5)" --end-time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" --period 300 --statistics Sum --region "$REGION" --output json 2>&1 | python3 -c "import json,sys; pts=json.load(sys.stdin).get('Datapoints',[]); print(int(sum(p['Sum'] for p in pts)))")

END=$(date +%s)

echo ""
echo "=============================================="
echo "RESULTS"
echo "=============================================="
echo "Duration:          $((END - START))s"
echo "Records sent:      $TOTAL"
echo "  Valid:           $VALID_COUNT"
echo "  Invalid:         $INVALID_COUNT"
echo "Enriched received: $ENRICHED_TOTAL"
echo "DLQ messages:      $DLQ_COUNT"
echo "Lambda errors:     $LAMBDA_ERRORS"
echo ""

# Assertions
PASS=0
FAIL=0

if [ $ENRICHED_TOTAL -ge $((VALID_COUNT * 80 / 100)) ]; then
  echo "PASS: Enriched ($ENRICHED_TOTAL) >= 80% of valid ($VALID_COUNT)"
  PASS=$((PASS+1))
else
  echo "FAIL: Enriched ($ENRICHED_TOTAL) < 80% of valid ($VALID_COUNT)"
  FAIL=$((FAIL+1))
fi

if [ "$DLQ_COUNT" -gt 0 ]; then
  echo "PASS: DLQ has $DLQ_COUNT messages (invalid records captured)"
  PASS=$((PASS+1))
else
  echo "FAIL: DLQ empty - invalid records not captured"
  FAIL=$((FAIL+1))
fi

if [ "$LAMBDA_ERRORS" -eq 0 ]; then
  echo "PASS: Zero Lambda unhandled errors"
  PASS=$((PASS+1))
else
  echo "FAIL: Lambda had $LAMBDA_ERRORS errors"
  FAIL=$((FAIL+1))
fi

# Validate a sample enriched record
echo ""
echo "Validating enriched record structure..."
SAMPLE_SHARD=$(echo "$SHARDS" | awk '{print $1}')
SAMPLE_ITER=$(aws kinesis get-shard-iterator --stream-name "$ENRICHED_STREAM" --shard-id "$SAMPLE_SHARD" --shard-iterator-type AT_TIMESTAMP --timestamp "$(date_minutes_ago 2)" --region "$REGION" --query "ShardIterator" --output text)
SAMPLE=$(aws kinesis get-records --shard-iterator "$SAMPLE_ITER" --limit 1 --region "$REGION" 2>&1)

echo "$SAMPLE" | python3 -c "
import json, sys, base64
data = json.load(sys.stdin)
recs = data.get('Records', [])
if not recs:
    print('  No records to validate')
    sys.exit(1)
r = json.loads(base64.b64decode(recs[0]['Data']))
required = ['correlationId','deviceId','deviceGroupId','timestamp','originalTimestamp','sensorType','sensorClassification','readings','location','processingMetadata']
missing = [f for f in required if f not in r]
if missing:
    print(f'  FAIL: Missing fields: {missing}')
    sys.exit(1)
loc = r['location']
loc_missing = [f for f in ['latitude','longitude','facility','zone'] if f not in loc]
if loc_missing:
    print(f'  FAIL: Location missing: {loc_missing}')
    sys.exit(1)
pm = r['processingMetadata']
pm_missing = [f for f in ['processedAt','pipelineVersion','lmiInstanceId'] if f not in pm]
if pm_missing:
    print(f'  FAIL: processingMetadata missing: {pm_missing}')
    sys.exit(1)
if 'T' not in r['timestamp']:
    print(f'  FAIL: timestamp not ISO-8601')
    sys.exit(1)
print(f'  PASS: Enriched record structure valid')
print(f'    correlationId: {r[\"correlationId\"][:50]}...')
print(f'    deviceGroupId: {r[\"deviceGroupId\"]}')
print(f'    sensorClassification: {r[\"sensorClassification\"]}')
print(f'    location: {r[\"location\"][\"facility\"]}, {r[\"location\"][\"zone\"]}')
" 2>&1 && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo ""
echo "=============================================="
echo "PASSED: $PASS / $((PASS+FAIL))"
if [ $FAIL -eq 0 ]; then
  echo "STATUS: ALL CHECKS PASSED"
else
  echo "STATUS: $FAIL CHECK(S) FAILED"
  exit 1
fi
echo "=============================================="
