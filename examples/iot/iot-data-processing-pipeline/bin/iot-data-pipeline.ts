#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { IoTDataPipelineStack } from '../lib/iot-data-pipeline-stack';

const app = new cdk.App();

const environment = app.node.tryGetContext('environment') ?? 'dev';
const projectName = app.node.tryGetContext('projectName') ?? 'iot-data-pipeline';
const costCenter = app.node.tryGetContext('costCenter') ?? 'iot-operations';
const region = app.node.tryGetContext('region') ?? 'us-east-1';

new IoTDataPipelineStack(app, `IoTDataPipelineStack-${environment}`, {
  env: {
    region,
    account: process.env.CDK_DEFAULT_ACCOUNT,
  },
  tags: {
    Environment: environment,
    Project: projectName,
    CostCenter: costCenter,
  },
});
