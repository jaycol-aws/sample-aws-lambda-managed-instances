#!/bin/bash
# Cleanup script for IoT Data Pipeline
#
# Removes ALL resources created by the stack, including those with RETAIN
# removal policies that survive `cdk destroy`. Run this for a full teardown.
#
# Usage:
#   ./scripts/cleanup.sh [--region REGION]
#
# Resources cleaned up:
#   - CloudFormation stack (if still exists)
#   - Kinesis streams (iot-raw-telemetry-stream, iot-enriched-telemetry-stream, iot-alerts-stream)
#   - S3 bucket (iot-data-lake-<account>-<region>) — empties then deletes
#   - DynamoDB table (iot-analytics-results)
#   - SQS queue (iot-pipeline-dlq)
#   - SNS topics (iot-pipeline-alerts, iot-pipeline-ops)
#   - Lambda functions (iot-lmi-processor, iot-alerts-publisher)
#   - CloudWatch log groups (/iot-pipeline/*)
#   - KMS key (IoT data pipeline key — scheduled for deletion)
#   - IoT Core topic rule (IoTTelemetryIngestionRule)
set -euo pipefail

# Parse arguments
REGION=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    -h|--help) echo "Usage: $0 [--region REGION]"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

REGION="${REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
STACK_NAME="IoTDataPipelineStack-dev"

echo "========================================="
echo "IoT Data Pipeline — Full Cleanup"
echo "========================================="
echo "Region:  $REGION"
echo "Account: $ACCOUNT"
echo "Stack:   $STACK_NAME"
echo "========================================="
echo ""

# Helper: suppress errors for resources that may not exist
safe_delete() {
  "$@" 2>/dev/null || true
}

# ---------------------------------------------------------------
# 1. Delete CloudFormation stack (handles most resources)
# ---------------------------------------------------------------
echo "--- Step 1: CloudFormation stack ---"
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].StackStatus" \
  --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [[ "$STACK_STATUS" != "DOES_NOT_EXIST" && "$STACK_STATUS" != "DELETE_COMPLETE" ]]; then
  echo "  Deleting stack (status: $STACK_STATUS)..."
  aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
  echo "  Waiting for stack deletion..."
  aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION" 2>/dev/null || true
  echo "  Stack deleted."
else
  echo "  Stack does not exist. Skipping."
fi

# ---------------------------------------------------------------
# 2. Kinesis streams (may survive stack delete during active processing)
# ---------------------------------------------------------------
echo ""
echo "--- Step 2: Kinesis streams ---"
STREAMS=("iot-raw-telemetry-stream" "iot-enriched-telemetry-stream" "iot-alerts-stream")
for STREAM in "${STREAMS[@]}"; do
  if aws kinesis describe-stream --stream-name "$STREAM" --region "$REGION" >/dev/null 2>&1; then
    echo "  Deleting stream: $STREAM"
    aws kinesis delete-stream --stream-name "$STREAM" --region "$REGION"
  else
    echo "  Stream $STREAM not found. Skipping."
  fi
done

# ---------------------------------------------------------------
# 3. S3 bucket (RETAIN policy — must empty and delete manually)
# ---------------------------------------------------------------
echo ""
echo "--- Step 3: S3 data lake bucket ---"
BUCKET="iot-data-lake-${ACCOUNT}-${REGION}"
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "  Emptying bucket: $BUCKET (including versions)..."
  aws s3api list-object-versions --bucket "$BUCKET" --region "$REGION" \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null | \
    python3 -c "
import json, sys
data = json.load(sys.stdin)
objects = data.get('Objects') or []
if objects:
    print(json.dumps({'Objects': objects, 'Quiet': True}))
" | while read -r batch; do
    if [ -n "$batch" ]; then
      aws s3api delete-objects --bucket "$BUCKET" --delete "$batch" --region "$REGION" >/dev/null
    fi
  done

  # Also remove delete markers
  aws s3api list-object-versions --bucket "$BUCKET" --region "$REGION" \
    --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null | \
    python3 -c "
import json, sys
data = json.load(sys.stdin)
objects = data.get('Objects') or []
if objects:
    print(json.dumps({'Objects': objects, 'Quiet': True}))
