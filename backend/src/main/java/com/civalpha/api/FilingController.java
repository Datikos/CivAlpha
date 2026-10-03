package com.civalpha.api;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class FilingController {

    private final JdbcClient jdbc;
    private final Rows rows;

    public FilingController(JdbcClient jdbc, Rows rows) {
        this.jdbc = jdbc;
        this.rows = rows;
    }

    /** Demo filings are not on EDGAR: link to the stored copy instead of a sec.gov URL. */
    static Map<String, Object> withUrl(Map<String, Object> f) {
        Object docId = f.remove("sourceDocumentId");
        Object src = f.remove("sourceUrl");
        f.put("documentUrl", docId == null ? null : "/api/documents/" + docId);
        f.put("url", Boolean.TRUE.equals(f.get("isDemo")) ? f.get("documentUrl") : src);
        return f;
    }

    @GetMapping("/api/filings/{id}")
    public Map<String, Object> get(@PathVariable long id) {
        Map<String, Object> f = withUrl(rows.camel(jdbc.sql("""
                SELECT f.id, f.accession_no, f.form_type, f.period_of_report, f.filed_date, f.accepted_at, f.items, f.amends_accession,
                       f.is_demo, sd.url AS source_url, f.source_document_id,
                       (SELECT symbol FROM ticker_history t WHERE t.company_id = f.company_id ORDER BY valid_from DESC LIMIT 1) AS company_symbol
                FROM filing f LEFT JOIN source_document sd ON sd.id = f.source_document_id WHERE f.id = :id""")
                .param("id", id).query().singleRow()));
        f.put("passages", rows.camel(jdbc.sql("""
                SELECT id, section, topic, text, extraction_method, extractor_version, char_start, char_end
                FROM filing_passage WHERE filing_id = :id ORDER BY char_start""").param("id", id).query().listOfRows()));
        f.put("facts", rows.camel(jdbc.sql("""
                SELECT id, concept, value, unit, period_start, period_end, dimensions, accepted_at
                FROM xbrl_fact WHERE filing_id = :id ORDER BY concept, period_end, dims_key LIMIT 500""").param("id", id).query().listOfRows()));
        return f;
    }
}
