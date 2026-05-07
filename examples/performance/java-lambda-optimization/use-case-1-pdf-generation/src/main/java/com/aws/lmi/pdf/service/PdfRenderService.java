package com.aws.lmi.pdf.service;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import com.aws.lmi.pdf.model.TransactionRecord;
import org.springframework.stereotype.Service;

/**
 * CPU-intensive PDF generation using raw PDF operators.
 * No external libraries, no AWT — GraalVM native compatible.
 *
 * Generates valid PDF 1.4 documents with multi-page table layout.
 * The text positioning math and stream encoding are CPU-intensive
 * hot paths that benefit from JIT C2 optimization on LMI.
 */
@Service
public class PdfRenderService {

    private static final float PAGE_W = 595, PAGE_H = 842; // A4
    private static final float MARGIN = 50, ROW_H = 14;
    private static final float HEADER_H = 70;

    public byte[] generateReport(String accountId, String dateRange, List<TransactionRecord> records)
            throws IOException {
        float usable = PAGE_H - 2 * MARGIN - HEADER_H;
        int rowsPerPage = (int) (usable / ROW_H);
        int totalPages = Math.max(1, (int) Math.ceil((double) records.size() / rowsPerPage));

        List<byte[]> pageStreams = new ArrayList<>();
        for (int p = 0; p < totalPages; p++) {
            int start = p * rowsPerPage;
            int end = Math.min(start + rowsPerPage, records.size());
            pageStreams.add(buildPageStream(accountId, dateRange, p + 1, totalPages,
                    records.subList(start, end), p == totalPages - 1 ? records : null));
        }
        return assemblePdf(pageStreams);
    }

    private byte[] buildPageStream(String accountId, String dateRange, int page, int total,
                                    List<TransactionRecord> rows, List<TransactionRecord> allForSummary) {
        StringBuilder s = new StringBuilder(4096);

        // Header
        float y = PAGE_H - MARGIN;
        s.append("BT /F1 14 Tf ").append(MARGIN).append(' ').append(y).append(" Td (Transaction Statement) Tj ET\n");
        y -= 20;
        s.append("BT /F1 9 Tf ").append(MARGIN).append(' ').append(y)
                .append(" Td (Account: ").append(clean(accountId))
                .append("  |  Period: ").append(clean(dateRange))
                .append("  |  Page ").append(page).append(" of ").append(total).append(") Tj ET\n");
        y -= 15;
        // Line
        s.append(MARGIN).append(' ').append(y).append(" m ")
                .append(PAGE_W - MARGIN).append(' ').append(y).append(" l S\n");
        y -= 20;

        // Table header
        s.append("BT /F1 8 Tf ").append(MARGIN).append(' ').append(y)
                .append(" Td (Date          Description                    Category       Amount     Status) Tj ET\n");
        y -= ROW_H;

        // Rows — CPU-intensive string formatting and positioning
        for (TransactionRecord r : rows) {
            String desc = r.getDescription();
            if (desc != null && desc.length() > 26) desc = desc.substring(0, 26) + "..";
            String line = String.format("%-14s%-31s%-15s%10.2f  %-8s",
                    safe(r.getDate()), safe(desc), safe(r.getCategory()), r.getAmount(), safe(r.getStatus()));
            s.append("BT /F1 7 Tf ").append(MARGIN).append(' ').append(y)
                    .append(" Td (").append(clean(line)).append(") Tj ET\n");
            y -= ROW_H;
        }

        // Summary on last page
        if (allForSummary != null) {
            y -= 5;
            s.append(MARGIN).append(' ').append(y).append(" m ")
                    .append(PAGE_W - MARGIN).append(' ').append(y).append(" l S\n");
            y -= 15;
            double total$ = allForSummary.stream().mapToDouble(TransactionRecord::getAmount).sum();
            long credits = allForSummary.stream().filter(r -> r.getAmount() > 0).count();
            long debits = allForSummary.stream().filter(r -> r.getAmount() < 0).count();
            String summary = String.format("Total: %.2f  |  %d transactions (%d credits, %d debits)",
                    total$, allForSummary.size(), credits, debits);
            s.append("BT /F1 9 Tf ").append(MARGIN).append(' ').append(y)
                    .append(" Td (").append(clean(summary)).append(") Tj ET\n");
        }

        return s.toString().getBytes(StandardCharsets.US_ASCII);
    }

    private byte[] assemblePdf(List<byte[]> pageStreams) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream(32768);
        List<Integer> offsets = new ArrayList<>();

        // Header
        write(out, "%PDF-1.4\n");

        // Font (obj 1)
        offsets.add(out.size());
        write(out, "1 0 obj <</Type /Font /Subtype /Type1 /BaseFont /Helvetica>> endobj\n");

        // Pages parent (obj 2) — written after pages
        int pagesObjOffset = -1;

        // Page objects start at obj 3
        int objNum = 3;
        List<Integer> pageObjNums = new ArrayList<>();
        List<Integer> streamObjNums = new ArrayList<>();

        for (byte[] stream : pageStreams) {
            // Stream object
            offsets.add(out.size());
            int streamObj = objNum++;
            streamObjNums.add(streamObj);
            write(out, streamObj + " 0 obj <</Length " + stream.length + ">> stream\n");
            out.write(stream);
            write(out, "\nendstream endobj\n");

            // Page object
            offsets.add(out.size());
            int pageObj = objNum++;
            pageObjNums.add(pageObj);
            write(out, pageObj + " 0 obj <</Type /Page /Parent 2 0 R /MediaBox [0 0 "
                    + (int) PAGE_W + " " + (int) PAGE_H + "] "
                    + "/Contents " + streamObj + " 0 R "
                    + "/Resources <</Font <</F1 1 0 R>>>>"
                    + ">> endobj\n");
        }

        // Pages object (obj 2)
        pagesObjOffset = out.size();
        StringBuilder kids = new StringBuilder("[");
        for (int pn : pageObjNums) kids.append(pn).append(" 0 R ");
        kids.append("]");
        write(out, "2 0 obj <</Type /Pages /Kids " + kids + " /Count " + pageObjNums.size() + ">> endobj\n");

        // Catalog
        offsets.add(out.size());
        int catalogObj = objNum++;
        write(out, catalogObj + " 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n");

        // Xref
        int xrefOffset = out.size();
        write(out, "xref\n0 " + (objNum) + "\n");
        write(out, "0000000000 65535 f \n");
        // obj 1 (font)
        write(out, String.format("%010d 00000 n \n", offsets.get(0)));
        // obj 2 (pages)
        write(out, String.format("%010d 00000 n \n", pagesObjOffset));
        // remaining objects
        for (int i = 1; i < offsets.size(); i++) {
            write(out, String.format("%010d 00000 n \n", offsets.get(i)));
        }

        // Trailer
        write(out, "trailer <</Size " + objNum + " /Root " + catalogObj + " 0 R>>\n");
        write(out, "startxref\n" + xrefOffset + "\n%%EOF\n");

        return out.toByteArray();
    }

    private static void write(ByteArrayOutputStream out, String s) throws IOException {
        out.write(s.getBytes(StandardCharsets.US_ASCII));
    }

    private static String safe(String s) { return s != null ? s : ""; }

    /** Escape PDF special characters */
    private static String clean(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)");
    }
}
