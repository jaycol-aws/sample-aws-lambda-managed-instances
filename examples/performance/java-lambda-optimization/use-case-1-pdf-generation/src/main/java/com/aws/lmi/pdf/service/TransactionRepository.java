package com.aws.lmi.pdf.service;

import java.util.List;

import com.aws.lmi.pdf.model.Schemas;
import com.aws.lmi.pdf.model.TransactionRecord;
import org.springframework.stereotype.Service;
import software.amazon.awssdk.enhanced.dynamodb.DynamoDbEnhancedClient;
import software.amazon.awssdk.enhanced.dynamodb.DynamoDbTable;
import software.amazon.awssdk.enhanced.dynamodb.Key;
import software.amazon.awssdk.enhanced.dynamodb.model.QueryConditional;
import software.amazon.awssdk.enhanced.dynamodb.model.QueryEnhancedRequest;

@Service
public class TransactionRepository {

    private final DynamoDbTable<TransactionRecord> table;

    public TransactionRepository(DynamoDbEnhancedClient enhancedClient) {
        String tableName = System.getenv("TRANSACTION_TABLE_NAME");
        this.table = enhancedClient.table(
                tableName != null ? tableName : "Transactions",
                Schemas.TRANSACTION_SCHEMA);
    }

    /**
     * Query all transactions for an account within a date range.
     * Sort key (transactionId) is prefixed with date for range queries.
     */
    public List<TransactionRecord> getTransactions(String accountId, String startDate, String endDate) {
        QueryConditional condition = QueryConditional.sortBetween(
                Key.builder().partitionValue(accountId).sortValue(startDate).build(),
                Key.builder().partitionValue(accountId).sortValue(endDate + "~").build());

        return table.query(QueryEnhancedRequest.builder()
                        .queryConditional(condition)
                        .build())
                .items()
                .stream()
                .toList();
    }
}
