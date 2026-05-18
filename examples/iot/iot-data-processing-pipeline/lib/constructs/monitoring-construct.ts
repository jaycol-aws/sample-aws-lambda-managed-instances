import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';

export interface MonitoringConstructProps {
  /** KMS customer-managed key for encrypting the ops SNS topic */
  readonly encryptionKey: kms.IKey;

  /** SQS Dead Letter Queue for DLQ message count alarm */
  readonly deadLetterQueue: sqs.IQueue;

  /** End-to-end latency alarm threshold in milliseconds (default: 30000ms = 30s) */
  readonly latencyThresholdMs?: number;
}

/**
 * Monitoring Construct
 *
 * Creates the observability infrastructure for the IoT data pipeline:
 * - CloudWatch log groups per component with 30-day retention
 * - CloudWatch operational dashboard with pipeline health widgets
 * - CloudWatch Alarms for key metrics with SNS notification
 * - SNS topic `iot-pipeline-ops` for alarm notifications
 *
 * Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 5.4
 */
export class MonitoringConstruct extends Construct {
  /** SNS topic for operational alarm notifications */
  public readonly opsTopic: sns.Topic;

  /** CloudWatch operational dashboard */
  public readonly dashboard: cloudwatch.Dashboard;

  /** Log group for IoT Core component */
  public readonly iotCoreLogGroup: logs.LogGroup;

  /** Log group for LMI Processor component */
  public readonly lmiProcessorLogGroup: logs.LogGroup;

  /** Log group for Flink Analyzer component */
  public readonly flinkAnalyzerLogGroup: logs.LogGroup;

  /** Log group for Delivery component */
  public readonly deliveryLogGroup: logs.LogGroup;

  /** Log group for Alerts Publisher component */
  public readonly alertsPublisherLogGroup: logs.LogGroup;

  /** Alarm: End-to-end latency exceeds threshold */
  public readonly latencyAlarm: cloudwatch.Alarm;

  /** Alarm: LMI error rate exceeds 5% */
  public readonly lmiErrorRateAlarm: cloudwatch.Alarm;

  /** Alarm: Flink checkpoint duration exceeds 120s */
  public readonly flinkCheckpointAlarm: cloudwatch.Alarm;

  /** Alarm: Flink watermark lag exceeds 300s */
  public readonly flinkWatermarkLagAlarm: cloudwatch.Alarm;

  /** Alarm: DLQ message count greater than 0 */
  public readonly dlqAlarm: cloudwatch.Alarm;

