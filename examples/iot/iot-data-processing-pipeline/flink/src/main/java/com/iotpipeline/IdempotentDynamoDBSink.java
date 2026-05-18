package com.iotpipeline;

import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashMap;
import java.util.Map;

/**
 * Custom DynamoDB sink that uses conditional PutItem to achieve idempotent writes.
 * <p>
 * Uses {@code ConditionExpression: "attribute_not_exists(deviceGroupId)"} to prevent
 * duplicate records on Flink checkpoint recovery. Includes a {@code writeId} attribute
 * (SHA-256 hash of window key + window timestamp + sensor type) for auditability.
 * <p>
 * Retry logic: up to 3 retries with exponential backoff (100ms, 200ms, 400ms).
 * Failures are logged to CloudWatch after exhausting retries.
 */
public class IdempotentDynamoDBSink implements SinkFunction<AnalyticsResult> {

    private static final long serialVersionUID = 1L;
    private static final Logger LOG = LoggerFactory.getLogger(IdempotentDynamoDBSink.class);

    private static final int MAX_RETRIES = 3;
    private static final long[] BACKOFF_MILLIS = {100L, 200L, 400L};

    private final String tableName;
    private transient DynamoDbClient dynamoDbClient;

    /**
     * Creates an IdempotentDynamoDBSink with the specified table name.
     *
     * @param tableName the DynamoDB table name to write analytics results to
     */
    public IdempotentDynamoDBSink(String tableName) {
        this.tableName = tableName;
    }

    /**
     * Package-private constructor for testing, allowing injection of a DynamoDB client.
     *
     * @param tableName     the DynamoDB table name
     * @param dynamoDbClient the DynamoDB client to use
     */
    IdempotentDynamoDBSink(String tableName, DynamoDbClient dynamoDbClient) {
        this.tableName = tableName;
        this.dynamoDbClient = dynamoDbClient;
    }

    @Override
    public void invoke(AnalyticsResult value, Context context) throws Exception {
        if (dynamoDbClient == null) {
            dynamoDbClient = DynamoDbClient.create();
        }

        Map<String, AttributeValue> item = buildItem(value);
        PutItemRequest request = PutItemRequest.builder()
                .tableName(tableName)
                .item(item)
                .conditionExpression("attribute_not_exists(deviceGroupId)")
                .build();

        putItemWithRetry(request, value);
    }

    /**
     * Attempts to put an item into DynamoDB with retry logic.
     * On ConditionalCheckFailedException, logs at INFO level (idempotent duplicate).
     * On other errors, retries up to MAX_RETRIES times with exponential backoff.
     */
    private void putItemWithRetry(PutItemRequest request, AnalyticsResult value) {
        for (int attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            try {
                dynamoDbClient.putItem(request);
                LOG.debug("Successfully wrote analytics result: deviceGroupId={}, windowEndTimestamp={}, sensorType={}",
                        value.getDeviceGroupId(), value.getWindowEndTimestamp(), value.getSensorType());
                return;
            } catch (ConditionalCheckFailedException e) {
                // Record already exists — this is expected on checkpoint recovery replay
                LOG.info("Idempotent write skipped (record already exists): deviceGroupId={}, windowEndTimestamp={}, sensorType={}",
                        value.getDeviceGroupId(), value.getWindowEndTimestamp(), value.getSensorType());
                return;
            } catch (Exception e) {
                if (attempt < MAX_RETRIES) {
                    LOG.warn("DynamoDB write failed (attempt {}/{}), retrying: deviceGroupId={}, windowEndTimestamp={}, sensorType={}, error={}",
                            attempt + 1, MAX_RETRIES, value.getDeviceGroupId(), value.getWindowEndTimestamp(),
                            value.getSensorType(), e.getMessage());
                    try {
                        Thread.sleep(BACKOFF_MILLIS[attempt]);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        LOG.error("Retry interrupted for DynamoDB write: deviceGroupId={}, windowEndTimestamp={}, sensorType={}",
                                value.getDeviceGroupId(), value.getWindowEndTimestamp(), value.getSensorType());
                        return;
                    }
                } else {
                    // Exhausted all retries — log error for CloudWatch
                    LOG.error("DynamoDB write failed after {} retries: deviceGroupId={}, windowEndTimestamp={}, sensorType={}, error={}",
                            MAX_RETRIES, value.getDeviceGroupId(), value.getWindowEndTimestamp(),
                            value.getSensorType(), e.getMessage(), e);
                }
            }
        }
    }

    /**
     * Builds a DynamoDB item map from an AnalyticsResult, including the writeId for auditability.
     */
    private Map<String, AttributeValue> buildItem(AnalyticsResult value) {
        Map<String, AttributeValue> item = new HashMap<>();

        // Partition key and sort key
        item.put("deviceGroupId", AttributeValue.builder().s(value.getDeviceGroupId()).build());
        item.put("windowEndTimestamp", AttributeValue.builder().n(String.valueOf(value.getWindowEndTimestamp())).build());

        // Additional attributes
        item.put("windowDurationSeconds", AttributeValue.builder().n(String.valueOf(value.getWindowDurationSeconds())).build());
        item.put("sensorType", AttributeValue.builder().s(value.getSensorType()).build());
        item.put("deviceCount", AttributeValue.builder().n(String.valueOf(value.getDeviceCount())).build());
        item.put("alertsEmitted", AttributeValue.builder().n(String.valueOf(value.getAlertsEmitted())).build());

        // Statistics nested map
        if (value.getStatistics() != null) {
            Map<String, AttributeValue> statsMap = new HashMap<>();
            statsMap.put("average", AttributeValue.builder().n(String.valueOf(value.getStatistics().getAverage())).build());
            statsMap.put("min", AttributeValue.builder().n(String.valueOf(value.getStatistics().getMin())).build());
            statsMap.put("max", AttributeValue.builder().n(String.valueOf(value.getStatistics().getMax())).build());
            statsMap.put("stddev", AttributeValue.builder().n(String.valueOf(value.getStatistics().getStddev())).build());
            statsMap.put("count", AttributeValue.builder().n(String.valueOf(value.getStatistics().getCount())).build());
            item.put("statistics", AttributeValue.builder().m(statsMap).build());
        }

        // writeId for auditability — SHA-256 hash of {deviceGroupId}:{windowEndTimestamp}:{sensorType}
        String writeId = computeWriteId(value.getDeviceGroupId(), value.getWindowEndTimestamp(), value.getSensorType());
        item.put("writeId", AttributeValue.builder().s(writeId).build());

        return item;
    }

    /**
     * Computes a SHA-256 hash of the composite key for auditability.
     *
     * @param deviceGroupId      the device group identifier
     * @param windowEndTimestamp  the window end timestamp (epoch millis)
     * @param sensorType         the sensor type
     * @return hex-encoded SHA-256 hash string
     */
    static String computeWriteId(String deviceGroupId, long windowEndTimestamp, String sensorType) {
        String input = deviceGroupId + ":" + windowEndTimestamp + ":" + sensorType;
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(input.getBytes(StandardCharsets.UTF_8));
            StringBuilder hexString = new StringBuilder();
            for (byte b : hash) {
                String hex = Integer.toHexString(0xff & b);
                if (hex.length() == 1) {
                    hexString.append('0');
                }
                hexString.append(hex);
            }
            return hexString.toString();
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 is guaranteed to be available in all JVM implementations
            throw new RuntimeException("SHA-256 algorithm not available", e);
        }
    }
}
