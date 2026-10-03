package com.civalpha.api;

import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
public class ForecastController {

    private static final String SUMMARY = """
            SELECT f.id, f.company_id, f.symbol, c.name AS company_name, f.benchmark_symbol, f.model_kind, f.probability, f.prob_low,
                   f.prob_high, f.horizon_trading_days, f.as_of_date, f.as_of, f.issued_at, f.issue_mode, f.version, f.supersedes_id,
                   f.reason, f.is_demo,
                   o.window_end_date, o.stock_return, o.benchmark_return, o.excess_return, o.outcome, o.brier
            FROM forecast f JOIN company c ON c.id = f.company_id LEFT JOIN forecast_outcome o ON o.forecast_id = f.id""";

    private final JdbcClient jdbc;
    private final Rows rows;

    public ForecastController(JdbcClient jdbc, Rows rows) {
        this.jdbc = jdbc;
        this.rows = rows;
    }

    private Map<String, Object> summary(Map<String, Object> r) {
        Map<String, Object> m = rows.camel(r);
        Object end = m.remove("windowEndDate");
        Map<String, Object> outcome = new LinkedHashMap<>();
        for (String k : List.of("stockReturn", "benchmarkReturn", "excessReturn", "outcome", "brier")) outcome.put(k, m.remove(k));
        outcome.put("windowEndDate", end);
        m.put("outcome", end == null ? null : outcome);
        return m;
    }

    @GetMapping("/api/forecasts/current")
    public List<Map<String, Object>> current() {
        return jdbc.sql("""
                WITH latest_date AS (SELECT max(as_of_date) AS d FROM forecast),
                     latest AS (SELECT DISTINCT ON (company_id, model_kind) id FROM forecast, latest_date
                                WHERE as_of_date = latest_date.d ORDER BY company_id, model_kind, version DESC)
                """ + SUMMARY + " WHERE f.id IN (SELECT id FROM latest) ORDER BY f.symbol, f.model_kind")
                .query().listOfRows().stream().map(this::summary).toList();
    }

    @GetMapping("/api/forecasts/history")
    public List<Map<String, Object>> history(@RequestParam(required = false) String symbol, @RequestParam(required = false) String modelKind) {
        Long companyId = symbol == null || symbol.isBlank() ? null : jdbc.sql("SELECT company_id FROM ticker_history WHERE upper(symbol) = upper(:s) ORDER BY valid_from DESC LIMIT 1")
                .param("s", symbol).query(Long.class).optional().orElse(-1L);
        return jdbc.sql(SUMMARY + """
                 WHERE (CAST(:c AS bigint) IS NULL OR f.company_id = :c) AND (CAST(:k AS text) IS NULL OR f.model_kind = :k)
                ORDER BY f.as_of_date DESC, f.symbol, f.model_kind, f.version DESC LIMIT 2000""")
                .param("c", companyId).param("k", modelKind == null || modelKind.isBlank() ? null : modelKind)
                .query().listOfRows().stream().map(this::summary).toList();
    }

    @GetMapping("/api/forecasts/{id}")
    public Map<String, Object> get(@PathVariable long id) {
        Map<String, Object> r = jdbc.sql(SUMMARY + " WHERE f.id = :id").param("id", id).query().listOfRows().stream().findFirst()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
        Map<String, Object> m = summary(r);
        Map<String, Object> d = jdbc.sql("SELECT target, uncertainty_note, features, explanation, sources, content_sha256, model_version_id, series_key FROM forecast WHERE id = :id")
                .param("id", id).query().singleRow();
        m.putAll(rows.camel(d));
        String series = (String) m.remove("seriesKey");
        Object mv = m.remove("modelVersionId");
        m.put("modelVersion", rows.camel(jdbc.sql("SELECT id, model_kind, algorithm, trained_through, training_cutoff, n_samples, feature_names, code_version FROM model_version WHERE id = :id")
                .param("id", mv).query().singleRow()));
        m.put("versions", rows.camel(jdbc.sql("SELECT id, version, issued_at, probability, reason, supersedes_id FROM forecast WHERE series_key = :s ORDER BY version")
                .param("s", series).query().listOfRows()));
        return m;
    }
}
