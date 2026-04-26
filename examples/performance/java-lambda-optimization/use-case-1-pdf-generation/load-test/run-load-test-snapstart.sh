#!/bin/bash
STACK_NAME="lmi-blog-pdf-snapstart"
API_ENDPOINT=$(aws cloudformation describe-stacks --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text)

echo "Running load test against: $API_ENDPOINT"
API_ENDPOINT=$API_ENDPOINT artillery run load-test.yml --output report-snapstart.json
artillery report report-snapstart.json --output report-snapstart.html
echo "Report saved to report-snapstart.html"
