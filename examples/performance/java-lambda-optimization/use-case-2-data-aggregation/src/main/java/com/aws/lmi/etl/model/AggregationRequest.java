package com.aws.lmi.etl.model;

public class AggregationRequest {

    private String deviceId;
    private String startTime;
    private String endTime;

    public AggregationRequest() {}

    public AggregationRequest(String deviceId, String startTime, String endTime) {
        this.deviceId = deviceId;
        this.startTime = startTime;
        this.endTime = endTime;
    }

    public String getDeviceId() { return deviceId; }
    public void setDeviceId(String deviceId) { this.deviceId = deviceId; }
    public String getStartTime() { return startTime; }
    public void setStartTime(String startTime) { this.startTime = startTime; }
    public String getEndTime() { return endTime; }
    public void setEndTime(String endTime) { this.endTime = endTime; }
}
