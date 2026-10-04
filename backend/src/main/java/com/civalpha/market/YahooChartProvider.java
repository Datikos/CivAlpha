package com.civalpha.market;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Yahoo Finance chart endpoint. No key, but unofficial: no SLA, and Yahoo's terms restrict automated use and
 * redistribution, so treat it as personal research only. Closes are split-adjusted (not dividend-adjusted);
 * dividends and splits come from the "events" block.
 */
public class YahooChartProvider implements PriceProvider {

    static final String BASE = "https://query1.finance.yahoo.com/v8/finance/chart/";
    private final HttpClient http;
    private final ObjectMapper om;

    public YahooChartProvider(HttpClient http, ObjectMapper om) {
        this.http = http;
        this.om = om;
    }

    @Override
    public String name() {
        return "Yahoo Finance (unofficial chart API)";
    }

    @Override
    public boolean splitAdjusted() {
        return true;
    }

    @Override
    public Series fetch(String symbol, LocalDate from, LocalDate to) throws Exception {
        long p1 = from.atStartOfDay(ZoneOffset.UTC).toEpochSecond();
        long p2 = to.plusDays(1).atStartOfDay(ZoneOffset.UTC).toEpochSecond();
        String url = BASE + PriceProvider.vendorSymbol(symbol) + "?period1=" + p1 + "&period2=" + p2
                + "&interval=1d&events=div%2Csplit&includeAdjustedClose=true";
        HttpResponse<byte[]> r = http.send(HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(30))
                .header("User-Agent", "Mozilla/5.0 (CivAlpha research)").GET().build(), HttpResponse.BodyHandlers.ofByteArray());
        if (r.statusCode() == 404) throw new IllegalArgumentException("unknown symbol at Yahoo: " + symbol);
        if (r.statusCode() == 429) throw new RateLimited("Yahoo is rate-limiting requests");
        if (r.statusCode() != 200) throw new IllegalStateException("Yahoo HTTP " + r.statusCode());
        return parse(om, r.body(), symbol.toUpperCase(), url);
    }

    static Series parse(ObjectMapper om, byte[] body, String symbol, String url) {
        JsonNode root = om.readTree(body).path("chart");
        if (root.path("error").isObject()) throw new IllegalStateException("Yahoo: " + root.path("error").path("description").asString(""));
        JsonNode res = root.path("result").path(0);
        ZoneId zone = zone(res.path("meta"));
        JsonNode ts = res.path("timestamp");
        JsonNode q = res.path("indicators").path("quote").path(0);
        List<PriceBarRow> bars = new ArrayList<>();
        for (int i = 0; i < ts.size(); i++) {
            JsonNode close = q.path("close").path(i);
            if (close.isNull() || close.isMissingNode()) continue; // halted/holiday placeholder
            LocalDate d = Instant.ofEpochSecond(ts.path(i).asLong()).atZone(zone).toLocalDate();
            bars.add(new PriceBarRow(symbol, d, dec(q.path("open").path(i)), dec(q.path("high").path(i)), dec(q.path("low").path(i)),
                    dec(close), q.path("volume").path(i).isNumber() ? q.path("volume").path(i).asLong() : null));
        }
        List<CsvPrices.ActionRow> actions = new ArrayList<>();
        for (Map.Entry<String, JsonNode> e : res.path("events").path("dividends").properties()) {
            JsonNode v = e.getValue();
            actions.add(new CsvPrices.ActionRow(symbol, Instant.ofEpochSecond(v.path("date").asLong()).atZone(zone).toLocalDate(),
                    "CASH_DIVIDEND", v.path("amount").decimalValue(), null));
        }
        for (Map.Entry<String, JsonNode> e : res.path("events").path("splits").properties()) {
            JsonNode v = e.getValue();
            BigDecimal ratio = v.path("numerator").decimalValue().divide(v.path("denominator").decimalValue(), 8, RoundingMode.HALF_UP);
            actions.add(new CsvPrices.ActionRow(symbol, Instant.ofEpochSecond(v.path("date").asLong()).atZone(zone).toLocalDate(),
                    "SPLIT", ratio.stripTrailingZeros(), null));
        }
        return new Series(bars, actions, body, "application/json", url);
    }

    private static ZoneId zone(JsonNode meta) {
        try {
            String tz = meta.path("exchangeTimezoneName").asString("");
            if (!tz.isEmpty()) return ZoneId.of(tz);
        } catch (RuntimeException ignored) {
            // fall through
        }
        return ZoneOffset.ofTotalSeconds(meta.path("gmtoffset").asInt(-14400));
    }

    private static BigDecimal dec(JsonNode n) {
        return n == null || !n.isNumber() ? null : n.decimalValue().setScale(4, RoundingMode.HALF_UP);
    }
}
