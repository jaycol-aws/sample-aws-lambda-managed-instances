package com.aws.lmi.pdf.model;

/**
 * Request payload for PDF generation.
 * POJO with no-arg constructor required for Jackson deserialization
 * via Spring Cloud Function adapter on Lambda.
 */
public class PdfRequest {

    private String accountId;
    private String startDate;
    private String endDate;

    public PdfRequest() {}

    public PdfRequest(String accountId, String startDate, String endDate) {
        this.accountId = accountId;
        this.startDate = startDate;
        this.endDate = endDate;
    }

    public String getAccountId() { return accountId; }
    public void setAccountId(String accountId) { this.accountId = accountId; }
    public String getStartDate() { return startDate; }
    public void setStartDate(String startDate) { this.startDate = startDate; }
    public String getEndDate() { return endDate; }
    public void setEndDate(String endDate) { this.endDate = endDate; }
}