" | while read -r batch; do
    if [ -n "$batch" ]; then
      aws s3api delete-objects --bucket "$BUCKET" --delete "$batch" --region "$REGION" >/dev/null
    fi
  done

  echo "  Deleting bucket: $BUCKET"
  aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION"
  echo "  Bucket deleted."
else
  echo "  Bucket $BUCKET not found. Skipping."
fi

# ---------------------------------------------------------------
# 4. DynamoDB table (RETAIN policy)
# IMPORTANT: Must run BEFORE KMS key deletion (step 9).
# If the KMS key is scheduled for deletion first, the table
# becomes INACCESSIBLE_ENCRYPTION_CREDENTIALS and cannot be
# deleted until the key is re-enabled or the 7-day window passes.
# ---------------------------------------------------------------
echo ""
echo "--- Step 4: DynamoDB tables ---"

# Check for the well-known table name
TABLE="iot-analytics-results"
if aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1; then
  echo "  Deleting table: $TABLE"
  aws dynamodb delete-table --table-name "$TABLE" --region "$REGION" >/dev/null
  echo "  Waiting for table deletion..."
  aws dynamodb wait table-not-exists --table-name "$TABLE" --region "$REGION" 2>/dev/null || true
  echo "  Table deleted."
else
  echo "  Table $TABLE not found. Skipping."
fi

# Also catch CDK-generated long table names (e.g. IoTDataPipelineStack-dev-DeliveryAnalytics...)
CDK_TABLES=$(aws dynamodb list-tables --region "$REGION" \
  --query "TableNames[?contains(@, 'IoTDataPipeline') || contains(@, 'iot-analytics')]" \
  --output text 2>/dev/null || echo "")
for CDK_TABLE in $CDK_TABLES; do
  if [ -n "$CDK_TABLE" ] && [ "$CDK_TABLE" != "$TABLE" ]; then
    echo "  Deleting CDK-generated table: $CDK_TABLE"
    aws dynamodb delete-table --table-name "$CDK_TABLE" --region "$REGION" >/dev/null 2>&1 || true
    aws dynamodb wait table-not-exists --table-name "$CDK_TABLE" --region "$REGION" 2>/dev/null || true
    echo "  Table deleted."
  fi
done

# ---------------------------------------------------------------
# 5. SQS queue
# ---------------------------------------------------------------
echo ""
echo "--- Step 5: SQS dead letter queue ---"
DLQ_URL=$(aws sqs get-queue-url --queue-name "iot-pipeline-dlq" --region "$REGION" --query QueueUrl --output text 2>/dev/null || echo "")
if [ -n "$DLQ_URL" ]; then
  echo "  Deleting queue: iot-pipeline-dlq"
  aws sqs delete-queue --queue-url "$DLQ_URL" --region "$REGION"
else
  echo "  Queue iot-pipeline-dlq not found. Skipping."
fi

# ---------------------------------------------------------------
# 6. SNS topics
# ---------------------------------------------------------------
echo ""
echo "--- Step 6: SNS topics ---"
TOPICS=("iot-pipeline-alerts" "iot-pipeline-ops")
for TOPIC_NAME in "${TOPICS[@]}"; do
  TOPIC_ARN="arn:aws:sns:${REGION}:${ACCOUNT}:${TOPIC_NAME}"
  if aws sns get-topic-attributes --topic-arn "$TOPIC_ARN" --region "$REGION" >/dev/null 2>&1; then
    echo "  Deleting topic: $TOPIC_NAME"
    aws sns delete-topic --topic-arn "$TOPIC_ARN" --region "$REGION"
  else
    echo "  Topic $TOPIC_NAME not found. Skipping."
  fi
done

# ---------------------------------------------------------------
# 7. Lambda functions
# ---------------------------------------------------------------
echo ""
echo "--- Step 7: Lambda functions ---"
FUNCTIONS=("iot-lmi-processor" "iot-alerts-publisher")
for FN in "${FUNCTIONS[@]}"; do
  if aws lambda get-function --function-name "$FN" --region "$REGION" >/dev/null 2>&1; then
    echo "  Deleting function: $FN"
    aws lambda delete-function --function-name "$FN" --region "$REGION"
  else
    echo "  Function $FN not found. Skipping."
  fi
