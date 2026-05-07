package com.aws.lmi.etl.handler;

import java.lang.management.CompilationMXBean;
import java.lang.management.GarbageCollectorMXBean;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.lang.management.MemoryUsage;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Function;

import com.amazonaws.xray.AWSXRay;
import com.amazonaws.xray.entities.Subsegment;
import com.aws.lmi.etl.model.AggregationRequest;
import com.aws.lmi.etl.model.AggregationResponse;
import com.aws.lmi.etl.model.AggregationResponse.MetricSummary;
import com.aws.lmi.etl.model.SensorReading;
import com.aws.lmi.etl.model.Schemas;
import com.aws.lmi.etl.service.AggregationEngine;
import com.aws.lmi.etl.service.CsvExportService;
import org.crac.Context;
import org.crac.Core;
import org.crac.Resource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.enhanced.dynamodb.DynamoDbEnhancedClient;
import software.amazon.awssdk.enhanced.dynamodb.DynamoDbTable;
import software.amazon.awssdk.enhanced.dynamodb.Key;
import software.amazon.awssdk.enhanced.dynamodb.model.QueryConditional;
import software.amazon.awssdk.enhanced.dynamodb.model.QueryEnhancedRequest;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;

@Configuration
public class AggregateDataFunction implements Resource {

    private static final Logger log = LoggerFactory.getLogger(AggregateDataFunction.class);
    private static final AtomicBoolean COLD_START = new AtomicBoolean(true);
    private static final long INIT_TIME = System.currentTimeMillis();
    private static final MemoryMXBean MEM = ManagementFactory.getMemoryMXBean();
    private static final CompilationMXBean COMP = ManagementFactory.getCompilationMXBean();

    @SuppressWarnings("unused")
    private final Resource self = this;

    public AggregateDataFunction() {
        Core.getGlobalContext().register(this);
    }

    @Override
    public void beforeCheckpoint(Context<? extends Resource> context) throws Exception {
        log.info("SnapStart priming: exercising aggregation code paths");
        // Prime: Jackson POJOs, aggregation engine, CSV writer, DynamoDB schema
        new AggregationRequest("PRIME", "2026-01-01", "2026-01-01");
        new SensorReading();
        new AggregationEngine().aggregate(java.util.Collections.emptyList());
        new CsvExportService().exportRawData(java.util.Collections.emptyList());
        log.info("SnapStart priming complete");
    }

    @Override
    public void afterRestore(Context<? extends Resource> context) throws Exception {
        COLD_START.set(true);
    }

    @Bean
    public Function<AggregationRequest, AggregationResponse> dataAggregatorFunction(
            DynamoDbEnhancedClient enhancedClient,
            AggregationEngine engine,
            CsvExportService csvService,
            S3Client s3Client) {

        String tableName = System.getenv("SENSOR_TABLE_NAME");
        String bucketName = System.getenv("OUTPUT_BUCKET_NAME");
        DynamoDbTable<SensorReading> table = enhancedClient.table(
                tableName != null ? tableName : "SensorReadings",
                Schemas.SENSOR_SCHEMA);

        return request -> {
            long start = System.nanoTime();
            boolean isColdStart = COLD_START.compareAndSet(true, false);

            // 1. Query all readings (traced)
            List<SensorReading> readings = traced("DynamoDB-Query", () ->
                    table.query(QueryEnhancedRequest.builder()
                                    .queryConditional(QueryConditional.sortBetween(
                                            Key.builder().partitionValue(request.getDeviceId()).sortValue(request.getStartTime()).build(),
                                            Key.builder().partitionValue(request.getDeviceId()).sortValue(request.getEndTime() + "~").build()))
                                    .build())
                            .items().stream().toList());

            log.info("Loaded {} readings for device {}", readings.size(), request.getDeviceId());

            // 2. Aggregate — memory-intensive (traced)
            Map<String, MetricSummary> metrics = traced("Aggregation", () -> engine.aggregate(readings));

            // 3. Export CSV (traced)
            String rawCsv = traced("CSV-Export", () -> csvService.exportRawData(readings));
            String summaryCsv = csvService.exportSummary(request.getDeviceId(), metrics);

            // 4. Upload to S3 (traced)
            String s3Key = String.format("aggregations/%s/%s-%s.csv",
                    request.getDeviceId(), request.getStartTime(), request.getEndTime());
            tracedVoid("S3-Upload-Raw", () ->
                    s3Client.putObject(
                            PutObjectRequest.builder().bucket(bucketName).key(s3Key).contentType("text/csv").build(),
                            RequestBody.fromString(rawCsv, StandardCharsets.UTF_8)));

            String summaryKey = s3Key.replace(".csv", "-summary.csv");
            tracedVoid("S3-Upload-Summary", () ->
                    s3Client.putObject(
                            PutObjectRequest.builder().bucket(bucketName).key(summaryKey).contentType("text/csv").build(),
                            RequestBody.fromString(summaryCsv, StandardCharsets.UTF_8)));

            long durationMs = (System.nanoTime() - start) / 1_000_000;
            emitMetrics(durationMs, isColdStart, readings.size());

            return new AggregationResponse(request.getDeviceId(), readings.size(), metrics, s3Key, durationMs);
        };
    }

