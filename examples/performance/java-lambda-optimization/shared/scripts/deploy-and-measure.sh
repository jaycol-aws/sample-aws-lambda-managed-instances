#!/bin/bash
# deploy-and-measure.sh — Build, measure artifact size, deploy with timing
#
# Usage:
#   ./deploy-and-measure.sh <use-case-dir> <template> <stack-name> [--memory 1024]
#
# Example:
#   ./deploy-and-measure.sh use-case-1-pdf-generation template.yaml lmi-blog-pdf-standard
#   ./deploy-and-measure.sh use-case-1-pdf-generation template.snapstart.yaml lmi-blog-pdf-snapstart
#   ./deploy-and-measure.sh use-case-1-pdf-generation template.lmi.yaml lmi-blog-pdf-lmi \
#       --parameter-overrides "SubnetIds=subnet-xxx,subnet-yyy SecurityGroupIds=sg-zzz"
#
# Captures:
#   - Build time (mvn package)
#   - Deployment artifact size (JAR or native zip)
#   - SAM deploy time
#   - All written to measurements/<stack-name>.json

set -euo pipefail

USE_CASE_DIR="$1"
TEMPLATE="$2"
STACK_NAME="$3"
shift 3

MEMORY=""
PARAM_OVERRIDES=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --memory) MEMORY="$2"; shift 2 ;;
        --parameter-overrides) PARAM_OVERRIDES="$2"; shift 2 ;;
        *) shift ;;
    esac
done

MEASUREMENTS_DIR="$(dirname "$0")/../../measurements"
mkdir -p "$MEASUREMENTS_DIR"
OUTPUT_FILE="$MEASUREMENTS_DIR/${STACK_NAME}.json"

cd "$(dirname "$0")/../../${USE_CASE_DIR}"

echo "═══════════════════════════════════════════════════════"
echo "  Building: ${USE_CASE_DIR}"
echo "  Template: ${TEMPLATE}"
echo "  Stack:    ${STACK_NAME}"
echo "═══════════════════════════════════════════════════════"

# ── Build ────────────────────────────────────────────────────────────────────
echo ""
echo "▶ Building..."
BUILD_START=$(date +%s%N)

if [[ "$TEMPLATE" == *"native"* ]]; then
    mvn clean package -Pnative -q
else
    mvn clean package -q
fi

BUILD_END=$(date +%s%N)
BUILD_TIME_MS=$(( (BUILD_END - BUILD_START) / 1000000 ))
echo "  Build time: ${BUILD_TIME_MS}ms"

# ── Measure artifact size ────────────────────────────────────────────────────
if [[ "$TEMPLATE" == *"native"* ]]; then
    ARTIFACT=$(find target -name "native-zip.zip" 2>/dev/null | head -1)
else
    ARTIFACT=$(find target -name "*-SNAPSHOT.jar" -not -name "*.original" 2>/dev/null | head -1)
fi

if [[ -n "$ARTIFACT" ]]; then
    ARTIFACT_SIZE=$(stat -f%z "$ARTIFACT" 2>/dev/null || stat --printf="%s" "$ARTIFACT" 2>/dev/null)
    ARTIFACT_SIZE_MB=$(echo "scale=2; $ARTIFACT_SIZE / 1048576" | bc)
    echo "  Artifact: ${ARTIFACT} (${ARTIFACT_SIZE_MB} MB)"
else
    ARTIFACT_SIZE=0
    ARTIFACT_SIZE_MB="0"
    echo "  ⚠ No artifact found"
fi

# ── Deploy ───────────────────────────────────────────────────────────────────
echo ""
echo "▶ Deploying..."
DEPLOY_START=$(date +%s%N)

SAM_ARGS="--template-file $TEMPLATE --stack-name $STACK_NAME --resolve-s3 --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset"

if [[ -n "$MEMORY" ]]; then
    SAM_ARGS="$SAM_ARGS --parameter-overrides MemorySize=$MEMORY"
fi

if [[ -n "$PARAM_OVERRIDES" ]]; then
    SAM_ARGS="$SAM_ARGS --parameter-overrides $PARAM_OVERRIDES"
fi

sam deploy $SAM_ARGS 2>&1 | tail -5

DEPLOY_END=$(date +%s%N)
DEPLOY_TIME_MS=$(( (DEPLOY_END - DEPLOY_START) / 1000000 ))
echo "  Deploy time: ${DEPLOY_TIME_MS}ms"

# ── Get outputs ──────────────────────────────────────────────────────────────
API_ENDPOINT=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text 2>/dev/null || echo "N/A")

# ── Write measurements ───────────────────────────────────────────────────────
cat > "$OUTPUT_FILE" << EOF
{
  "stackName": "${STACK_NAME}",
  "useCase": "${USE_CASE_DIR}",
  "template": "${TEMPLATE}",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "buildTimeMs": ${BUILD_TIME_MS},
  "artifactSizeBytes": ${ARTIFACT_SIZE},
  "artifactSizeMB": ${ARTIFACT_SIZE_MB},
  "deployTimeMs": ${DEPLOY_TIME_MS},
  "memoryMB": "${MEMORY:-default}",
  "apiEndpoint": "${API_ENDPOINT}"
}
EOF

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✓ Measurements saved to: ${OUTPUT_FILE}"
echo "  API Endpoint: ${API_ENDPOINT}"
echo "═══════════════════════════════════════════════════════"
