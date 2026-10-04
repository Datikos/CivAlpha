package com.civalpha.forecast;

import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.databind.json.JsonMapper;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Client of the Python ML service (feature generation, training, evaluation). */
@Component
public class MlClient {

    private static final ParameterizedTypeReference<Map<String, Object>> MAP = new ParameterizedTypeReference<>() {};
    private final RestClient rest;

    public MlClient(RestClient mlRestClient) {
        this.rest = mlRestClient;
    }

    public Map<String, Object> generateDemo(String outDir) {
        return post("/demo/generate", Map.of("out_dir", outDir));
    }

    public Map<String, Object> evaluate() {
        return post("/evaluate", Map.of());
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> forecasts(String asOf, List<String> asOfDates, List<Long> companyIds) {
        Map<String, Object> body = new HashMap<>();
        if (asOf != null) body.put("as_of", asOf);
        if (asOfDates != null) body.put("as_of_dates", asOfDates);
        if (companyIds != null) body.put("company_ids", companyIds);
        return (List<Map<String, Object>>) post("/forecasts", body).get("forecasts");
    }

    public Map<String, Object> backtestStrategies() {
        return post("/strategies/backtest", Map.of());
    }

    /** Today's AI action per company (as_of null = latest trading day); the caller persists them. */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> decide(String asOf) {
        Map<String, Object> body = new HashMap<>();
        if (asOf != null) body.put("as_of", asOf);
        return (List<Map<String, Object>>) post("/strategies/decide", body).get("decisions");
    }

    /** Forecasts as of a past close with only the data known then, compared with what followed; stored by the ML service. */
    public Map<String, Object> timeMachine(String asOfDate) {
        return post("/timemachine", Map.of("as_of_date", asOfDate));
    }

    public Map<String, Object> resolveOutcomes() {
        return post("/outcomes/resolve", Map.of());
    }

    public boolean healthy() {
        try {
            return "ok".equals(rest.get().uri("/health").retrieve().body(MAP).get("status"));
        } catch (RuntimeException e) {
            return false;
        }
    }

    private Map<String, Object> post(String path, Object body) {
        try {
            return rest.post().uri(path).contentType(MediaType.APPLICATION_JSON).body(body).retrieve().body(MAP);
        } catch (RestClientResponseException e) {
            throw new IllegalStateException(detail(e), e);
        }
    }

    /** The ML service reports problems as {"detail": "..."}; surface that text instead of the HTTP exception. */
    static String detail(RestClientResponseException e) {
        try {
            Object d = JsonMapper.builder().build().readValue(e.getResponseBodyAsString(), Map.class).get("detail");
            if (d instanceof String s && !s.isBlank()) return s;
        } catch (RuntimeException ignored) {
            // not JSON: fall through
        }
        return "ML service error " + e.getStatusCode().value() + ": " + e.getResponseBodyAsString();
    }
}