    private void emitMetrics(long handlerDurationMs, boolean isColdStart, int recordCount) {
        MemoryUsage heap = MEM.getHeapMemoryUsage();
        MemoryUsage nonHeap = MEM.getNonHeapMemoryUsage();
        long coldStartDuration = isColdStart ? System.currentTimeMillis() - INIT_TIME : 0;
        long gcCount = 0, gcTime = 0;
        for (GarbageCollectorMXBean gc : ManagementFactory.getGarbageCollectorMXBeans()) {
            gcCount += gc.getCollectionCount();
            gcTime += gc.getCollectionTime();
        }
        long compilationTime = COMP != null ? COMP.getTotalCompilationTime() : -1;

        System.out.println(String.format(
                "{\"_aws\":{\"Timestamp\":%d,\"CloudWatchMetrics\":[{\"Namespace\":\"LMI-Blog/DataAggregation\","
                + "\"Dimensions\":[[\"FunctionName\",\"ColdStart\"]],"
                + "\"Metrics\":["
                + "{\"Name\":\"HandlerDuration\",\"Unit\":\"Milliseconds\"},"
                + "{\"Name\":\"ColdStartDuration\",\"Unit\":\"Milliseconds\"},"
                + "{\"Name\":\"HeapUsed\",\"Unit\":\"Bytes\"},"
                + "{\"Name\":\"HeapCommitted\",\"Unit\":\"Bytes\"},"
                + "{\"Name\":\"HeapMax\",\"Unit\":\"Bytes\"},"
                + "{\"Name\":\"MetaspaceUsed\",\"Unit\":\"Bytes\"},"
                + "{\"Name\":\"GcCount\",\"Unit\":\"Count\"},"
                + "{\"Name\":\"GcTime\",\"Unit\":\"Milliseconds\"},"
                + "{\"Name\":\"ActiveThreads\",\"Unit\":\"Count\"},"
                + "{\"Name\":\"CompilationTime\",\"Unit\":\"Milliseconds\"},"
                + "{\"Name\":\"RecordCount\",\"Unit\":\"Count\"}"
                + "]}]},"
                + "\"FunctionName\":\"%s\",\"ColdStart\":\"%s\","
                + "\"HandlerDuration\":%d,\"ColdStartDuration\":%d,"
                + "\"HeapUsed\":%d,\"HeapCommitted\":%d,\"HeapMax\":%d,\"MetaspaceUsed\":%d,"
                + "\"GcCount\":%d,\"GcTime\":%d,"
                + "\"ActiveThreads\":%d,\"CompilationTime\":%d,\"RecordCount\":%d}",
                System.currentTimeMillis(),
                System.getenv("AWS_LAMBDA_FUNCTION_NAME"), isColdStart,
                handlerDurationMs, coldStartDuration,
                heap.getUsed(), heap.getCommitted(), heap.getMax(), nonHeap.getUsed(),
                gcCount, gcTime,
                Thread.activeCount(), compilationTime, recordCount));
    }

    private static <T> T traced(String name, java.util.concurrent.Callable<T> work) {
        Object sub = null;
        try { sub = AWSXRay.beginSubsegment(name); }
        catch (Exception | NoClassDefFoundError ignored) {}
        try { return work.call(); }
        catch (Exception e) {
            if (sub != null) { try { ((Subsegment) sub).addException(e); } catch (Exception | NoClassDefFoundError ignored) {} }
            throw (e instanceof RuntimeException re) ? re : new RuntimeException(e);
        } finally {
            if (sub != null) { try { AWSXRay.endSubsegment(); } catch (Exception | NoClassDefFoundError ignored) {} }
        }
    }

    private static void tracedVoid(String name, Runnable work) {
        Object sub = null;
        try { sub = AWSXRay.beginSubsegment(name); }
        catch (Exception | NoClassDefFoundError ignored) {}
        try { work.run(); }
        catch (Exception e) {
            if (sub != null) { try { ((Subsegment) sub).addException(e); } catch (Exception | NoClassDefFoundError ignored) {} }
            throw (e instanceof RuntimeException re) ? re : new RuntimeException(e);
        } finally {
            if (sub != null) { try { AWSXRay.endSubsegment(); } catch (Exception | NoClassDefFoundError ignored) {} }
        }
    }
}
