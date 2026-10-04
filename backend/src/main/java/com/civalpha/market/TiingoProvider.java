package com.civalpha.market;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

/**
 * Tiingo end-of-day prices (free API key; check the plan's terms for your use). Returns raw closes with a
 * per-day split factor and cash dividend, which matches how CivAlpha stores prices.
 */
public class TiingoProvider implements PriceProvider {

    static final String BASE = "https://api.tiingo.com/tiingo/daily/";
    private final HttpClient http;
    private final ObjectMapper om;
    private final String token;

    public TiingoProvider(HttpClient http, ObjectMapper om, String token) {
        if (token == null || token.isBlank()) throw new IllegalArgumentException("TIINGO_API_KEY is required for the tiingo price provider");
        this.http = http;
        this.om = om;
        this.token = token;
    }

    @Override
    public String name() {
        return "Tiingo";
    }

    @Override
    public boolean splitAdjusted() {
        return false;
    }

    @Override
    public Series fetch(String symbol, LocalDate from, LocalDate to) throws Exception {
        String base = BASE + PriceProvider.vendorSymbol(symbol).toLowerCase() + "/prices?startDate=" + from + "&endDate=" + to;
        HttpResponse<byte[]> r = http.send(HttpRequest.newBuilder(URI.create(base + "&token=" + URLEncoder.encode(token, StandardCharsets.UTF_8)))
                .timeout(Duration.ofSeconds(30)).header("Content-Type", "application/json").GET().build(), HttpResponse.BodyHandlers.ofByteArray());
        if (r.statusCode() == 404) throw new IllegalArgumentException("unknown symbol at Tiingo: " + symbol);
        if (r.statusCode() != 200) throw new IllegalStateException("Tiingo HTTP " + r.statusCode());
        return parse(om, r.body(), symbol.toUpperCase(), base);
    }

    static Series parse(ObjectMapper om, byte[] body, String symbol, String locator) {
        List<PriceBarRow> bars = new ArrayList<>();
        List<CsvPrices.ActionRow> actions = new ArrayList<>();
        for (JsonNode d : om.readTree(body)) {
            LocalDate date = LocalDate.parse(d.path("date").asString().substring(0, 10));
            bars.add(new PriceBarRow(symbol, date, num(d, "open"), num(d, "high"), num(d, "low"), num(d, "close"),
                    d.path("volume").isNumber() ? d.path("volume").asLong() : null));
            BigDecimal split = num(d, "splitFactor");
            if (split != null && split.compareTo(BigDecimal.ONE) != 0 && split.signum() > 0) {
                actions.add(new CsvPrices.ActionRow(symbol, date, "SPLIT", split.stripTrailingZeros(), null));
            }
            BigDecimal div = num(d, "divCash");
            if (div != null && div.signum() > 0) actions.add(new CsvPrices.ActionRow(symbol, date, "CASH_DIVIDEND", div, null));
        }
        return new Series(bars, actions, body, "application/json", locator);
    }

    private static BigDecimal num(JsonNode d, String f) {
        return d.path(f).isNumber() ? d.path(f).decimalValue() : null;
    }
}
