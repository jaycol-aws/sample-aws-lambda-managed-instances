package com.aws.lmi.pdf.model;

import software.amazon.awssdk.enhanced.dynamodb.TableSchema;
import software.amazon.awssdk.enhanced.dynamodb.mapper.StaticAttributeTags;
import software.amazon.awssdk.enhanced.dynamodb.mapper.StaticTableSchema;

public final class Schemas {

    public static final TableSchema<TransactionRecord> TRANSACTION_SCHEMA = StaticTableSchema.builder(TransactionRecord.class)
            .newItemSupplier(TransactionRecord::new)
            .addAttribute(String.class, a -> a.name("accountId").getter(TransactionRecord::getAccountId).setter(TransactionRecord::setAccountId)
                    .tags(StaticAttributeTags.primaryPartitionKey()))
            .addAttribute(String.class, a -> a.name("transactionId").getter(TransactionRecord::getTransactionId).setter(TransactionRecord::setTransactionId)
                    .tags(StaticAttributeTags.primarySortKey()))
            .addAttribute(String.class, a -> a.name("date").getter(TransactionRecord::getDate).setter(TransactionRecord::setDate))
            .addAttribute(String.class, a -> a.name("description").getter(TransactionRecord::getDescription).setter(TransactionRecord::setDescription))
            .addAttribute(String.class, a -> a.name("category").getter(TransactionRecord::getCategory).setter(TransactionRecord::setCategory))
            .addAttribute(Double.class, a -> a.name("amount").getter(TransactionRecord::getAmount).setter(TransactionRecord::setAmount))
            .addAttribute(String.class, a -> a.name("currency").getter(TransactionRecord::getCurrency).setter(TransactionRecord::setCurrency))
            .addAttribute(String.class, a -> a.name("status").getter(TransactionRecord::getStatus).setter(TransactionRecord::setStatus))
            .build();

    private Schemas() {}
}
