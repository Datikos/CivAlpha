package com.civalpha.forecast;

import com.civalpha.storage.DocumentStore;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;

/**
 * Issues forecasts and stores them immutably. A forecast series is (company, model, as-of trading date).
 * Re-issuing a series with changed inputs or output creates version n+1 linked to version n; an identical
 * re-issue is skipped. Database triggers reject UPDATE/DELETE on forecast rows.
 */
@Service
public class ForecastService {

    private final JdbcClient jdbc;
    private final MlClient ml;
    private final ObjectMapper om;

    public ForecastService(JdbcClient jdbc, MlClient ml, ObjectMapper om) {
        this.jdbc = jdbc;
        this.ml = ml;
        this.om = om;
    }

    public record IssueResult(int created, int unchanged, List<Long> ids) {}

    /** Live issue: data cutoff = now; the window starts at the latest close at or before now. */
    public IssueResult issueLive(List<Long> companyIds, String reason) {
        List<Map<String, Object>> payloads = ml.forecasts(OffsetDateTime.now(java.time.ZoneOffset.UTC).toString(), null, companyIds);
        return persistAll(payloads, "LIVE", reason);
    }

    /** Replay issue: cutoff = close of each historical date. Published now, labelled REPLAY in the UI. */
    public IssueResult issueReplay(List<LocalDate> dates, String reason) {
        List<Map<String, Object>> payloads = ml.forecasts(null, dates.stream().map(LocalDate::toString).toList(), null);
        return persistAll(payloads, "REPLAY", reason);
    }

    public IssueResult issueAt(LocalDate asOfDate, String reason) {
        List<Map<String, Object>> payloads = ml.forecasts(null, List.of(asOfDate.toString()), null);
        return persistAll(payloads, asOfDate.isBefore(LocalDate.now().minusDays(7)) ? "REPLAY" : "LIVE", reason);
    }

    private IssueResult persistAll(List<Map<String, Object>> payloads, String mode, String reason) {
        int created = 0, unchanged = 0;
        List<Long> ids = new ArrayList<>();
        for (Map<String, Object> p : payloads) {
            Optional<Long> id = persist(p, mode, reason);
            if (id.isPresent()) {
                created++;
                ids.add(id.get());
            } else {
                unchanged++;
            }
        }
        return new IssueResult(created, unchanged, ids);
    }

    /** @return the new forecast id, or empty if identical to the latest version of its series. */
    @Transactional
    public Optional<Long> persist(Map<String, Object> p, String mode, String reason) {
        long companyId = ((Number) p.get("companyId")).longValue();
        String kind = (String) p.get("modelKind");
        String asOfDate = (String) p.get("asOfDate");
        String series = companyId + "|" + kind + "|" + asOfDate;
        String hash = contentHash(p);
        Optional<Map<String, Object>> prev = jdbc.sql("SELECT id, version, content_sha256 FROM forecast WHERE series_key = :s ORDER BY version DESC LIMIT 1")
                .param("s", series).query().listOfRows().stream().findFirst();
        if (prev.isPresent() && hash.equals(prev.get().get("content_sha256"))) return Optional.empty();
        int version = prev.map(r -> ((Number) r.get("version")).intValue() + 1).orElse(1);
        Long supersedes = prev.map(r -> ((Number) r.get("id")).longValue()).orElse(null);
        boolean demo = jdbc.sql("SELECT is_demo FROM company WHERE id = :c").param("c", companyId).query(Boolean.class).single();
        long id = jdbc.sql("""
                INSERT INTO forecast (company_id, symbol, benchmark_symbol, model_kind, model_version_id, target, horizon_trading_days,
                    as_of, as_of_date, issue_mode, probability, prob_low, prob_high, uncertainty_note, features, explanation, sources,
                    series_key, version, supersedes_id, reason, content_sha256, is_demo)
                VALUES (:c, :sym, :bench, :kind, :mv, :target, :h, :asof, :d, :mode, :p, :lo, :hi, :note,
                    CAST(:features AS jsonb), CAST(:expl AS jsonb), CAST(:sources AS jsonb), :series, :v, :sup, :reason, :hash, :demo)
                RETURNING id""")
                .param("c", companyId).param("sym", p.get("symbol")).param("bench", p.get("benchmarkSymbol")).param("kind", kind)
                .param("mv", ((Number) p.get("modelVersionId")).longValue()).param("target", p.get("target"))
                .param("h", ((Number) p.get("horizonTradingDays")).intValue()).param("asof", OffsetDateTime.parse((String) p.get("asOf")))
                .param("d", LocalDate.parse(asOfDate)).param("mode", mode).param("p", num(p.get("probability")))
                .param("lo", num(p.get("probLow"))).param("hi", num(p.get("probHigh"))).param("note", p.get("uncertaintyNote"))
                .param("features", om.writeValueAsString(p.get("features"))).param("expl", om.writeValueAsString(p.get("explanation")))
                .param("sources", om.writeValueAsString(p.get("sources"))).param("series", series).param("v", version)
                .param("sup", supersedes).param("reason", version > 1 ? reason + " (supersedes v" + (version - 1) + ")" : reason)
                .param("hash", hash).param("demo", demo).query(Long.class).single();
        return Optional.of(id);
    }

    /** Hash of everything a reader sees, excluding the model-version row id (a refit with identical output is not new evidence). */
    String contentHash(Map<String, Object> p) {
        Map<String, Object> c = new TreeMap<>();
        for (String k : List.of("companyId", "modelKind", "asOfDate", "probability", "probLow", "probHigh", "features", "explanation", "sources", "target")) {
            c.put(k, canonical(p.get(k)));
        }
        return DocumentStore.sha256(om.writeValueAsString(c).getBytes(StandardCharsets.UTF_8));
    }

    @SuppressWarnings("unchecked")
    private static Object canonical(Object o) {
        if (o instanceof Map<?, ?> m) {
            Map<String, Object> t = new TreeMap<>();
            m.forEach((k, v) -> t.put(String.valueOf(k), canonical(v)));
            return t;
        }
        if (o instanceof List<?> l) return l.stream().map(ForecastService::canonical).toList();
        if (o instanceof Double d) return Math.round(d * 1e6) / 1e6;
        return o;
    }

    private static Double num(Object o) {
        return o == null ? null : ((Number) o).doubleValue();
    }
}
