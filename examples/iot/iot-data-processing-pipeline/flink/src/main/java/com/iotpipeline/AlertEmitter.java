package com.iotpipeline;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.services.kinesis.KinesisClient;
import software.amazon.awssdk.services.kinesis.model.PutRecordRequest;
import software.amazon.awssdk.services.kinesis.model.PutRecordResponse;

/**
 * Flink SinkFunction that writes AlertRecord instances to the iot-alerts-stream
 * Kinesis Data Stream.
 * <p>
 * Each alert record is serialized to JSON using Jackson ObjectMapper and written
 * to Kinesis with the deviceGroupId as the partition key. This ensures alerts
 * for the same device group are routed to the same shard for ordered processing.
 * <p>
 * Validates: Requirements 4.3
 */
public class AlertEmitter implements SinkFunction<AlertRecord> {

    private static final long serialVersionUID = 1L;
    private static final Logger LOG = LoggerFactory.getLogger(AlertEmitter.class);

    private static final String DEFAULT_STREAM_NAME = "iot-alerts-stream";

    private final String streamName;
    private transient KinesisClient kinesisClient;
    private transient ObjectMapper objectMapper;

    /**
     * Creates an AlertEmitter with the default stream name ("iot-alerts-stream").
     */
    public AlertEmitter() {
        this(DEFAULT_STREAM_NAME);
    }

    /**
     * Creates an AlertEmitter with a configurable stream name.
     *
     * @param streamName the Kinesis Data Stream name to write alerts to
     */
    public AlertEmitter(String streamName) {
        this.streamName = streamName;
    }

    /**
     * Package-private constructor for testing, allowing injection of a Kinesis client.
     *
     * @param streamName    the Kinesis Data Stream name
     * @param kinesisClient the Kinesis client to use
     */
    AlertEmitter(String streamName, KinesisClient kinesisClient) {
        this.streamName = streamName;
        this.kinesisClient = kinesisClient;
    }

    @Override
    public void invoke(AlertRecord value, Context context) throws Exception {
        if (kinesisClient == null) {
            kinesisClient = KinesisClient.create();
        }
        if (objectMapper == null) {
            objectMapper = new ObjectMapper();
        }

        String json = serializeAlert(value);
        String partitionKey = value.getDeviceGroupId();

        PutRecordRequest request = PutRecordRequest.builder()
                .streamName(streamName)
                .partitionKey(partitionKey)
                .data(SdkBytes.fromUtf8String(json))
                .build();

        try {
            PutRecordResponse response = kinesisClient.putRecord(request);
            LOG.debug("Alert emitted to {}: alertId={}, deviceGroupId={}, shardId={}, sequenceNumber={}",
                    streamName, value.getAlertId(), value.getDeviceGroupId(),
                    response.shardId(), response.sequenceNumber());
        } catch (Exception e) {
            LOG.error("Failed to emit alert to {}: alertId={}, deviceGroupId={}, error={}",
                    streamName, value.getAlertId(), value.getDeviceGroupId(), e.getMessage(), e);
            throw e;
        }
    }

    /**
     * Serializes an AlertRecord to JSON string.
     *
     * @param alert the alert record to serialize
     * @return JSON string representation of the alert
     * @throws JsonProcessingException if serialization fails
     */
    private String serializeAlert(AlertRecord alert) throws JsonProcessingException {
        return objectMapper.writeValueAsString(alert);
    }
}
