package com.iotpipeline;

import org.apache.avro.Schema;
import org.apache.avro.reflect.ReflectData;
import org.apache.flink.api.common.eventtime.SerializableTimestampAssigner;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.file.sink.FileSink;
import org.apache.flink.connector.kinesis.source.KinesisStreamsSource;
import org.apache.flink.core.fs.Path;
import org.apache.flink.formats.parquet.ParquetBuilder;
import org.apache.flink.formats.parquet.ParquetWriterFactory;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.CheckpointConfig;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.sink.filesystem.BucketAssigner;
import org.apache.flink.streaming.api.functions.sink.filesystem.OutputFileConfig;
import org.apache.flink.streaming.api.functions.sink.filesystem.bucketassigners.SimpleVersionedStringSerializer;
import org.apache.flink.streaming.api.windowing.assigners.TumblingEventTimeWindows;
import org.apache.parquet.avro.AvroParquetWriter;
import org.apache.parquet.hadoop.ParquetWriter;
import org.apache.parquet.hadoop.metadata.CompressionCodecName;
import org.apache.parquet.io.OutputFile;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.List;

/**
 * Flink application entry point for the IoT data pipeline analytics layer.
 * <p>
 * Pipeline topology:
 * 1. Kinesis Source (enriched stream) → EnrichedRecordDeserializer
 * 2. Key by deviceGroupId
 * 3. Tumbling event time window (configurable, default 60s)
 * 4. StatisticsAggregator (ProcessWindowFunction)
 * 5. Fan-out:
 *    a. IdempotentDynamoDBSink (analytics results → DynamoDB)
 *    b. ThresholdDetector → AlertEmitter (alerts → Kinesis alerts stream)
 * 6. Separately: enriched records → FileSink (Parquet with Snappy compression to S3)
 * <p>
 * Checkpointing: 60s interval, EXACTLY_ONCE mode, 5s min pause, RocksDB state backend.
 * Parallelism: 4 minimum.
 * <p>
 * Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 5.1, 5.2
 */
public class FlinkAnalyzerApp {

    private static final Logger LOG = LoggerFactory.getLogger(FlinkAnalyzerApp.class);

    // Default configuration values
    private static final String DEFAULT_ENRICHED_STREAM_NAME = "iot-enriched-telemetry-stream";
    private static final String DEFAULT_ALERTS_STREAM_NAME = "iot-alerts-stream";
    private static final String DEFAULT_DYNAMODB_TABLE_NAME = "iot-analytics-results";
    private static final String DEFAULT_S3_OUTPUT_PATH = "s3://iot-data-lake/";
    private static final int DEFAULT_WINDOW_DURATION_SECONDS = 60;
    private static final String DEFAULT_AWS_REGION = "us-east-1";

    // Checkpointing configuration
    private static final long CHECKPOINT_INTERVAL_MS = 60_000L;
    private static final long MIN_PAUSE_BETWEEN_CHECKPOINTS_MS = 5_000L;
    private static final int MIN_PARALLELISM = 4;

