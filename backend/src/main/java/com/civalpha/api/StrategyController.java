package com.civalpha.api;

import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Strategy lab results (latest backtest run) and the AI strategy's daily decisions. */
@RestController
public class StrategyController {

    static final String AI_KEY = "AI_GBM";
    static final String REFERENCE = "EW_BUY_HOLD";

    private final JdbcClient jdbc;
    private final Rows rows;

    public StrategyController(JdbcClient jdbc, Rows rows) {
        this.jdbc = jdbc;
        this.rows = rows;
    }

    private Map<String, Object> latestRun() {
        return jdbc.sql("SELECT id, run_at, data_cutoff, oos_start, config, summary, is_demo FROM strategy_run ORDER BY id DESC LIMIT 1")
                .query().listOfRows().stream().findFirst().map(rows::camel).orElse(null);
    }

    @GetMapping("/api/strategies")
    public Map<String, Object> strategies() {
        Map<String, Object> out = new HashMap<>();
        Map<String, Object> run = latestRun();
        out.put("run", run);
        out.put("results", run == null ? List.of() : rows.camel(jdbc.sql("""
                SELECT strategy_key, family, name, description, params, metrics, equity, yearly, cost_sensitivity, verdict
                FROM strategy_result WHERE run_id = :r
                ORDER BY (metrics->>'sharpe')::float8 DESC NULLS LAST, strategy_key""").param("r", run.get("id")).query().listOfRows()));
        return out;
    }

    @GetMapping("/api/strategies/{key}")
    public Map<String, Object> strategy(@PathVariable String key) {
        Map<String, Object> run = latestRun();
        if (run == null) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "no strategy backtest has been run yet");
        Object runId = run.get("id");
        Map<String, Object> result = jdbc.sql("""
                SELECT strategy_key, family, name, description, params, metrics, equity, yearly, cost_sensitivity, verdict
                FROM strategy_result WHERE run_id = :r AND strategy_key = :k""").param("r", runId).param("k", key)
                .query().listOfRows().stream().findFirst().map(rows::camel)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown strategy " + key));
        Map<String, Object> out = new HashMap<>();
        out.put("run", run);
        out.put("result", result);
        out.put("reference", jdbc.sql("SELECT strategy_key, name, equity FROM strategy_result WHERE run_id = :r AND strategy_key = :k")
                .param("r", runId).param("k", REFERENCE).query().listOfRows().stream().findFirst().map(rows::camel).orElse(null));
        out.put("trades", rows.camel(jdbc.sql("""
                SELECT company_id, symbol, entry_date, exit_date, trade_return, holding_days, entry_reason, exit_reason
                FROM strategy_trade WHERE run_id = :r AND strategy_key = :k ORDER BY entry_date DESC, symbol LIMIT 2000""")
                .param("r", runId).param("k", key).query().listOfRows()));
        out.put("tradeCount", jdbc.sql("SELECT count(*) FROM strategy_trade WHERE run_id = :r AND strategy_key = :k")
                .param("r", runId).param("k", key).query(Long.class).single());
        return out;
    }

    @GetMapping("/api/decisions")
    public Map<String, Object> decisions(@RequestParam(required = false) LocalDate date) {
        List<LocalDate> dates = jdbc.sql("""
                SELECT DISTINCT as_of_date FROM strategy_decision WHERE strategy_key = :k ORDER BY as_of_date DESC LIMIT 60""")
                .param("k", AI_KEY).query(LocalDate.class).list();
        LocalDate d = date != null ? date : dates.stream().findFirst().orElse(null);
        Map<String, Object> out = new HashMap<>();
        out.put("asOfDate", d);
        out.put("dates", dates);
        out.put("decisions", d == null ? List.of() : rows.camel(jdbc.sql("""
                SELECT s.id, s.company_id, c.name,
                       (SELECT symbol FROM ticker_history t WHERE t.company_id = c.id ORDER BY valid_from DESC LIMIT 1) AS symbol,
                       s.as_of_date, s.strategy_key, s.action, s.probability, s.entry_p, s.exit_p, s.weight, s.rank,
                       s.factors, s.rule_votes, s.model, s.issued_at, s.is_demo,
                       e.text AS explanation, e.model AS explanation_model
                FROM strategy_decision s JOIN company c ON c.id = s.company_id
                LEFT JOIN decision_explanation e ON e.decision_id = s.id
                WHERE s.strategy_key = :k AND s.as_of_date = :d
                ORDER BY CASE s.action WHEN 'ENTER' THEN 0 WHEN 'EXIT' THEN 1 WHEN 'HOLD' THEN 2 ELSE 3 END, s.probability DESC""")
                .param("k", AI_KEY).param("d", d).query().listOfRows()));
        return out;
    }
}
