package com.aws.lmi.pdf.handler;

import java.lang.management.CompilationMXBean;
import java.lang.management.GarbageCollectorMXBean;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.lang.management.MemoryUsage;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Function;

import com.aws.lmi.pdf.config.AwsConfig;
import com.aws.lmi.pdf.model.PdfRequest;
import com.aws.lmi.pdf.model.PdfResponse;
import com.aws.lmi.pdf.model.TransactionRecord;
import com.aws.lmi.pdf.service.PdfRenderService;
import com.aws.lmi.pdf.service.TransactionRepository;
import org.crac.Context;
import org.crac.Core;
import org.crac.Resource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;

/**
 * Spring Cloud Function handler for PDF generation.
 *
 * Implements CRaC Resource for SnapStart invoke priming — beforeCheckpoint()
 * exercises the full handler code path so class loading, JIT compilation of
 * Jackson marshallers, PDFBox font initialization, and DynamoDB client setup
 * are all captured in the snapshot. This eliminates lazy-loading penalties
 * on the first request after restore.
 */
@Configuration
public class GenerateReportFunction implements Resource {

    private static final Logger log = LoggerFactory.getLogger(GenerateReportFunction.class);
    private static final AtomicBoolean COLD_START = new AtomicBoolean(true);
    private static final long INIT_TIME = System.currentTimeMillis();
    private static final MemoryMXBean MEM = ManagementFactory.getMemoryMXBean();
    private static final CompilationMXBean COMP = ManagementFactory.getCompilationMXBean();

    // Strong reference prevents GC before checkpoint (per AWS docs)
    @SuppressWarnings("unused")
    private final Resource self = this;

    public GenerateReportFunction() {
        Core.getGlobalContext().register(this);
    }

    @Override
    public void beforeCheckpoint(Context<? extends Resource> context) throws Exception {
        log.info("SnapStart priming: exercising handler code paths before snapshot");
        // 1. Jackson deserialization (PdfRequest POJO)
        new PdfRequest("PRIME", "2026-01-01", "2026-01-01");
        new PdfResponse("prime", 0, 0);
        // 2. PDF rendering classes — load all font/rendering code paths
        try {
            new PdfRenderService().generateReport("PRIME", "priming", java.util.Collections.emptyList());
        } catch (Exception e) {
            // Expected — empty list produces minimal PDF, loading all rendering classes
        }
        // 3. DynamoDB Enhanced Client — trigger schema resolution + HTTP client init
        new TransactionRecord();
        try {
            TransactionRepository repo = new TransactionRepository(
                    software.amazon.awssdk.enhanced.dynamodb.DynamoDbEnhancedClient.builder()
                            .dynamoDbClient(software.amazon.awssdk.services.dynamodb.DynamoDbClient.create())
                            .build());
            repo.getTransactions("PRIME", "2026-01-01", "2026-01-01");
        } catch (Exception ignored) {}
        // 4. S3 client — trigger HTTP client pool + TLS setup
        try {
            software.amazon.awssdk.services.s3.S3Client.create()
                    .headBucket(b -> b.bucket("__prime__"));
        } catch (Exception ignored) {}
        log.info("SnapStart priming complete — Jackson, PDF, DynamoDB schema, S3 client all warmed");
    }

    @Override
    public void afterRestore(Context<? extends Resource> context) throws Exception {
        COLD_START.set(true);
        log.info("SnapStart restore complete");
    }

    @Bean
    public Function<PdfRequest, PdfResponse> pdfGeneratorFunction(
            TransactionRepository repository,
            PdfRenderService pdfService,
            S3Client s3Client) {

        String bucketName = System.getenv("REPORT_BUCKET_NAME");

        return request -> {
            long start = System.nanoTime();
            boolean isColdStart = COLD_START.compareAndSet(true, false);

            // 1. Query transactions from DynamoDB (traced)
            List<TransactionRecord> records = AwsConfig.traced("DynamoDB-Query", () ->
                    repository.getTransactions(request.getAccountId(), request.getStartDate(), request.getEndDate()));
            log.info("Queried {} transactions for account {}", records.size(), request.getAccountId());

            // 2. Generate PDF — CPU-intensive (traced)
            byte[] pdfBytes = AwsConfig.traced("PDF-Render", () -> {
                String dateRange = request.getStartDate() + " to " + request.getEndDate();
                return pdfService.generateReport(request.getAccountId(), dateRange, records);
            });

            // 3. Upload to S3 (traced)
            String s3Key = String.format("reports/%s/%s.pdf", request.getAccountId(), UUID.randomUUID());
            AwsConfig.tracedVoid("S3-Upload", () ->
                    s3Client.putObject(
                            PutObjectRequest.builder()
                                    .bucket(bucketName)
                                    .key(s3Key)
                                    .contentType("application/pdf")
                                    .build(),
                            RequestBody.fromBytes(pdfBytes)));

            long durationMs = (System.nanoTime() - start) / 1_000_000;

            // 4. Emit EMF metrics
            emitMetrics(durationMs, isColdStart);

            log.info("Generated PDF ({} bytes, {} transactions) in {}ms [coldStart={}]",
                    pdfBytes.length, records.size(), durationMs, isColdStart);

            return new PdfResponse(s3Key, records.size(), durationMs);
        };
    }

    private void emitMetrics(long handlerDurationMs, boolean isColdStart) {
        MemoryUsage heap = MEM.getHeapMemoryUsage();
        MemoryUsage nonHeap = MEM.getNonHeapMemoryUsage();
        long coldStartDuration = isColdStart ? System.currentTimeMillis() - INIT_TIME : 0;

        long gcCount = 0, gcTime = 0;
        for (GarbageCollectorMXBean gc : ManagementFactory.getGarbageCollectorMXBeans()) {
            gcCount += gc.getCollectionCount();
            gcTime += gc.getCollectionTime();
        }

        long compilationTime = COMP != null ? COMP.getTotalCompilationTime() : -1;
        int activeThreads = Thread.activeCount();

        // EMF structured log — CloudWatch parses this automatically
        System.out.println(String.format(
                "{\"_aws\":{\"Timestamp\":%d,\"CloudWatchMetrics\":[{\"Namespace\":\"LMI-Blog/PdfGeneration\","
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
                + "{\"Name\":\"CompilationTime\",\"Unit\":\"Milliseconds\"}"
                + "]}]},"
                + "\"FunctionName\":\"%s\",\"ColdStart\":\"%s\","
                + "\"HandlerDuration\":%d,\"ColdStartDuration\":%d,"
                + "\"HeapUsed\":%d,\"HeapCommitted\":%d,\"HeapMax\":%d,\"MetaspaceUsed\":%d,"
                + "\"GcCount\":%d,\"GcTime\":%d,"
                + "\"ActiveThreads\":%d,\"CompilationTime\":%d}",
                System.currentTimeMillis(),
                System.getenv("AWS_LAMBDA_FUNCTION_NAME"), isColdStart,
                handlerDurationMs, coldStartDuration,
                heap.getUsed(), heap.getCommitted(), heap.getMax(), nonHeap.getUsed(),
                gcCount, gcTime,
                activeThreads, compilationTime));
    }
}
