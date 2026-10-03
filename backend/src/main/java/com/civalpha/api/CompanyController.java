package com.civalpha.api;

import com.civalpha.universe.TickerResolver;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/companies")
public class CompanyController {

    static final Map<String, String> LABELS = Map.ofEntries(
            Map.entry("Revenues", "Revenue"), Map.entry("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenue (ASC 606)"),
            Map.entry("GrossProfit", "Gross profit"), Map.entry("OperatingIncomeLoss", "Operating income"),
            Map.entry("NetIncomeLoss", "Net income"), Map.entry("Assets", "Total assets"), Map.entry("Liabilities", "Total liabilities"),
            Map.entry("LongTermDebtNoncurrent", "Long-term debt"), Map.entry("CashAndCashEquivalentsAtCarryingValue", "Cash and equivalents"));

    private final JdbcClient jdbc;
    private final Rows rows;
    private final TickerResolver tickers;

    public CompanyController(JdbcClient jdbc, Rows rows, TickerResolver tickers) {
        this.jdbc = jdbc;
        this.rows = rows;
        this.tickers = tickers;
    }

    long resolve(String symbol) {
        return tickers.companyEver(symbol).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown symbol " + symbol));
    }

    @GetMapping
    public List<Map<String, Object>> list() {
        List<Map<String, Object>> out = rows.camel(jdbc.sql("""
                SELECT c.id, (SELECT symbol FROM ticker_history t WHERE t.company_id = c.id ORDER BY valid_from DESC LIMIT 1) AS symbol,
                       c.name, c.sector, c.benchmark_symbol, c.is_demo,
                       (SELECT cik FROM cik_mapping m WHERE m.company_id = c.id AND m.valid_to IS NULL LIMIT 1) AS cik,
                       p.close AS latest_close, p.trade_date AS latest_close_date
                FROM company c
                LEFT JOIN LATERAL (SELECT close, trade_date FROM price_bar b WHERE b.company_id = c.id ORDER BY trade_date DESC LIMIT 1) p ON true
                ORDER BY 2""").query().listOfRows());
        Map<Long, Map<String, Object>> latest = new HashMap<>();
        for (Map<String, Object> f : jdbc.sql("""
                SELECT DISTINCT ON (company_id, model_kind) company_id, model_kind, id, probability, as_of_date
                FROM forecast ORDER BY company_id, model_kind, as_of_date DESC, version DESC""").query().listOfRows()) {
            long cid = ((Number) f.get("company_id")).longValue();
            latest.computeIfAbsent(cid, k -> new LinkedHashMap<>()).put((String) f.get("model_kind"),
                    Map.of("id", f.get("id"), "probability", rows.value(f.get("probability")), "asOfDate", rows.value(f.get("as_of_date"))));
        }
        for (Map<String, Object> c : out) c.put("latestForecasts", latest.getOrDefault(((Number) c.get("id")).longValue(), Map.of()));
        return out;
    }

    @GetMapping("/{symbol}")
    public Map<String, Object> get(@PathVariable String symbol) {
        long id = resolve(symbol);
        Map<String, Object> c = rows.camel(jdbc.sql("""
                SELECT c.id, c.name, c.sector, c.industry, c.benchmark_symbol, c.exchange, c.is_demo FROM company c WHERE c.id = :id""")
                .param("id", id).query().singleRow());
        c.put("symbol", tickers.currentSymbol(id));
        c.put("tickerHistory", rows.camel(jdbc.sql("SELECT symbol, valid_from, valid_to, source FROM ticker_history WHERE company_id = :id ORDER BY valid_from")
                .param("id", id).query().listOfRows()));
        c.put("cikHistory", rows.camel(jdbc.sql("SELECT cik, valid_from, valid_to, source FROM cik_mapping WHERE company_id = :id ORDER BY valid_from")
                .param("id", id).query().listOfRows()));
        List<Map<String, Object>> facts = new ArrayList<>();
        for (Map<String, Object> f : pitFacts(id, OffsetDateTime.now(ZoneOffset.UTC), true)) {
            String concept = (String) f.get("concept");
            if (!LABELS.containsKey(concept)) continue;
            if (facts.stream().anyMatch(x -> x.get("concept").equals(concept))) continue;
            facts.add(fact(f));
        }
        c.put("keyFacts", facts);
        return c;
    }

