package com.civalpha.sec;

import com.civalpha.config.AppProperties;
import com.civalpha.exposure.ExposureService;
import com.civalpha.storage.DocumentStore;
import com.civalpha.storage.SourceDocument;
import com.civalpha.universe.TickerResolver;
import com.civalpha.universe.UniverseService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Ingests one company's SEC filings: filing index (submissions), XBRL facts (companyfacts), primary
 * documents (source-linked passages), XBRL instances of annual reports (dimensional geographic revenue),
 * then derives exposures. Idempotent; each fetched resource is stored as a versioned source document.
 */
@Service
public class FilingIngestionService {

    private static final Logger log = LoggerFactory.getLogger(FilingIngestionService.class);
    private static final ZoneId NEW_YORK = ZoneId.of("America/New_York");
    private static final Set<String> PERIODIC = Set.of("10-K", "10-Q", "10-K/A", "10-Q/A");
    private static final Set<String> RELEVANT_8K_ITEMS = Set.of("1.01", "2.02", "7.01", "8.01");
    private static final Set<String> INSTANCE_CONCEPTS = Set.of("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet");

    private final JdbcClient jdbc;
    private final NamedParameterJdbcTemplate named;
    private final ObjectMapper om;
    private final DocumentStore docs;
    private final TickerResolver tickers;
    private final UniverseService universe;
    private final ExposureService exposures;
    private final AppProperties props;

    public FilingIngestionService(JdbcClient jdbc, NamedParameterJdbcTemplate named, ObjectMapper om, DocumentStore docs,
                                  TickerResolver tickers, UniverseService universe, ExposureService exposures, AppProperties props) {
        this.jdbc = jdbc;
        this.named = named;
        this.om = om;
        this.docs = docs;
        this.tickers = tickers;
        this.universe = universe;
        this.exposures = exposures;
        this.props = props;
    }

    public record Result(String symbol, int filings, int facts, int passages, int exposures) {}

