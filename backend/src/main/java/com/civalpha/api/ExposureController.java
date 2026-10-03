package com.civalpha.api;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
public class ExposureController {

    private final JdbcClient jdbc;
    private final Rows rows;
    private final CompanyController companies;

    public ExposureController(JdbcClient jdbc, Rows rows, CompanyController companies) {
        this.jdbc = jdbc;
        this.rows = rows;
        this.companies = companies;
    }

    /** Latest exposure per (target, channel) available at asOf, with its supporting filing, passage and fact. */
    List<Map<String, Object>> exposuresAsOf(long companyId, OffsetDateTime asOf) {
        return jdbc.sql("""
                SELECT DISTINCT ON (x.target_type, x.target_code, x.exposure_channel)
                       x.id, x.target_type, x.target_code, x.exposure_channel AS channel, x.share, x.basis, x.confidence, x.method,
                       x.rationale, x.available_at, x.period_end, x.version, x.is_demo,
                       f.id AS filing_id, f.accession_no, f.form_type, f.is_demo AS filing_demo, f.source_document_id, sd.url AS filing_url,
                       p.id AS passage_id, p.section AS passage_section, p.text AS passage_text,
                       xf.id AS fact_id, xf.concept AS fact_concept, xf.value AS fact_value, xf.dimensions AS fact_dimensions
                FROM company_exposure x
                LEFT JOIN filing f ON f.id = x.filing_id
                LEFT JOIN source_document sd ON sd.id = f.source_document_id
                LEFT JOIN filing_passage p ON p.id = x.passage_id
                LEFT JOIN xbrl_fact xf ON xf.id = x.xbrl_fact_id
                WHERE x.company_id = :c AND x.available_at <= :asof
                ORDER BY x.target_type, x.target_code, x.exposure_channel, x.available_at DESC, x.id DESC""")
                .param("c", companyId).param("asof", asOf).query().listOfRows()
                .stream().map(this::shape).sorted((a, b) -> Double.compare(share(b), share(a))).toList();
    }

    private static double share(Map<String, Object> m) {
        Object s = m.get("share");
        return s == null ? -1 : ((Number) s).doubleValue();
    }

    private Map<String, Object> shape(Map<String, Object> r) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (String k : List.of("id", "target_type", "target_code", "channel", "share", "basis", "confidence", "method", "rationale",
                "available_at", "period_end", "version", "is_demo")) {
            m.put(Rows.camelKey(k), rows.value(r.get(k)));
        }
        if (r.get("filing_id") != null) {
            boolean demo = Boolean.TRUE.equals(r.get("filing_demo"));
            m.put("filing", Map.of("id", r.get("filing_id"), "accessionNo", r.get("accession_no"), "formType", r.get("form_type"),
                    "url", demo || r.get("filing_url") == null ? "/filings/" + r.get("filing_id") : r.get("filing_url")));
        } else {
            m.put("filing", null);
        }
        m.put("passage", r.get("passage_id") == null ? null : Map.of("id", r.get("passage_id"),
                "section", r.get("passage_section") == null ? "" : r.get("passage_section"), "text", r.get("passage_text")));
        if (r.get("fact_id") != null) {
            Map<String, Object> f = new LinkedHashMap<>();
            f.put("id", r.get("fact_id"));
            f.put("concept", r.get("fact_concept"));
            f.put("value", rows.value(r.get("fact_value")));
            f.put("dimensions", rows.value(r.get("fact_dimensions")));
            m.put("fact", f);
        } else {
            m.put("fact", null);
        }
        return m;
    }

    @GetMapping("/api/companies/{symbol}/exposures")
    public Map<String, Object> exposures(@PathVariable String symbol, @RequestParam(required = false) OffsetDateTime asOf) {
        long id = companies.resolve(symbol);
        OffsetDateTime at = asOf == null ? OffsetDateTime.now(ZoneOffset.UTC) : asOf;
        List<Map<String, Object>> ex = exposuresAsOf(id, at);
        List<Map<String, Object>> paths = rows.camel(jdbc.sql("""
                WITH ex AS (
                    SELECT DISTINCT ON (x.target_type, x.target_code, x.exposure_channel) x.*
                    FROM company_exposure x WHERE x.company_id = :c AND x.available_at <= :asof
                    ORDER BY x.target_type, x.target_code, x.exposure_channel, x.available_at DESC, x.id DESC
                )
                SELECT e.id AS event_id, e.title AS event_title, e.category AS event_category, e.published_at AS event_published_at,
                       e.evidence_status, t.target_type, t.target_code, ex.id AS exposure_id, ex.exposure_channel AS channel, ex.basis,
                       ex.confidence, ex.share, ex.passage_id, f.accession_no AS filing_accession_no, f.id AS filing_id
                FROM ex JOIN event_target t ON t.target_type = ex.target_type AND t.target_code = ex.target_code
                JOIN policy_event e ON e.id = t.event_id AND e.published_at <= :asof
                LEFT JOIN filing f ON f.id = ex.filing_id
                ORDER BY e.published_at DESC LIMIT 200""").param("c", id).param("asof", at).query().listOfRows());
        return Map.of("asOf", at, "exposures", ex, "paths", paths);
    }
}