  constructor(scope: Construct, id: string, props: MonitoringConstructProps) {
    super(scope, id);

    const latencyThreshold = props.latencyThresholdMs ?? 30000;

    // ---------------------------------------------------------------
    // SNS Topic — iot-pipeline-ops for alarm notifications
    // Req 6.3: All alarms notify this topic
    // ---------------------------------------------------------------
    this.opsTopic = new sns.Topic(this, 'OpsTopic', {
      topicName: 'iot-pipeline-ops',
      masterKey: props.encryptionKey,
      displayName: 'IoT Pipeline Operations Alarms',
    });

    // ---------------------------------------------------------------
    // CloudWatch Log Groups — per component, 30-day retention
    // Req 6.4: Structured JSON logs with correlation ID
    // ---------------------------------------------------------------
    this.iotCoreLogGroup = new logs.LogGroup(this, 'IoTCoreLogGroup', {
      logGroupName: '/iot-pipeline/iot-core',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.lmiProcessorLogGroup = new logs.LogGroup(this, 'LmiProcessorLogGroup', {
      logGroupName: '/iot-pipeline/lmi-processor',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.flinkAnalyzerLogGroup = new logs.LogGroup(this, 'FlinkAnalyzerLogGroup', {
      logGroupName: '/iot-pipeline/flink-analyzer',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.deliveryLogGroup = new logs.LogGroup(this, 'DeliveryLogGroup', {
      logGroupName: '/iot-pipeline/delivery',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.alertsPublisherLogGroup = new logs.LogGroup(this, 'AlertsPublisherLogGroup', {
      logGroupName: '/iot-pipeline/alerts-publisher',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ---------------------------------------------------------------
    // Custom Metrics — define metric references for dashboard and alarms
    // Req 6.1, 6.2, 6.5, 6.6
    // ---------------------------------------------------------------
    const pipelineNamespace = 'IoTPipeline';

    // Pipeline end-to-end latency (Req 6.1)
    const endToEndLatencyMetric = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Pipeline/EndToEndLatency',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    const endToEndLatencyP50 = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Pipeline/EndToEndLatency',
      statistic: 'p50',
      period: cdk.Duration.seconds(60),
    });

    const endToEndLatencyP90 = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Pipeline/EndToEndLatency',
      statistic: 'p90',
      period: cdk.Duration.seconds(60),
    });

    const endToEndLatencyP99 = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Pipeline/EndToEndLatency',
      statistic: 'p99',
      period: cdk.Duration.seconds(60),
    });

    // LMI Processor metrics (Req 6.2, 6.5)
    const lmiRecordsProcessed = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'LMI/RecordsProcessed',
      statistic: 'Sum',
      period: cdk.Duration.seconds(60),
    });

    const lmiRecordsFailed = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'LMI/RecordsFailed',
      statistic: 'Sum',
      period: cdk.Duration.seconds(60),
    });

    const lmiAvgProcessingDuration = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'LMI/AverageProcessingDuration',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    const lmiCpuUtilization = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'LMI/CPUUtilization',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    const lmiConcurrencySaturation = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'LMI/MultiConcurrencySaturation',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    const lmiExecutionEnvironmentCount = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'LMI/ExecutionEnvironmentCount',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    // Flink Analyzer metrics (Req 6.6)
    const flinkCheckpointDuration = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Flink/CheckpointDuration',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    const flinkRecordsPerSecond = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Flink/RecordsPerSecond',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    const flinkWatermarkLag = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Flink/WatermarkLag',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    // DLQ metric
    const dlqMessageCount = new cloudwatch.Metric({
      namespace: 'AWS/SQS',
      metricName: 'ApproximateNumberOfMessagesVisible',
      dimensionsMap: {
        QueueName: props.deadLetterQueue.queueName,
      },
      statistic: 'Sum',
      period: cdk.Duration.seconds(60),
    });

    // Flink restart count (from custom metrics)
    const flinkRestartCount = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Flink/RestartCount',
      statistic: 'Sum',
      period: cdk.Duration.seconds(60),
    });

    // Alert activity metrics
    const alertCount = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Alerts/Count',
      statistic: 'Sum',
      period: cdk.Duration.seconds(60),
    });

    // Device statistics metrics
    const activeDeviceCount = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Devices/ActiveCount',
      statistic: 'Average',
      period: cdk.Duration.seconds(60),
    });

    const recordsPerDeviceGroup = new cloudwatch.Metric({
      namespace: pipelineNamespace,
      metricName: 'Devices/RecordsPerGroup',
      statistic: 'Sum',
      period: cdk.Duration.seconds(60),
    });

    // ---------------------------------------------------------------
    // CloudWatch Alarms — 60-second evaluation period, notify ops topic
    // Req 6.3: Alarm triggers within 60 seconds, sends to SNS
    // ---------------------------------------------------------------

    // Alarm: End-to-end latency > threshold
    this.latencyAlarm = new cloudwatch.Alarm(this, 'LatencyAlarm', {
      alarmName: 'iot-pipeline-end-to-end-latency',
      alarmDescription: `End-to-end pipeline latency exceeds ${latencyThreshold}ms threshold`,
      metric: endToEndLatencyMetric,
      threshold: latencyThreshold,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    this.latencyAlarm.addAlarmAction(new cloudwatchActions.SnsAction(this.opsTopic));

    // Alarm: LMI error rate > 5%
    // Error rate = RecordsFailed / (RecordsProcessed + RecordsFailed) * 100
    const lmiErrorRateExpression = new cloudwatch.MathExpression({
      expression: '(failed / (processed + failed)) * 100',
      usingMetrics: {
        failed: lmiRecordsFailed,
        processed: lmiRecordsProcessed,
      },
      period: cdk.Duration.seconds(60),
    });

    this.lmiErrorRateAlarm = new cloudwatch.Alarm(this, 'LmiErrorRateAlarm', {
      alarmName: 'iot-pipeline-lmi-error-rate',
      alarmDescription: 'LMI processor error rate exceeds 5%',
      metric: lmiErrorRateExpression,
      threshold: 5,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    this.lmiErrorRateAlarm.addAlarmAction(new cloudwatchActions.SnsAction(this.opsTopic));

    // Alarm: Flink checkpoint duration > 120s (120000ms)
    this.flinkCheckpointAlarm = new cloudwatch.Alarm(this, 'FlinkCheckpointAlarm', {
      alarmName: 'iot-pipeline-flink-checkpoint-duration',
      alarmDescription: 'Flink checkpoint duration exceeds 120 seconds',
      metric: flinkCheckpointDuration,
      threshold: 120000,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    this.flinkCheckpointAlarm.addAlarmAction(new cloudwatchActions.SnsAction(this.opsTopic));

    // Alarm: Flink watermark lag > 300s (300000ms)
    this.flinkWatermarkLagAlarm = new cloudwatch.Alarm(this, 'FlinkWatermarkLagAlarm', {
      alarmName: 'iot-pipeline-flink-watermark-lag',
      alarmDescription: 'Flink watermark lag exceeds 300 seconds',
      metric: flinkWatermarkLag,
      threshold: 300000,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    this.flinkWatermarkLagAlarm.addAlarmAction(new cloudwatchActions.SnsAction(this.opsTopic));

    // Alarm: DLQ message count > 0
    this.dlqAlarm = new cloudwatch.Alarm(this, 'DlqAlarm', {
      alarmName: 'iot-pipeline-dlq-messages',
      alarmDescription: 'Dead letter queue has messages - failed records require investigation',
      metric: dlqMessageCount,
      threshold: 0,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    this.dlqAlarm.addAlarmAction(new cloudwatchActions.SnsAction(this.opsTopic));

    // ---------------------------------------------------------------
    // CloudWatch Operational Dashboard
    // Req 5.4: Real-time operational dashboard provisioned via CDK
    // Widgets: throughput, latency, LMI health, Flink health,
    //          alert activity, error rates, device statistics
    // ---------------------------------------------------------------
    this.dashboard = new cloudwatch.Dashboard(this, 'OperationalDashboard', {
      dashboardName: 'IoT-Pipeline-Operations',
      defaultInterval: cdk.Duration.hours(3),
    });

    // Row 1: Pipeline Throughput
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Pipeline Throughput (records/min)',
        left: [lmiRecordsProcessed, lmiRecordsFailed],
        right: [flinkRecordsPerSecond],
        width: 12,
        height: 6,
      }),
      new cloudwatch.GraphWidget({
        title: 'End-to-End Latency (P50/P90/P99)',
        left: [endToEndLatencyP50, endToEndLatencyP90, endToEndLatencyP99],
        width: 12,
        height: 6,
        leftAnnotations: [
          {
            value: latencyThreshold,
            label: 'Alarm Threshold',
            color: '#d13212',
          },
        ],
      }),
    );

    // Row 2: LMI Health
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'LMI Health — CPU Utilization',
        left: [lmiCpuUtilization],
        width: 8,
        height: 6,
      }),
      new cloudwatch.GraphWidget({
        title: 'LMI Health — Execution Environments',
        left: [lmiExecutionEnvironmentCount],
        width: 8,
        height: 6,
      }),
      new cloudwatch.GraphWidget({
        title: 'LMI Health — Concurrency Saturation',
        left: [lmiConcurrencySaturation],
        width: 8,
        height: 6,
      }),
    );

    // Row 3: Flink Health
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Flink — Checkpoint Duration (ms)',
        left: [flinkCheckpointDuration],
        width: 8,
        height: 6,
        leftAnnotations: [
          {
            value: 120000,
            label: 'Alarm Threshold (120s)',
            color: '#d13212',
          },
        ],
      }),
      new cloudwatch.GraphWidget({
        title: 'Flink — Records/Second',
        left: [flinkRecordsPerSecond],
        width: 8,
        height: 6,
      }),
      new cloudwatch.GraphWidget({
        title: 'Flink — Watermark Lag (ms)',
        left: [flinkWatermarkLag],
        width: 8,
        height: 6,
        leftAnnotations: [
          {
            value: 300000,
            label: 'Alarm Threshold (300s)',
            color: '#d13212',
          },
        ],
      }),
    );

    // Row 4: Alert Activity
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Alert Activity',
        left: [alertCount],
        width: 12,
        height: 6,
      }),
      new cloudwatch.GraphWidget({
        title: 'Error Rates',
        left: [dlqMessageCount, lmiRecordsFailed, flinkRestartCount],
        width: 12,
        height: 6,
      }),
    );

    // Row 5: Device Statistics
    this.dashboard.addWidgets(
      new cloudwatch.SingleValueWidget({
        title: 'Active Device Count',
        metrics: [activeDeviceCount],
        width: 12,
        height: 4,
      }),
      new cloudwatch.GraphWidget({
        title: 'Records per Device Group',
        left: [recordsPerDeviceGroup],
        width: 12,
        height: 4,
      }),
    );
  }
}