    private Map<String, Object> fact(Map<String, Object> f) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("concept", f.get("concept"));
        m.put("label", LABELS.getOrDefault((String) f.get("concept"), (String) f.get("concept")));
        m.put("value", rows.value(f.get("value")));
        m.put("unit", f.get("unit"));
        m.put("periodStart", rows.value(f.get("period_start")));
        m.put("periodEnd", rows.value(f.get("period_end")));
        m.put("fiscalPeriod", f.get("fiscal_period"));
        m.put("formType", f.get("form_type"));
        m.put("filedDate", rows.value(f.get("filed_date")));
        m.put("accessionNo", f.get("accession_no"));
        m.put("filingId", f.get("filing_id"));
        m.put("sourceUrl", Boolean.TRUE.equals(f.get("is_demo")) && f.get("filing_id") != null ? "/filings/" + f.get("filing_id") : f.get("source_url"));
        m.put("isDemo", f.get("is_demo"));
        return m;
    }

    /**
     * Point-in-time facts: for every (concept, unit, period, dimensions) the latest value accepted on/before asOf,
     * plus the first-filed value (also on/before asOf) to show revisions.
     */
    List<Map<String, Object>> pitFacts(long companyId, OffsetDateTime asOf, boolean latestPeriodFirst) {
        return jdbc.sql("""
                WITH known AS (
                    SELECT * FROM xbrl_fact WHERE company_id = :c AND accepted_at <= :asof AND dims_key = '' AND taxonomy = 'us-gaap'
                ), ranked AS (
                    SELECT k.*,
                           row_number() OVER (PARTITION BY concept, unit, period_start, period_end ORDER BY accepted_at DESC, id DESC) AS rn,
                           first_value(value) OVER (PARTITION BY concept, unit, period_start, period_end ORDER BY accepted_at, id) AS original_value
                    FROM known k
                )
                SELECT * FROM ranked WHERE rn = 1 ORDER BY period_end %s, concept""".formatted(latestPeriodFirst ? "DESC" : "ASC"))
                .param("c", companyId).param("asof", asOf).query().listOfRows();
    }

    @GetMapping("/{symbol}/financials")
    public Map<String, Object> financials(@PathVariable String symbol, @RequestParam(required = false) OffsetDateTime asOf) {
        long id = resolve(symbol);
        OffsetDateTime at = asOf == null ? OffsetDateTime.now(ZoneOffset.UTC) : asOf;
        Map<String, Map<String, Object>> series = new LinkedHashMap<>();
        for (Map<String, Object> f : pitFacts(id, at, false)) {
            String concept = (String) f.get("concept");
            if (!LABELS.containsKey(concept)) continue;
            Map<String, Object> s = series.computeIfAbsent(concept, k -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("concept", k);
                m.put("label", LABELS.get(k));
                m.put("unit", f.get("unit"));
                m.put("points", new ArrayList<Map<String, Object>>());
                return m;
            });
            Map<String, Object> p = fact(f);
            double v = ((Number) rows.value(f.get("value"))).doubleValue();
            double orig = ((Number) rows.value(f.get("original_value"))).doubleValue();
            p.put("revised", v != orig);
            p.put("originalValue", v != orig ? orig : null);
            if (f.get("period_start") != null) {
                long days = java.time.temporal.ChronoUnit.DAYS.between((LocalDate) rows.value(f.get("period_start")), (LocalDate) rows.value(f.get("period_end")));
                if (days > 100 && days < 350) continue; // skip year-to-date (6/9-month) values
                p.put("fiscalPeriod", days >= 350 ? "FY" : f.get("fiscal_period") == null || "FY".equals(f.get("fiscal_period")) ? "Q" : f.get("fiscal_period"));
            }
            @SuppressWarnings("unchecked") List<Map<String, Object>> pts = (List<Map<String, Object>>) s.get("points");
            pts.add(p);
        }
        return Map.of("asOf", at, "series", new ArrayList<>(series.values()));
    }

    @GetMapping("/{symbol}/prices")
    public Map<String, Object> prices(@PathVariable String symbol, @RequestParam(required = false) LocalDate from) {
        long id = resolve(symbol);
        LocalDate start = from == null ? LocalDate.now().minusYears(3) : from;
        Map<String, Object> c = jdbc.sql("SELECT benchmark_symbol, is_demo FROM company WHERE id = :id").param("id", id).query().singleRow();
        List<Map<String, Object>> bars = rows.camel(jdbc.sql("""
                SELECT p.trade_date AS date, p.symbol, p.close, b.close AS benchmark_close
                FROM price_bar p LEFT JOIN price_bar b ON b.symbol = :bench AND b.trade_date = p.trade_date
                WHERE p.company_id = :id AND p.trade_date >= :from ORDER BY p.trade_date""")
                .param("bench", c.get("benchmark_symbol")).param("id", id).param("from", start).query().listOfRows());
        List<Map<String, Object>> actions = rows.camel(jdbc.sql("""
                SELECT ex_date, action_type AS type, value, symbol FROM corporate_action WHERE company_id = :id AND ex_date >= :from ORDER BY ex_date""")
                .param("id", id).param("from", start).query().listOfRows());
        return Map.of("symbol", tickers.currentSymbol(id), "benchmarkSymbol", c.get("benchmark_symbol"), "isDemo", c.get("is_demo"),
                "bars", bars, "corporateActions", actions);
    }

    @GetMapping("/{symbol}/filings")
    public List<Map<String, Object>> filings(@PathVariable String symbol) {
        long id = resolve(symbol);
        return jdbc.sql("""
                SELECT f.id, f.accession_no, f.form_type, f.period_of_report, f.filed_date, f.accepted_at, f.items, f.amends_accession,
                       f.is_demo, sd.url AS source_url, f.source_document_id,
                       (SELECT count(*) FROM filing_passage p WHERE p.filing_id = f.id) AS passage_count,
                       (SELECT count(*) FROM xbrl_fact x WHERE x.filing_id = f.id) AS fact_count
                FROM filing f LEFT JOIN source_document sd ON sd.id = f.source_document_id
                WHERE f.company_id = :id ORDER BY f.accepted_at DESC""").param("id", id).query().listOfRows()
                .stream().map(r -> FilingController.withUrl(rows.camel(r))).toList();
    }
}
