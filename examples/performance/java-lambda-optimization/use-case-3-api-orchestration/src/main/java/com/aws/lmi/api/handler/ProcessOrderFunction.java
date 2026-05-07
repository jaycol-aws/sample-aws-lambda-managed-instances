package com.aws.lmi.api.handler;

import java.lang.management.CompilationMXBean;
import java.lang.management.GarbageCollectorMXBean;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.lang.management.MemoryUsage;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Function;

import com.amazonaws.xray.AWSXRay;
import com.amazonaws.xray.entities.Subsegment;
import com.aws.lmi.api.model.*;
import com.aws.lmi.api.model.Schemas;
import tools.jackson.databind.ObjectMapper;
import org.crac.Context;
import org.crac.Core;
import org.crac.Resource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.enhanced.dynamodb.DynamoDbEnhancedClient;
import software.amazon.awssdk.enhanced.dynamodb.DynamoDbTable;
import software.amazon.awssdk.enhanced.dynamodb.Key;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.sns.SnsClient;
import software.amazon.awssdk.services.sns.model.PublishRequest;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.SendMessageRequest;

@Configuration
public class ProcessOrderFunction implements Resource {

    private static final Logger log = LoggerFactory.getLogger(ProcessOrderFunction.class);
    private static final AtomicBoolean COLD_START = new AtomicBoolean(true);
    private static final long INIT_TIME = System.currentTimeMillis();
    private static final MemoryMXBean MEM = ManagementFactory.getMemoryMXBean();
    private static final CompilationMXBean COMP = ManagementFactory.getCompilationMXBean();
    // Dedicated thread pool for fan-out I/O — avoids ForkJoinPool contention
    // under LMI multi-concurrency where many invocations share the same JVM.
    private static final ExecutorService FANOUT_EXECUTOR = Executors.newCachedThreadPool();

    @SuppressWarnings("unused")
    private final Resource self = this;

    public ProcessOrderFunction() {
        Core.getGlobalContext().register(this);
    }

    @Override
    public void beforeCheckpoint(Context<? extends Resource> context) throws Exception {
        log.info("SnapStart priming: exercising full order processing code path");
        // 1. Jackson serialization classes
        Order o = new Order(); o.setOrderId("PRIME"); o.setCustomerId("PRIME");
        o.setProductId("PRIME"); o.setQuantity(1); o.setUnitPrice(0.0);
        o.setStatus("PRIME"); o.setCreatedAt(Instant.now().toString());
        Product p = new Product(); p.setProductId("PRIME"); p.setPrice(0.0); p.setStockQuantity(0);
        new OrderRequest("PRIME", "PRIME", 1);
        new OrderResponse("PRIME", "PRIME", 0, false, false, 0);
        ObjectMapper mapper = new ObjectMapper();
        try { mapper.writeValueAsString(o); mapper.readValue("{}", Order.class); } catch (Exception ignored) {}

        // 2. DynamoDB Enhanced Client schema resolution — triggers StaticTableSchema introspection
        try {
            DynamoDbClient rawClient = DynamoDbClient.create();
            DynamoDbEnhancedClient ec = DynamoDbEnhancedClient.builder().dynamoDbClient(rawClient).build();
            DynamoDbTable<Order> ot = ec.table("__prime__", Schemas.ORDER_SCHEMA);
            DynamoDbTable<Product> pt = ec.table("__prime__", Schemas.PRODUCT_SCHEMA);
            // Force schema marshalling classes to load (will fail on table not found — that's fine)
            try { ot.getItem(Key.builder().partitionValue("PRIME").build()); } catch (Exception ignored) {}
            try { pt.getItem(Key.builder().partitionValue("PRIME").build()); } catch (Exception ignored) {}
            rawClient.close();
        } catch (Exception ignored) {}

        // 3. SQS/SNS client initialization — triggers HTTP client pool + TLS setup
        try {
            SqsClient sqs = SqsClient.create();
            sqs.listQueues(b -> b.maxResults(1));
            sqs.close();
        } catch (Exception ignored) {}
        try {
            SnsClient sns = SnsClient.create();
            sns.listTopics(b -> b.build());
            sns.close();
        } catch (Exception ignored) {}

        // 4. Thread pool warm-up — pre-create threads in the cached pool
        CompletableFuture.allOf(
            CompletableFuture.runAsync(() -> {}, FANOUT_EXECUTOR),
            CompletableFuture.runAsync(() -> {}, FANOUT_EXECUTOR)
        ).join();

        log.info("SnapStart priming complete — Jackson, DynamoDB schema, SQS, SNS, thread pool all warmed");
    }

