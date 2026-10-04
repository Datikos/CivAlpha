package com.civalpha.market;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** CSV parsing for prices (symbol,date,open,high,low,close,volume) and corporate actions. */
public final class CsvPrices {

    public record ActionRow(String symbol, LocalDate exDate, String type, BigDecimal value, java.time.OffsetDateTime announcedAt) {}

    public static List<PriceBarRow> parseBars(InputStream in) throws IOException {
        List<PriceBarRow> out = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            Map<String, Integer> h = header(r.readLine());
            for (String req : List.of("symbol", "date", "close")) {
                if (!h.containsKey(req)) throw new IllegalArgumentException("price CSV needs column '" + req + "'");
            }
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isBlank()) continue;
                String[] c = line.split(",", -1);
                out.add(new PriceBarRow(c[h.get("symbol")].trim().toUpperCase(), LocalDate.parse(c[h.get("date")].trim()),
                        dec(c, h.get("open")), dec(c, h.get("high")), dec(c, h.get("low")), dec(c, h.get("close")),
                        h.containsKey("volume") && !c[h.get("volume")].isBlank() ? Long.parseLong(c[h.get("volume")].trim()) : null));
            }
        }
        return out;
    }

    public static List<ActionRow> parseActions(InputStream in) throws IOException {
        List<ActionRow> out = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            Map<String, Integer> h = header(r.readLine());
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isBlank()) continue;
                String[] c = line.split(",", -1);
                String ann = h.containsKey("announced_at") ? c[h.get("announced_at")].trim() : "";
                out.add(new ActionRow(c[h.get("symbol")].trim().toUpperCase(), LocalDate.parse(c[h.get("ex_date")].trim()),
                        c[h.get("action_type")].trim(), new BigDecimal(c[h.get("value")].trim()),
                        ann.isEmpty() ? null : java.time.OffsetDateTime.parse(ann)));
            }
        }
        return out;
    }

    private static Map<String, Integer> header(String line) {
        if (line == null) throw new IllegalArgumentException("empty CSV");
        Map<String, Integer> h = new HashMap<>();
        String[] cols = line.replace("﻿", "").split(",");
        for (int i = 0; i < cols.length; i++) h.put(cols[i].trim().toLowerCase(), i);
        return h;
    }

    private static BigDecimal dec(String[] c, Integer i) {
        return i == null || i >= c.length || c[i].isBlank() ? null : new BigDecimal(c[i].trim());
    }

    private CsvPrices() {}
}