done

# ---------------------------------------------------------------
# 8. CloudWatch log groups
# ---------------------------------------------------------------
echo ""
echo "--- Step 8: CloudWatch log groups ---"
LOG_GROUPS=$(aws logs describe-log-groups \
  --log-group-name-prefix "/iot-pipeline" \
  --region "$REGION" \
  --query "logGroups[].logGroupName" --output text 2>/dev/null || echo "")

# Also check Lambda auto-created log groups
LAMBDA_LOGS=$(aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/iot-" \
  --region "$REGION" \
  --query "logGroups[].logGroupName" --output text 2>/dev/null || echo "")

ALL_LOGS="$LOG_GROUPS $LAMBDA_LOGS"
if [ -n "$(echo "$ALL_LOGS" | tr -d '[:space:]')" ]; then
  for LG in $ALL_LOGS; do
    echo "  Deleting log group: $LG"
    aws logs delete-log-group --log-group-name "$LG" --region "$REGION" 2>/dev/null || true
  done
else
  echo "  No IoT pipeline log groups found. Skipping."
fi

# ---------------------------------------------------------------
# 9. KMS key (RETAIN policy — schedule for deletion)
# IMPORTANT: Only safe to run AFTER DynamoDB tables are deleted
# (step 4). Scheduling key deletion while a table still uses it
# makes the table inaccessible and triggers Health Dashboard alerts.
# ---------------------------------------------------------------
echo ""
echo "--- Step 9: KMS keys ---"

# Safety check: abort KMS deletion if any IoT tables still exist
REMAINING_TABLES=$(aws dynamodb list-tables --region "$REGION" \
  --query "TableNames[?contains(@, 'IoTDataPipeline') || contains(@, 'iot-analytics')]" \
  --output text 2>/dev/null || echo "")
if [ -n "$(echo "$REMAINING_TABLES" | tr -d '[:space:]')" ]; then
  echo "  WARNING: DynamoDB tables still exist that may use these keys:"
  echo "    $REMAINING_TABLES"
  echo "  Skipping KMS deletion to avoid INACCESSIBLE_ENCRYPTION_CREDENTIALS."
  echo "  Delete the tables first, then re-run this script."
else
  KEY_IDS=$(aws kms list-keys --region "$REGION" --query "Keys[].KeyId" --output text 2>/dev/null)
  for KEY_ID in $KEY_IDS; do
    DESC=$(aws kms describe-key --key-id "$KEY_ID" --region "$REGION" \
      --query "KeyMetadata.{Desc:Description,State:KeyState}" --output json 2>/dev/null)
    KEY_DESC=$(echo "$DESC" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('Desc',''))" 2>/dev/null)
    KEY_STATE=$(echo "$DESC" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('State',''))" 2>/dev/null)

    if [[ "$KEY_DESC" == *"IoT data pipeline"* && "$KEY_STATE" == "Enabled" ]]; then
      echo "  Scheduling key deletion: $KEY_ID (7-day wait)"
      aws kms schedule-key-deletion --key-id "$KEY_ID" --pending-window-in-days 7 --region "$REGION" >/dev/null
    fi
  done
  echo "  Done (keys pending deletion will be removed after 7 days)."
fi

# ---------------------------------------------------------------
# 10. IoT Core topic rule
# ---------------------------------------------------------------
echo ""
echo "--- Step 10: IoT Core topic rule ---"
if aws iot get-topic-rule --rule-name "IoTTelemetryIngestionRule" --region "$REGION" >/dev/null 2>&1; then
  echo "  Deleting IoT rule: IoTTelemetryIngestionRule"
  aws iot delete-topic-rule --rule-name "IoTTelemetryIngestionRule" --region "$REGION"
else
  echo "  IoT rule not found. Skipping."
fi

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
echo ""
echo "========================================="
echo "Cleanup complete."
echo ""
echo "Note: KMS keys are scheduled for deletion"
echo "with a 7-day waiting period. They can be"
echo "cancelled with: aws kms cancel-key-deletion"
echo "========================================="
