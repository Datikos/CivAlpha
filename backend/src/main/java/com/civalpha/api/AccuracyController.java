package com.civalpha.api;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

@RestController
public class AccuracyController {

    private final JdbcClient jdbc;
    private final Rows rows;

    public AccuracyController(JdbcClient jdbc, Rows rows) {
        this.jdbc = jdbc;
        this.rows = rows;
    }

    @GetMapping("/api/accuracy")
    public Map<String, Object> accuracy() {
        Map<String, Object> out = new HashMap<>();
        out.put("evaluation", jdbc.sql("""
                SELECT id, run_at, data_cutoff, is_demo, config, metrics, comparison, calibration, trading, folds, verdict
                FROM model_evaluation ORDER BY id DESC LIMIT 1""").query().listOfRows().stream().findFirst().map(rows::camel).orElse(null));
        // realized accuracy of issued forecasts (latest version of each series), split by issue mode
        Map<String, Map<String, Object>> byMode = new LinkedHashMap<>();
        for (Map<String, Object> r : jdbc.sql("""
                WITH latest AS (SELECT DISTINCT ON (series_key) * FROM forecast ORDER BY series_key, version DESC)
                SELECT l.issue_mode, l.model_kind, count(*) AS issued, count(o.forecast_id) AS resolved, avg(o.brier) AS brier,
                       avg(CASE WHEN o.forecast_id IS NULL THEN NULL WHEN (l.probability > 0.5) = o.outcome THEN 1.0 ELSE 0.0 END) AS hit_rate,
                       avg(CASE WHEN o.outcome THEN 1.0 WHEN o.forecast_id IS NOT NULL THEN 0.0 END) AS base_rate
                FROM latest l LEFT JOIN forecast_outcome o ON o.forecast_id = l.id GROUP BY l.issue_mode, l.model_kind""").query().listOfRows()) {
            Map<String, Object> m = rows.camel(r);
            String mode = (String) m.remove("issueMode");
            byMode.computeIfAbsent(mode, k -> new LinkedHashMap<>()).put((String) m.remove("modelKind"), m);
        }
        out.put("issued", byMode.getOrDefault("LIVE", Map.of()));
        out.put("issuedByMode", byMode);
        return out;
    }
}