    public Result ingest(SecClient sec, long companyId, boolean demo, Consumer<String> logLine) {
        String cik = tickers.cikOf(companyId).orElseThrow(() -> new IllegalStateException("no CIK for company " + companyId));
        String symbol = tickers.currentSymbol(companyId);
        // 1. filing index
        String subUrl = SecClient.SUBMISSIONS.formatted(cik);
        byte[] subBytes = sec.get(subUrl).orElseThrow(() -> new IllegalStateException("SEC submissions not found for CIK " + cik));
        docs.store(new DocumentStore.NewDocument("SEC_API", "SEC EDGAR", subUrl, null, "Submissions index CIK" + cik,
                null, "application/json", subBytes, demo));
        SubmissionsParser.Submissions sub = SubmissionsParser.parse(om, subBytes);
        if (!sub.tickers().isEmpty()) {
            universe.recordObservedTicker(cik, sub.tickers().get(0), LocalDate.now(), "SEC submissions")
                    .ifPresent(ch -> logLine.accept(symbol + ": ticker change observed " + ch));
        }
        LocalDate minDate = demo ? LocalDate.of(1990, 1, 1) : LocalDate.now().minusYears(props.sec().lookbackYears());
        List<FilingMeta> wanted = sub.filings().stream()
                .filter(f -> f.filingDate() != null && !f.filingDate().isBefore(minDate))
                .filter(f -> PERIODIC.contains(f.form()) || ("8-K".equals(f.form()) && relevant8k(f.items())))
                .sorted(Comparator.comparing(FilingMeta::acceptedAt)).toList();
        Map<String, Long> filingIds = new HashMap<>();
        int newFilings = 0;
        for (FilingMeta f : wanted) {
            Optional<Long> existing = jdbc.sql("SELECT id FROM filing WHERE accession_no = :a").param("a", f.accessionNo()).query(Long.class).optional();
            if (existing.isPresent()) {
                filingIds.put(f.accessionNo(), existing.get());
                continue;
            }
            String amends = f.isAmendment() ? amendedAccession(companyId, f) : null;
            long id = jdbc.sql("""
                    INSERT INTO filing (company_id, cik, accession_no, form_type, period_of_report, filed_date, accepted_at,
                                        primary_document, items, amends_accession, is_demo)
                    VALUES (:c, :cik, :a, :form, :p, :fd, :at, :doc, :items, :amends, :demo) RETURNING id""")
                    .param("c", companyId).param("cik", cik).param("a", f.accessionNo()).param("form", f.form())
                    .param("p", f.reportDate()).param("fd", f.filingDate()).param("at", f.acceptedAt())
                    .param("doc", f.primaryDocument()).param("items", f.items()).param("amends", amends).param("demo", demo)
                    .query(Long.class).single();
            filingIds.put(f.accessionNo(), id);
            newFilings++;
        }
        // 2. XBRL facts (point-in-time key: EDGAR acceptance of the reporting filing)
        Map<String, FilingMeta> byAcc = new HashMap<>();
        sub.filings().forEach(f -> byAcc.put(f.accessionNo(), f));
        String cfUrl = SecClient.COMPANY_FACTS.formatted(cik);
        int facts = 0;
        Optional<byte[]> cf = sec.get(cfUrl);
        if (cf.isPresent()) {
            docs.store(new DocumentStore.NewDocument("SEC_API", "SEC EDGAR", cfUrl, null, "XBRL company facts CIK" + cik,
                    null, "application/json", cf.get(), demo));
            List<FactRow> rows = CompanyFactsParser.parse(om, cf.get(), CompanyFactsParser.CONCEPTS).stream()
                    .filter(r -> !r.filed().isBefore(minDate)).toList();
            facts = insertFacts(companyId, rows, byAcc, filingIds, cfUrl, demo);
        }
        // 3. documents, passages, instance facts, exposures
        int passages = 0, expo = 0;
        for (FilingMeta f : wanted) {
            long fid = filingIds.get(f.accessionNo());
            boolean hasDoc = jdbc.sql("SELECT source_document_id IS NOT NULL FROM filing WHERE id = :id").param("id", fid).query(Boolean.class).single();
            if (!hasDoc && f.primaryDocument() != null && !f.primaryDocument().isBlank()) {
                String url = SecClient.archiveUrl(cik, f.accessionNo(), f.primaryDocument());
                Optional<byte[]> html = sec.get(url);
                if (html.isPresent()) {
                    SourceDocument d = docs.store(new DocumentStore.NewDocument("SEC_FILING", "SEC EDGAR", url, f.accessionNo(),
                            symbol + " " + f.form() + " " + f.reportDate(), f.acceptedAt(), "text/html", html.get(), demo));
                    jdbc.sql("UPDATE filing SET source_document_id = :d WHERE id = :id").param("d", d.id()).param("id", fid).update();
                    passages += insertPassages(fid, new String(html.get(), StandardCharsets.UTF_8), demo);
                }
                if (f.isAnnual()) {
                    String inst = SecClient.archiveUrl(cik, f.accessionNo(), f.primaryDocument().replaceAll("\\.htm$", "_htm.xml"));
                    Optional<byte[]> xml = sec.get(inst);
                    if (xml.isPresent()) {
                        docs.store(new DocumentStore.NewDocument("SEC_FILING", "SEC EDGAR", inst, f.accessionNo(),
                                symbol + " " + f.form() + " XBRL instance", f.acceptedAt(), "application/xml", xml.get(), demo));
                        List<FactRow> dimFacts = XbrlInstanceParser.parse(xml.get(), INSTANCE_CONCEPTS, f.accessionNo(), f.form(), f.filingDate());
                        facts += insertFacts(companyId, dimFacts, byAcc, filingIds, inst, demo);
                    }
                }
                expo += exposures.deriveForFiling(fid);
            }
        }
        logLine.accept("%s: %d new filings, %d facts, %d passages, %d exposures (%s mode)".formatted(symbol, newFilings, facts, passages, expo, sec.mode()));
        return new Result(symbol, newFilings, facts, passages, expo);
    }