    public static void main(String[] args) throws Exception {
        // Load configuration from command-line args and environment variables
        ParameterTool params = ParameterTool.fromArgs(args);

        String enrichedStreamName = getConfig(params, "ENRICHED_STREAM_NAME", DEFAULT_ENRICHED_STREAM_NAME);
        String alertsStreamName = getConfig(params, "ALERTS_STREAM_NAME", DEFAULT_ALERTS_STREAM_NAME);
        String dynamoDbTableName = getConfig(params, "DYNAMODB_TABLE_NAME", DEFAULT_DYNAMODB_TABLE_NAME);
        String s3OutputPath = getConfig(params, "S3_OUTPUT_PATH", DEFAULT_S3_OUTPUT_PATH);
        int windowDurationSeconds = Integer.parseInt(
                getConfig(params, "WINDOW_DURATION_SECONDS", String.valueOf(DEFAULT_WINDOW_DURATION_SECONDS)));
        String awsRegion = getConfig(params, "AWS_REGION", DEFAULT_AWS_REGION);

        LOG.info("Starting FlinkAnalyzerApp with configuration: enrichedStream={}, alertsStream={}, "
                        + "dynamoDbTable={}, s3OutputPath={}, windowDuration={}s, region={}",
                enrichedStreamName, alertsStreamName, dynamoDbTableName,
                s3OutputPath, windowDurationSeconds, awsRegion);

        // Set up execution environment
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        // Configure parallelism (minimum 4)
        int parallelism = Math.max(MIN_PARALLELISM, env.getParallelism());
        env.setParallelism(parallelism);

        // Configure checkpointing
        configureCheckpointing(env);

        // Configure RocksDB state backend
        env.setStateBackend(new org.apache.flink.contrib.streaming.state.EmbeddedRocksDBStateBackend());

        // Make parameters available to all operators
        env.getConfig().setGlobalJobParameters(params);

        // --- Source: Kinesis enriched telemetry stream ---
        KinesisStreamsSource<EnrichedRecord> kinesisSource = KinesisStreamsSource.<EnrichedRecord>builder()
                .setStreamArn("arn:aws:kinesis:" + awsRegion + ":000000000000:stream/" + enrichedStreamName)
                .setDeserializationSchema(new EnrichedRecordDeserializer())
                .build();

        // Watermark strategy using originalTimestamp as event time
        WatermarkStrategy<EnrichedRecord> watermarkStrategy = WatermarkStrategy
                .<EnrichedRecord>forBoundedOutOfOrderness(Duration.ofSeconds(10))
                .withTimestampAssigner(
                        (SerializableTimestampAssigner<EnrichedRecord>) (record, recordTimestamp) ->
                                record.getOriginalTimestamp()
                );

        DataStream<EnrichedRecord> enrichedStream = env
                .fromSource(kinesisSource, watermarkStrategy, "Kinesis Enriched Source")
                .uid("kinesis-enriched-source");

        // --- Branch 1: Windowed aggregation pipeline ---
        SingleOutputStreamOperator<AnalyticsResult> analyticsStream = enrichedStream
                .keyBy(EnrichedRecord::getDeviceGroupId)
                .window(TumblingEventTimeWindows.of(Duration.ofSeconds(windowDurationSeconds)))
                .process(new StatisticsAggregator(windowDurationSeconds))
                .uid("statistics-aggregator")
                .name("Statistics Aggregator");

        // --- Sink 1a: DynamoDB analytics results ---
        analyticsStream
                .addSink(new IdempotentDynamoDBSink(dynamoDbTableName))
                .uid("dynamodb-sink")
                .name("DynamoDB Analytics Sink");

        // --- Sink 1b: Threshold detection → Alert emission ---
        List<ThresholdDetector.ThresholdRule> thresholdRules = getDefaultThresholdRules();

        DataStream<AlertRecord> alertStream = analyticsStream
                .flatMap(new ThresholdDetector(thresholdRules))
                .uid("threshold-detector")
                .name("Threshold Detector");

        alertStream
                .addSink(new AlertEmitter(alertsStreamName))
                .uid("alert-emitter-sink")
                .name("Alert Emitter Sink");

        // --- Branch 2: S3 data lake (enriched records as Parquet with Snappy compression) ---
        ParquetWriterFactory<EnrichedRecord> parquetWriterFactory = createSnappyParquetWriterFactory();

        FileSink<EnrichedRecord> s3Sink = FileSink
                .<EnrichedRecord>forBulkFormat(new Path(s3OutputPath), parquetWriterFactory)
                .withBucketAssigner(new SensorTypeDateBucketAssigner())
                .withOutputFileConfig(OutputFileConfig.builder()
                        .withPartPrefix("enriched")
                        .withPartSuffix(".parquet")
                        .build())
                .build();

        enrichedStream
                .sinkTo(s3Sink)
                .uid("s3-data-lake-sink")
                .name("S3 Data Lake Sink");

        // Execute the Flink job
        env.execute("IoT Pipeline Flink Analyzer");
    }

    /**
     * Creates a ParquetWriterFactory configured with Snappy compression for EnrichedRecord.
     * Uses Avro reflection-based serialization to convert POJOs to Parquet format.
     */
    private static ParquetWriterFactory<EnrichedRecord> createSnappyParquetWriterFactory() {
        return new ParquetWriterFactory<>(new ParquetBuilder<EnrichedRecord>() {
            private static final long serialVersionUID = 1L;

            @Override
            public ParquetWriter<EnrichedRecord> createWriter(OutputFile outputFile) throws IOException {
                Schema schema = ReflectData.get().getSchema(EnrichedRecord.class);
                return AvroParquetWriter.<EnrichedRecord>builder(outputFile)
                        .withSchema(schema)
                        .withDataModel(ReflectData.get())
                        .withCompressionCodec(CompressionCodecName.SNAPPY)
                        .build();
            }
        });
    }

