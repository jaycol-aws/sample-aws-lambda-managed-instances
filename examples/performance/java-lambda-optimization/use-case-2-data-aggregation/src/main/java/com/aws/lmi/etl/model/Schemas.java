package com.aws.lmi.etl.model;

import software.amazon.awssdk.enhanced.dynamodb.TableSchema;
import software.amazon.awssdk.enhanced.dynamodb.mapper.StaticAttributeTags;
import software.amazon.awssdk.enhanced.dynamodb.mapper.StaticTableSchema;

/**
 * Static DynamoDB table schema — GraalVM native compatible.
 */
public final class Schemas {

    public static final TableSchema<SensorReading> SENSOR_SCHEMA = StaticTableSchema.builder(SensorReading.class)
            .newItemSupplier(SensorReading::new)
            .addAttribute(String.class, a -> a.name("deviceId").getter(SensorReading::getDeviceId).setter(SensorReading::setDeviceId)
                    .tags(StaticAttributeTags.primaryPartitionKey()))
            .addAttribute(String.class, a -> a.name("timestamp").getter(SensorReading::getTimestamp).setter(SensorReading::setTimestamp)
                    .tags(StaticAttributeTags.primarySortKey()))
            .addAttribute(Double.class, a -> a.name("temperature").getter(SensorReading::getTemperature).setter(SensorReading::setTemperature))
            .addAttribute(Double.class, a -> a.name("humidity").getter(SensorReading::getHumidity).setter(SensorReading::setHumidity))
            .addAttribute(Double.class, a -> a.name("pressure").getter(SensorReading::getPressure).setter(SensorReading::setPressure))
            .addAttribute(Double.class, a -> a.name("voltage").getter(SensorReading::getVoltage).setter(SensorReading::setVoltage))
            .addAttribute(String.class, a -> a.name("location").getter(SensorReading::getLocation).setter(SensorReading::setLocation))
            .addAttribute(String.class, a -> a.name("status").getter(SensorReading::getStatus).setter(SensorReading::setStatus))
            .build();

    private Schemas() {}
}
