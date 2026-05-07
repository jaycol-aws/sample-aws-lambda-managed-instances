#!/bin/bash
STACK_NAME="lmi-blog-pdf-lmi"
API_ENDPOINT=$(aws cloudformation describe-stacks --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text)

echo "Running load test against: $API_ENDPOINT"
API_ENDPOINT=$API_ENDPOINT artillery run load-test.yml --output report-lmi.json
artillery report report-lmi.json --output report-lmi.html
echo "Report saved to report-lmi.html"
