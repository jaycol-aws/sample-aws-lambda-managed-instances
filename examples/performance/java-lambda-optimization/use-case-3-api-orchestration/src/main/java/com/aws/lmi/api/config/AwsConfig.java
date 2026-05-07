package com.aws.lmi.api.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.enhanced.dynamodb.DynamoDbEnhancedClient;
import software.amazon.awssdk.http.apache.ApacheHttpClient;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.sns.SnsClient;
import software.amazon.awssdk.services.sqs.SqsClient;

import java.time.Duration;

/**
 * AWS SDK clients configured with Apache HTTP connection pooling.
 * Connection pool reuse is a key LMI advantage — on standard Lambda,
 * pools are created per cold start and underutilized. On LMI, the
 * long-lived instance keeps pools warm across thousands of invocations.
 */
@Configuration
public class AwsConfig {

    private static final ApacheHttpClient.Builder POOLED_HTTP = ApacheHttpClient.builder()
            .maxConnections(50)
            .connectionTimeout(Duration.ofSeconds(2))
            .socketTimeout(Duration.ofSeconds(5))
            .connectionAcquisitionTimeout(Duration.ofSeconds(3));

    @Bean
    public DynamoDbClient dynamoDbClient() {
        return DynamoDbClient.builder().httpClientBuilder(POOLED_HTTP).build();
    }

    @Bean
    public DynamoDbEnhancedClient dynamoDbEnhancedClient(DynamoDbClient client) {
        return DynamoDbEnhancedClient.builder().dynamoDbClient(client).build();
    }

    @Bean
    public SqsClient sqsClient() {
        return SqsClient.builder().httpClientBuilder(POOLED_HTTP).build();
    }

    @Bean
    public SnsClient snsClient() {
        return SnsClient.builder().httpClientBuilder(POOLED_HTTP).build();
    }
}
