#!/bin/bash
# Run load test against Standard Lambda deployment
STACK_NAME="lmi-blog-pdf-standard"
API_ENDPOINT=$(aws cloudformation describe-stacks --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text)

echo "Running load test against: $API_ENDPOINT"
API_ENDPOINT=$API_ENDPOINT artillery run load-test.yml --output report-standard.json
artillery report report-standard.json --output report-standard.html
echo "Report saved to report-standard.html"