    private static boolean relevant8k(String items) {
        if (items == null) return false;
        for (String i : items.split(",")) if (RELEVANT_8K_ITEMS.contains(i.trim())) return true;
        return false;
    }

    /** Best-effort link from an amendment to the original filing: same base form and period, filed earlier. */
    private String amendedAccession(long companyId, FilingMeta f) {
        return jdbc.sql("""
                SELECT accession_no FROM filing WHERE company_id = :c AND form_type = :form AND period_of_report = :p
                  AND accepted_at < :at ORDER BY accepted_at DESC LIMIT 1""")
                .param("c", companyId).param("form", f.form().replace("/A", "")).param("p", f.reportDate()).param("at", f.acceptedAt())
                .query(String.class).optional().orElse(null);
    }

    int insertFacts(long companyId, List<FactRow> rows, Map<String, FilingMeta> byAcc, Map<String, Long> filingIds, String url, boolean demo) {
        List<MapSqlParameterSource> batch = new ArrayList<>(rows.size());
        for (FactRow r : rows) {
            FilingMeta f = byAcc.get(r.accessionNo());
            OffsetDateTime accepted = f != null ? f.acceptedAt()
                    : r.filed().atTime(23, 59, 59).atZone(NEW_YORK).toOffsetDateTime(); // conservative fallback
            String dims;
            try {
                dims = om.writeValueAsString(r.dimensions() == null ? Map.of() : r.dimensions());
            } catch (RuntimeException e) {
                dims = "{}";
            }
            batch.add(new MapSqlParameterSource()
                    .addValue("c", companyId).addValue("f", filingIds.get(r.accessionNo())).addValue("a", r.accessionNo())
                    .addValue("tax", r.taxonomy()).addValue("con", r.concept()).addValue("u", r.unit()).addValue("v", r.value())
                    .addValue("ps", r.periodStart()).addValue("pe", r.periodEnd()).addValue("fy", r.fiscalYear())
                    .addValue("fp", r.fiscalPeriod()).addValue("form", r.form()).addValue("fd", r.filed()).addValue("at", accepted)
                    .addValue("dims", dims).addValue("dk", r.dimsKey()).addValue("url", url).addValue("demo", demo));
        }
        int[] res = named.batchUpdate("""
                INSERT INTO xbrl_fact (company_id, filing_id, accession_no, taxonomy, concept, unit, value, period_start, period_end,
                    fiscal_year, fiscal_period, form_type, filed_date, accepted_at, dimensions, dims_key, source_url, is_demo)
                VALUES (:c, :f, :a, :tax, :con, :u, :v, :ps, :pe, :fy, :fp, :form, :fd, :at, CAST(:dims AS jsonb), :dk, :url, :demo)
                ON CONFLICT (accession_no, taxonomy, concept, unit, period_start, period_end, dims_key) DO NOTHING""",
                batch.toArray(MapSqlParameterSource[]::new));
        int n = 0;
        for (int x : res) n += Math.max(0, x);
        return n;
    }

    private int insertPassages(long filingId, String html, boolean demo) {
        int n = 0;
        for (PassageExtractor.Passage p : PassageExtractor.extract(html)) {
            jdbc.sql("""
                    INSERT INTO filing_passage (filing_id, section, topic, text, char_start, char_end, extraction_method, extractor_version, is_demo)
                    VALUES (:f, :s, :t, :x, :cs, :ce, 'RULE_KEYWORD', :v, :demo)""")
                    .param("f", filingId).param("s", p.section()).param("t", p.topic()).param("x", p.text())
                    .param("cs", p.charStart()).param("ce", p.charEnd()).param("v", PassageExtractor.VERSION).param("demo", demo).update();
            n++;
        }
        log.debug("filing {}: {} passages", filingId, n);
        return n;
    }
}
