package com.aws.lmi.pdf.model;

/**
 * Response payload after PDF generation.
 */
public class PdfResponse {

    private String s3Key;
    private int transactionCount;
    private long generationTimeMs;

    public PdfResponse() {}

    public PdfResponse(String s3Key, int transactionCount, long generationTimeMs) {
        this.s3Key = s3Key;
        this.transactionCount = transactionCount;
        this.generationTimeMs = generationTimeMs;
    }

    public String getS3Key() { return s3Key; }
    public void setS3Key(String s3Key) { this.s3Key = s3Key; }
    public int getTransactionCount() { return transactionCount; }
    public void setTransactionCount(int transactionCount) { this.transactionCount = transactionCount; }
    public long getGenerationTimeMs() { return generationTimeMs; }
    public void setGenerationTimeMs(long generationTimeMs) { this.generationTimeMs = generationTimeMs; }
}
