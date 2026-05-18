package com.iotpipeline;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.connector.kinesis.source.serialization.KinesisDeserializationSchema;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.kinesis.model.Record;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * Deserializes enriched telemetry records from the Kinesis enriched stream
 * into {@link EnrichedRecord} Flink POJOs.
 *
 * <p>Malformed records are logged as warnings and skipped (not collected),
 * ensuring that bad data does not block window processing.
 */
public class EnrichedRecordDeserializer implements KinesisDeserializationSchema<EnrichedRecord> {

    private static final long serialVersionUID = 1L;
    private static final Logger LOG = LoggerFactory.getLogger(EnrichedRecordDeserializer.class);

    private transient ObjectMapper objectMapper;

    @Override
    public void deserialize(Record record, String stream, String shardId, Collector<EnrichedRecord> collector)
            throws IOException {
        if (objectMapper == null) {
            objectMapper = new ObjectMapper();
        }

        byte[] data = record.data().asByteArray();

        try {
            EnrichedRecord enrichedRecord = objectMapper.readValue(data, EnrichedRecord.class);
            collector.collect(enrichedRecord);
        } catch (Exception e) {
            String rawPayload = new String(data, StandardCharsets.UTF_8);
            LOG.warn("Failed to deserialize enriched record from Kinesis. "
                    + "Stream: {}, Shard: {}, Partition key: {}, Sequence number: {}, Error: {}. "
                    + "Skipping record. Payload: {}",
                    stream,
                    shardId,
                    record.partitionKey(),
                    record.sequenceNumber(),
                    e.getMessage(),
                    rawPayload.length() > 500 ? rawPayload.substring(0, 500) + "..." : rawPayload);
        }
    }

    @Override
    public TypeInformation<EnrichedRecord> getProducedType() {
        return TypeInformation.of(EnrichedRecord.class);
    }
}
