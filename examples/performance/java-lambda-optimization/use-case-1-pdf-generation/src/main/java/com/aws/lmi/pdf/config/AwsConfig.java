package com.aws.lmi.pdf.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.enhanced.dynamodb.DynamoDbEnhancedClient;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.s3.S3Client;

@Configuration
public class AwsConfig {

    @Bean
    public DynamoDbClient dynamoDbClient() {
        return DynamoDbClient.create();
    }

    @Bean
    public DynamoDbEnhancedClient dynamoDbEnhancedClient(DynamoDbClient dynamoDbClient) {
        return DynamoDbEnhancedClient.builder().dynamoDbClient(dynamoDbClient).build();
    }

    @Bean
    public S3Client s3Client() {
        return S3Client.create();
    }

    /**
     * X-Ray subsegment tracing — wraps a callable with a named subsegment.
     * Gracefully degrades when X-Ray is not available (SnapStart, GraalVM native,
     * local testing) by catching NoClassDefFoundError and running without tracing.
     */
    public static <T> T traced(String name, java.util.concurrent.Callable<T> work) {
        Object subsegment = null;
        try {
            subsegment = com.amazonaws.xray.AWSXRay.beginSubsegment(name);
        } catch (Exception | NoClassDefFoundError ignored) {
            // X-Ray not available — run without tracing
        }
        try {
            return work.call();
        } catch (Exception e) {
            if (subsegment != null) {
                try { ((com.amazonaws.xray.entities.Subsegment) subsegment).addException(e); }
                catch (Exception | NoClassDefFoundError ignored) {}
            }
            throw (e instanceof RuntimeException re) ? re : new RuntimeException(e);
        } finally {
            if (subsegment != null) {
                try { com.amazonaws.xray.AWSXRay.endSubsegment(); }
                catch (Exception | NoClassDefFoundError ignored) {}
            }
        }
    }

    public static void tracedVoid(String name, Runnable work) {
        Object subsegment = null;
        try {
            subsegment = com.amazonaws.xray.AWSXRay.beginSubsegment(name);
        } catch (Exception | NoClassDefFoundError ignored) {}
        try {
            work.run();
        } catch (Exception e) {
            if (subsegment != null) {
                try { ((com.amazonaws.xray.entities.Subsegment) subsegment).addException(e); }
                catch (Exception | NoClassDefFoundError ignored) {}
            }
            throw (e instanceof RuntimeException re) ? re : new RuntimeException(e);
        } finally {
            if (subsegment != null) {
                try { com.amazonaws.xray.AWSXRay.endSubsegment(); }
                catch (Exception | NoClassDefFoundError ignored) {}
            }
        }
    }
}
