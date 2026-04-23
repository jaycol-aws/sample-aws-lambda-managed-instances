package com.aws.lmi.api.model;

import software.amazon.awssdk.enhanced.dynamodb.TableSchema;
import software.amazon.awssdk.enhanced.dynamodb.mapper.StaticAttributeTags;
import software.amazon.awssdk.enhanced.dynamodb.mapper.StaticTableSchema;

/**
 * Static DynamoDB table schemas — GraalVM native compatible.
 * StaticTableSchema avoids the reflection and hidden class generation
 * that BeanTableSchema.fromBean() uses, which is not supported in
 * GraalVM native image.
 *
 * On JVM (Standard Lambda, SnapStart, LMI), fromBean() works fine.
 * This class provides the same schemas without reflection for native.
 * Both approaches produce identical DynamoDB behavior.
 */
public final class Schemas {

    public static final TableSchema<Order> ORDER_SCHEMA = StaticTableSchema.builder(Order.class)
            .newItemSupplier(Order::new)
            .addAttribute(String.class, a -> a.name("customerId").getter(Order::getCustomerId).setter(Order::setCustomerId)
                    .tags(StaticAttributeTags.primaryPartitionKey()))
            .addAttribute(String.class, a -> a.name("orderId").getter(Order::getOrderId).setter(Order::setOrderId)
                    .tags(StaticAttributeTags.primarySortKey()))
            .addAttribute(String.class, a -> a.name("productId").getter(Order::getProductId).setter(Order::setProductId))
            .addAttribute(Integer.class, a -> a.name("quantity").getter(Order::getQuantity).setter(Order::setQuantity))
            .addAttribute(Double.class, a -> a.name("unitPrice").getter(Order::getUnitPrice).setter(Order::setUnitPrice))
            .addAttribute(String.class, a -> a.name("status").getter(Order::getStatus).setter(Order::setStatus))
            .addAttribute(String.class, a -> a.name("createdAt").getter(Order::getCreatedAt).setter(Order::setCreatedAt))
            .build();

    public static final TableSchema<Product> PRODUCT_SCHEMA = StaticTableSchema.builder(Product.class)
            .newItemSupplier(Product::new)
            .addAttribute(String.class, a -> a.name("productId").getter(Product::getProductId).setter(Product::setProductId)
                    .tags(StaticAttributeTags.primaryPartitionKey()))
            .addAttribute(String.class, a -> a.name("name").getter(Product::getName).setter(Product::setName))
            .addAttribute(Double.class, a -> a.name("price").getter(Product::getPrice).setter(Product::setPrice))
            .addAttribute(Integer.class, a -> a.name("stockQuantity").getter(Product::getStockQuantity).setter(Product::setStockQuantity))
            .build();

    private Schemas() {}
}
