package com.aws.lmi.etl.service;

import java.io.IOException;
import java.io.StringWriter;
import java.io.UncheckedIOException;
import java.util.List;
import java.util.Map;

import com.aws.lmi.etl.model.AggregationResponse.MetricSummary;
import com.aws.lmi.etl.model.SensorReading;
import com.opencsv.CSVWriter;
import org.springframework.stereotype.Service;

@Service
public class CsvExportService {

    public String exportRawData(List<SensorReading> readings) {
        StringWriter sw = new StringWriter(readings.size() * 100);
        try (CSVWriter writer = new CSVWriter(sw)) {
            writer.writeNext(new String[]{
                    "deviceId", "timestamp", "temperature", "humidity", "pressure", "voltage", "location", "status"});
            for (SensorReading r : readings) {
                writer.writeNext(new String[]{
                        r.getDeviceId(), r.getTimestamp(),
                        String.valueOf(r.getTemperature()), String.valueOf(r.getHumidity()),
                        String.valueOf(r.getPressure()), String.valueOf(r.getVoltage()),
                        r.getLocation(), r.getStatus()});
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return sw.toString();
    }

    public String exportSummary(String deviceId, Map<String, MetricSummary> metrics) {
        StringWriter sw = new StringWriter();
        try (CSVWriter writer = new CSVWriter(sw)) {
            writer.writeNext(new String[]{"metric", "min", "max", "avg", "stdDev", "p95"});
            metrics.forEach((name, s) -> writer.writeNext(new String[]{
                    name, fmt(s.getMin()), fmt(s.getMax()), fmt(s.getAvg()), fmt(s.getStdDev()), fmt(s.getP95())}));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return sw.toString();
    }

    private String fmt(double v) { return String.format("%.4f", v); }
}