    /**
     * Configures checkpointing with EXACTLY_ONCE semantics.
     */
    private static void configureCheckpointing(StreamExecutionEnvironment env) {
        CheckpointConfig checkpointConfig = env.getCheckpointConfig();
        env.enableCheckpointing(CHECKPOINT_INTERVAL_MS, CheckpointingMode.EXACTLY_ONCE);
        checkpointConfig.setMinPauseBetweenCheckpoints(MIN_PAUSE_BETWEEN_CHECKPOINTS_MS);
        checkpointConfig.setCheckpointTimeout(120_000L);
        checkpointConfig.setTolerableCheckpointFailureNumber(3);
        checkpointConfig.setExternalizedCheckpointCleanup(
                CheckpointConfig.ExternalizedCheckpointCleanup.RETAIN_ON_CANCELLATION);
    }

    /**
     * Retrieves configuration value from ParameterTool args, then environment variables,
     * falling back to the provided default.
     */
    static String getConfig(ParameterTool params, String key, String defaultValue) {
        // Check ParameterTool first (command-line args)
        String value = params.get(key);
        if (value != null && !value.isEmpty()) {
            return value;
        }
        // Check environment variable
        String envValue = System.getenv(key);
        if (envValue != null && !envValue.isEmpty()) {
            return envValue;
        }
        return defaultValue;
    }

    /**
     * Returns default threshold rules for alert detection.
     * In production, these would be loaded from configuration or a rules engine.
     */
    private static List<ThresholdDetector.ThresholdRule> getDefaultThresholdRules() {
        return Arrays.asList(
                new ThresholdDetector.ThresholdRule("temperature", "average", 85.0,
                        ThresholdDetector.Operator.GREATER_THAN, "warning"),
                new ThresholdDetector.ThresholdRule("temperature", "max", 100.0,
                        ThresholdDetector.Operator.GREATER_THAN, "critical"),
                new ThresholdDetector.ThresholdRule("humidity", "average", 90.0,
                        ThresholdDetector.Operator.GREATER_THAN, "warning"),
                new ThresholdDetector.ThresholdRule("vibration", "stddev", 5.0,
                        ThresholdDetector.Operator.GREATER_THAN, "warning"),
                new ThresholdDetector.ThresholdRule("pressure", "max", 150.0,
                        ThresholdDetector.Operator.GREATER_THAN, "critical")
        );
    }

    // --- Custom BucketAssigner for S3 partitioning ---

    /**
     * Custom bucket assigner that partitions enriched records by date and sensor type.
     * Produces paths like: year=2025/month=01/day=15/sensor_type=temperature/
     */
    static class SensorTypeDateBucketAssigner implements BucketAssigner<EnrichedRecord, String> {

        private static final long serialVersionUID = 1L;

        private static final DateTimeFormatter YEAR_FMT = DateTimeFormatter.ofPattern("yyyy").withZone(ZoneOffset.UTC);
        private static final DateTimeFormatter MONTH_FMT = DateTimeFormatter.ofPattern("MM").withZone(ZoneOffset.UTC);
        private static final DateTimeFormatter DAY_FMT = DateTimeFormatter.ofPattern("dd").withZone(ZoneOffset.UTC);

        @Override
        public String getBucketId(EnrichedRecord element, BucketAssigner.Context context) {
            Instant eventTime = Instant.ofEpochMilli(element.getOriginalTimestamp());
            String year = YEAR_FMT.format(eventTime);
            String month = MONTH_FMT.format(eventTime);
            String day = DAY_FMT.format(eventTime);
            String sensorType = element.getSensorType() != null ? element.getSensorType() : "unknown";

            return "year=" + year + "/month=" + month + "/day=" + day + "/sensor_type=" + sensorType;
        }

        @Override
        public SimpleVersionedStringSerializer getSerializer() {
            return SimpleVersionedStringSerializer.INSTANCE;
        }
    }
}