    @Override
    public void afterRestore(Context<? extends Resource> context) throws Exception {
        COLD_START.set(true);
    }

    @Bean
    public Function<OrderRequest, OrderResponse> orderProcessorFunction(
            DynamoDbEnhancedClient enhancedClient,
            SqsClient sqsClient,
            SnsClient snsClient,
            ObjectMapper objectMapper) {

        String orderTableName = env("ORDER_TABLE_NAME", "Orders");
        String productTableName = env("PRODUCT_TABLE_NAME", "Products");
        String queueUrl = System.getenv("NOTIFICATION_QUEUE_URL");
        String topicArn = System.getenv("ALERT_TOPIC_ARN");

        DynamoDbTable<Order> orderTable = enhancedClient.table(orderTableName, Schemas.ORDER_SCHEMA);
        DynamoDbTable<Product> productTable = enhancedClient.table(productTableName, Schemas.PRODUCT_SCHEMA);

        return request -> {
            long start = System.nanoTime();
            boolean isColdStart = COLD_START.compareAndSet(true, false);
            String orderId = UUID.randomUUID().toString();

            // 1. Lookup product (traced — shows DynamoDB read latency)
            Product product = traced("DynamoDB-GetProduct", () ->
                    productTable.getItem(Key.builder().partitionValue(request.getProductId()).build()));
            if (product == null) {
                return new OrderResponse(orderId, "REJECTED", 0, false, false, elapsed(start));
            }

            // 2. Reserve inventory (traced — conditional write)
            boolean reserved = traced("DynamoDB-ReserveInventory", () -> {
                if (product.getStockQuantity() >= request.getQuantity()) {
                    product.setStockQuantity(product.getStockQuantity() - request.getQuantity());
                    productTable.putItem(product);
                    return true;
                }
                return false;
            });

            // 3. Save order (traced)
            Order order = new Order();
            order.setCustomerId(request.getCustomerId());
            order.setOrderId(orderId);
            order.setProductId(request.getProductId());
            order.setQuantity(request.getQuantity());
            order.setUnitPrice(product.getPrice());
            order.setStatus(reserved ? "CONFIRMED" : "BACKORDERED");
            order.setCreatedAt(Instant.now().toString());
            tracedVoid("DynamoDB-SaveOrder", () -> orderTable.putItem(order));

            double totalPrice = product.getPrice() * request.getQuantity();

            // 4. Fan-out: SQS + SNS in parallel (traced as single subsegment)
            boolean notified = traced("Fanout-SQS-SNS", () -> {
                CompletableFuture<Void> sqsFuture = CompletableFuture.runAsync(() -> {
                    try {
                        sqsClient.sendMessage(SendMessageRequest.builder()
                                .queueUrl(queueUrl)
                                .messageBody(toJson(objectMapper, order))
                                .build());
                    } catch (Exception e) {
                        log.warn("SQS failed: {}", e.getMessage());
                    }
                }, FANOUT_EXECUTOR);
                CompletableFuture<Void> snsFuture = CompletableFuture.runAsync(() -> {
                    try {
                        snsClient.publish(PublishRequest.builder()
                                .topicArn(topicArn)
                                .subject("Order " + orderId)
                                .message("Order " + orderId + " — " + order.getStatus())
                                .build());
                    } catch (Exception e) {
                        log.warn("SNS failed: {}", e.getMessage());
                    }
                }, FANOUT_EXECUTOR);
                try {
                    CompletableFuture.allOf(sqsFuture, snsFuture).join();
                    return true;
                } catch (Exception e) {
                    return false;
                }
            });

            long durationMs = elapsed(start);
            emitMetrics(durationMs, isColdStart);

            return new OrderResponse(orderId, order.getStatus(), totalPrice, reserved, notified, durationMs);
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

        System.out.println(String.format(
                "{\"_aws\":{\"Timestamp\":%d,\"CloudWatchMetrics\":[{\"Namespace\":\"LMI-Blog/ApiOrchestration\","
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
                Thread.activeCount(), compilationTime));
    }

    private static long elapsed(long startNanos) { return (System.nanoTime() - startNanos) / 1_000_000; }
    private static String env(String k, String d) { String v = System.getenv(k); return v != null ? v : d; }
    private static String toJson(ObjectMapper m, Object o) {
        try { return m.writeValueAsString(o); } catch (Exception e) { return o.toString(); }
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
