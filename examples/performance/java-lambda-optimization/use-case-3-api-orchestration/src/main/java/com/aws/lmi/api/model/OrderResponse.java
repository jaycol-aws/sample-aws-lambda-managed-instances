package com.aws.lmi.api.model;

public class OrderResponse {

    private String orderId;
    private String status;
    private double totalPrice;
    private boolean inventoryReserved;
    private boolean notificationSent;
    private long processingTimeMs;

    public OrderResponse() {}

    public OrderResponse(String orderId, String status, double totalPrice,
                         boolean inventoryReserved, boolean notificationSent, long processingTimeMs) {
        this.orderId = orderId;
        this.status = status;
        this.totalPrice = totalPrice;
        this.inventoryReserved = inventoryReserved;
        this.notificationSent = notificationSent;
        this.processingTimeMs = processingTimeMs;
    }

    public String getOrderId() { return orderId; }
    public void setOrderId(String orderId) { this.orderId = orderId; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public double getTotalPrice() { return totalPrice; }
    public void setTotalPrice(double totalPrice) { this.totalPrice = totalPrice; }
    public boolean isInventoryReserved() { return inventoryReserved; }
    public void setInventoryReserved(boolean inventoryReserved) { this.inventoryReserved = inventoryReserved; }
    public boolean isNotificationSent() { return notificationSent; }
    public void setNotificationSent(boolean notificationSent) { this.notificationSent = notificationSent; }
    public long getProcessingTimeMs() { return processingTimeMs; }
    public void setProcessingTimeMs(long processingTimeMs) { this.processingTimeMs = processingTimeMs; }
}
