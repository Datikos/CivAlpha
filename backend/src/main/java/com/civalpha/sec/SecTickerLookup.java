package com.civalpha.sec;

import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

/**
 * Looks up a ticker's CIK and registrant name in SEC's company_tickers.json (live mode: sec.gov, cached for
 * a day; fixture mode: the local fixture file, which only covers the demo companies).
 */
@Component
public class SecTickerLookup {

    public record Match(String symbol, String cik, String name, String source) {}

    private final SecClientFactory sec;
    private final ObjectMapper om;
    private Map<String, Match> cache = Map.of();
    private Instant loadedAt = Instant.EPOCH;

    public SecTickerLookup(SecClientFactory sec, ObjectMapper om) {
        this.sec = sec;
        this.om = om;
    }

    public synchronized Optional<Match> lookup(String symbol) {
        if (Duration.between(loadedAt, Instant.now()).toHours() >= 24 || cache.isEmpty()) {
            SecClient client = sec.configured();
            cache = parse(om, client.get(SecClient.COMPANY_TICKERS).orElse("{}".getBytes()), client.mode());
            loadedAt = Instant.now();
        }
        return Optional.ofNullable(cache.get(symbol.toUpperCase().trim()));
    }

    static Map<String, Match> parse(ObjectMapper om, byte[] json, String mode) {
        Map<String, Match> out = new HashMap<>();
        for (Map.Entry<String, JsonNode> e : om.readTree(json).properties()) {
            JsonNode n = e.getValue();
            String t = n.path("ticker").asString("").toUpperCase();
            if (t.isEmpty()) continue;
            out.putIfAbsent(t, new Match(t, com.civalpha.universe.TickerResolver.padCik(n.path("cik_str").asString()),
                    n.path("title").asString(""), "SEC company_tickers.json (" + mode + ")"));
        }
        return out;
    }
}
