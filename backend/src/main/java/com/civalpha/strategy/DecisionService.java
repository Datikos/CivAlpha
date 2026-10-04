package com.civalpha.strategy;

import com.civalpha.forecast.MlClient;
import com.civalpha.llm.LlmProvider;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Stores the AI strategy's daily decisions (append-only; a database trigger rejects UPDATE/DELETE) and, when a
 * language model is configured, adds a plain-language explanation for ENTER and EXIT actions. The explanation
 * is written after the decision, in its own table, and never changes it.
 */
@Service
public class DecisionService {

    static final Set<String> EXPLAINED = Set.of("ENTER", "EXIT");

    private final JdbcClient jdbc;
    private final MlClient ml;
    private final LlmProvider llm;
    private final ObjectMapper om;

    public DecisionService(JdbcClient jdbc, MlClient ml, LlmProvider llm, ObjectMapper om) {
        this.jdbc = jdbc;
        this.ml = ml;
        this.llm = llm;
        this.om = om;
    }

    public record DecideResult(int created, int existing, int explained) {}

    public DecideResult decide(LocalDate asOf, Consumer<String> log) {
        return persistAll(ml.decide(asOf == null ? null : asOf.toString()), log);
    }

    public DecideResult persistAll(List<Map<String, Object>> decisions, Consumer<String> log) {
        int created = 0, existing = 0, explained = 0;
        for (Map<String, Object> d : decisions) {
            Optional<Long> id = persist(d);
            if (id.isEmpty()) {
                existing++;
                continue;
            }
            created++;
            String action = (String) d.get("action");
            if (!"HOLD".equals(action) && !"STAY_OUT".equals(action)) {
                log.accept("%s %s (p = %.2f)".formatted(action, d.get("symbol"), ((Number) d.get("probability")).doubleValue()));
            }
            if (llm.enabled() && EXPLAINED.contains(action) && explain(id.get(), d)) explained++;
        }
        return new DecideResult(created, existing, explained);
    }

    /** Inserts one decision; empty if this (company, date, strategy) was already decided. */
    Optional<Long> persist(Map<String, Object> d) {
        return jdbc.sql("""
                INSERT INTO strategy_decision (company_id, as_of_date, strategy_key, action, probability, entry_p, exit_p, weight, rank,
                                               factors, rule_votes, model, is_demo)
                VALUES (:c, :d, :k, :a, :p, :ep, :xp, :w, :r, CAST(:f AS jsonb), CAST(:v AS jsonb), CAST(:m AS jsonb), :demo)
                ON CONFLICT (company_id, as_of_date, strategy_key) DO NOTHING
                RETURNING id""")
                .param("c", ((Number) d.get("companyId")).longValue()).param("d", LocalDate.parse((String) d.get("asOfDate")))
                .param("k", d.get("strategyKey")).param("a", d.get("action"))
                .param("p", ((Number) d.get("probability")).doubleValue()).param("ep", ((Number) d.get("entryP")).doubleValue())
                .param("xp", ((Number) d.get("exitP")).doubleValue()).param("w", ((Number) d.get("weight")).doubleValue())
                .param("r", ((Number) d.get("rank")).intValue())
                .param("f", om.writeValueAsString(d.get("factors"))).param("v", om.writeValueAsString(d.get("ruleVotes")))
                .param("m", om.writeValueAsString(d.get("model"))).param("demo", Boolean.TRUE.equals(d.get("isDemo")))
                .query(Long.class).optional();
    }

    private boolean explain(long decisionId, Map<String, Object> d) {
        Map<String, Object> facts = new LinkedHashMap<>();
        for (String k : List.of("action", "probability", "entryP", "exitP", "rank", "maxPositions", "factors", "ruleVotes")) {
            facts.put(k, d.get(k));
        }
        facts.put("horizonTradingDays", ((Map<?, ?>) d.getOrDefault("model", Map.of())).get("horizon"));
        String text = llm.explainDecision((String) d.get("name"), (String) d.get("symbol"), facts);
        if (text == null || text.isBlank()) return false;
        jdbc.sql("INSERT INTO decision_explanation (decision_id, text, model) VALUES (:id, :t, :m) ON CONFLICT DO NOTHING")
                .param("id", decisionId).param("t", text).param("m", llm.name()).update();
        return true;
    }
}
