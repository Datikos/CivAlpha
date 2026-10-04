package com.civalpha.it;

import com.civalpha.api.CompanyController;
import com.civalpha.forecast.ForecastService;
import com.civalpha.market.MarketDataService;
import com.civalpha.sec.FilingIngestionService;
import com.civalpha.sec.FixtureSecClient;
import com.civalpha.universe.TickerResolver;
import com.civalpha.universe.UniverseService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Database-level tests (PostgreSQL via Testcontainers; skipped when Docker is unavailable). */
@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers(disabledWithoutDocker = true)
class PlatformIntegrationTest {

    static final PostgreSQLContainer PG = new PostgreSQLContainer("postgres:16-alpine");
    static final Path TMP;

    static {
        PG.start();
        try {
            TMP = Files.createTempDirectory("civalpha-it");
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry r) {
        r.add("spring.datasource.url", PG::getJdbcUrl);
        r.add("spring.datasource.username", PG::getUsername);
        r.add("spring.datasource.password", PG::getPassword);
        r.add("civalpha.universe-file", () -> "src/test/resources/test-universe.yml");
        r.add("civalpha.storage.documents-dir", () -> TMP.resolve("docs").toString());
    }

    @Autowired JdbcClient jdbc;
    @Autowired UniverseService universe;
    @Autowired TickerResolver tickers;
    @Autowired MarketDataService market;
    @Autowired FilingIngestionService filings;
    @Autowired CompanyController companies;
    @Autowired ForecastService forecasts;
    @Autowired MockMvc mvc;
    @Autowired com.civalpha.market.PriceSyncService priceSync;

    @Test
    void readEndpointsAnswerWithoutErrors() throws Exception {
        for (String url : List.of("/api/meta", "/api/companies", "/api/companies/META", "/api/companies/FB/prices",
                "/api/companies/META/filings", "/api/companies/META/financials", "/api/companies/META/exposures",
                "/api/events", "/api/events?category=TRADE_TARIFF", "/api/forecasts/current", "/api/forecasts/history",
                "/api/forecasts/history?symbol=META&modelKind=AUGMENTED", "/api/accuracy", "/api/admin/jobs")) {
            mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get(url))
                    .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isOk());
        }
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get("/api/companies/NOPE"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isNotFound());
        // an event without original evidence is rejected
        mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post("/api/events")
                        .contentType("application/json").content("{\"category\":\"TRADE_TARIFF\",\"title\":\"x\",\"eventDate\":\"2026-01-01\",\"publishedAt\":\"2026-01-01T00:00:00Z\"}"))
                .andExpect(org.springframework.test.web.servlet.result.MockMvcResultMatchers.status().isBadRequest());
    }

    @BeforeEach
    void load() {
        universe.load(false);
    }

    long meta() {
        return tickers.companyByCik("0001326801").orElseThrow();
    }

    @Test
    void tickerChangeKeepsOneCompanyAcrossPricesAndLookups() throws Exception {
        String csv = """
                symbol,date,open,high,low,close,volume
                FB,2022-06-08,195,197,193,196.64,100
                META,2022-06-09,194,195,184,184.00,100
                XLC,2022-06-08,60,61,59,60.5,100
                XLC,2022-06-09,60,61,59,59.9,100
                FB,2022-06-10,1,1,1,1,1
                """;
        var r = market.importPrices(csv.getBytes(StandardCharsets.UTF_8), "ticker-change.csv", "test", false);
        // FB after the rename is not a valid symbol for this company any more
        assertThat(r.unknownSymbols()).containsExactly("FB");
        List<Long> ids = jdbc.sql("SELECT DISTINCT company_id FROM price_bar WHERE symbol IN ('FB','META')").query(Long.class).list();
        assertThat(ids).containsExactly(meta());
        assertThat(tickers.companyEver("FB")).contains(meta());
        assertThat(tickers.companyAt("FB", LocalDate.parse("2021-01-04"))).contains(meta());
        assertThat(tickers.companyAt("META", LocalDate.parse("2021-01-04"))).isEmpty();
        assertThat(companies.get("FB").get("symbol")).isEqualTo("META");

        // a newly observed symbol (e.g. from SEC submissions) closes the current span and preserves history
        LocalDate change = LocalDate.parse("2030-01-02");
        assertThat(universe.recordObservedTicker("0001326801", "MTAX", change, "test")).contains("META -> MTAX");
        assertThat(tickers.companyAt("META", change.minusDays(1))).contains(meta());
        assertThat(tickers.companyAt("META", change)).isEmpty();
        assertThat(tickers.companyAt("MTAX", change)).contains(meta());
        assertThat(universe.recordObservedTicker("0001326801", "MTAX", change.plusDays(5), "test")).isEmpty(); // idempotent
        jdbc.sql("DELETE FROM ticker_history WHERE symbol = 'MTAX'").update();
        jdbc.sql("UPDATE ticker_history SET valid_to = NULL WHERE symbol = 'META'").update();
    }

    @Test
    @org.springframework.transaction.annotation.Transactional  // rolled back: other tests keep their benchmark rows
    void missingBenchmarksAreReportedBeforeCallingTheModel() {
        jdbc.sql("DELETE FROM price_bar WHERE company_id IS NULL").update();
        assertThat(market.missingBenchmarks()).containsExactly("XLC");
        assertThatThrownBy(market::requireBenchmarks).isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("No benchmark ETF prices are loaded (XLC)");
        jdbc.sql("""
                INSERT INTO price_bar (symbol, trade_date, close, provider) VALUES ('XLC', '2024-01-02', 60, 'test')""").update();
        assertThat(market.missingBenchmarks()).isEmpty();
        assertThat(market.requireBenchmarks()).isEmpty();
    }

    @Test
    @org.springframework.transaction.annotation.Transactional  // rolled back so other tests see the original universe
    void universeCanBeManagedAndSeedingNeverUndoesIt() {
        long id = universe.add(new UniverseService.NewCompany("bdsx", "Biodesix, Inc.", "1439725", "Health Care", "DIAGNOSTICS", "xlv", null));
        assertThat(tickers.companyAt("BDSX", LocalDate.now())).contains(id);
        assertThat(universe.companies()).extracting(c -> ((Number) c.get("id")).longValue()).contains(id);
        assertThat(universe.benchmarkSymbols()).contains("XLV");
        // duplicates are rejected
        assertThatThrownBy(() -> universe.add(new UniverseService.NewCompany("BDSY", "x", "1439725", "Health Care", null, "XLV", null)))
                .hasMessageContaining("already belongs to BDSX");
        assertThatThrownBy(() -> universe.add(new UniverseService.NewCompany("BDSX", "x", "999", "Health Care", null, "XLV", null)))
                .hasMessageContaining("already the current ticker");

        // remove: membership closes today, history kept; seeding from config does not re-add it
        universe.remove(id, null);
        assertThat(universe.companies()).extracting(c -> ((Number) c.get("id")).longValue()).doesNotContain(id);
        universe.load(false);
        assertThat(universe.isActive(id)).isFalse();
        universe.restore(id, null);
        assertThat(universe.isActive(id)).isTrue();

        universe.edit(id, new UniverseService.CompanyEdit(null, null, "MEDICAL_DIAGNOSTICS", null));
        assertThat(jdbc.sql("SELECT industry FROM company WHERE id = :id").param("id", id).query(String.class).single()).isEqualTo("MEDICAL_DIAGNOSTICS");

        universe.changeTicker(id, "BDSZ", LocalDate.now().plusDays(1));
        assertThat(tickers.companyAt("BDSX", LocalDate.now())).contains(id);
        assertThat(tickers.companyAt("BDSZ", LocalDate.now().plusDays(1))).contains(id);

        // a company with data cannot be deleted; one without can
        jdbc.sql("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (:c, 'BDSX', '2024-01-02', 1, 'test')")
                .param("c", id).update();
        assertThatThrownBy(() -> universe.delete(id)).hasMessageContaining("remove it from the universe instead");
        jdbc.sql("DELETE FROM price_bar WHERE company_id = :c").param("c", id).update();
        universe.delete(id);
        assertThat(jdbc.sql("SELECT count(*) FROM company WHERE id = :id").param("id", id).query(Integer.class).single()).isZero();
    }

    @Test
    void priceSyncStoresProviderBarsUnderTheTickerValidOnEachDate() {
        // fake provider: returns META history under today's symbol, including dates when it traded as FB
        com.civalpha.market.PriceProvider fake = new com.civalpha.market.PriceProvider() {
            public String name() { return "fake"; }
            public boolean splitAdjusted() { return false; }
            public Series fetch(String symbol, LocalDate from, LocalDate to) {
                var d1 = LocalDate.parse("2022-06-03");
                var d2 = LocalDate.parse("2022-06-10");
                var bars = java.util.List.of(
                        new com.civalpha.market.PriceBarRow(symbol, d1, null, null, null, new java.math.BigDecimal("190.78"), 1L),
                        new com.civalpha.market.PriceBarRow(symbol, d2, null, null, null, new java.math.BigDecimal("175.57"), 1L));
                var acts = java.util.List.of(new com.civalpha.market.CsvPrices.ActionRow(symbol, d2, "CASH_DIVIDEND", new java.math.BigDecimal("0.10"), null));
                return new Series(bars, acts, "[]".getBytes(), "application/json", "https://fake/" + symbol);
            }
        };
        java.util.List<String> log = new java.util.ArrayList<>();
        var summary = priceSync.sync(fake, log::add);
        assertThat(summary.failed()).isZero();
        assertThat(jdbc.sql("SELECT symbol FROM price_bar WHERE company_id = :c AND trade_date = '2022-06-03'").param("c", meta())
                .query(String.class).single()).isEqualTo("FB");
        assertThat(jdbc.sql("SELECT symbol FROM price_bar WHERE company_id = :c AND trade_date = '2022-06-10'").param("c", meta())
                .query(String.class).single()).isEqualTo("META");
        assertThat(jdbc.sql("SELECT count(*) FROM price_bar WHERE company_id IS NULL AND symbol = 'XLC' AND trade_date = '2022-06-10'")
                .query(Integer.class).single()).isEqualTo(1);
        assertThat(market.missingBenchmarks()).doesNotContain("XLC");
        // running again only re-checks the overlap window: nothing new
        assertThat(priceSync.sync(fake, log::add).inserted()).isZero();
        // never mixed into the synthetic demo
        jdbc.sql("UPDATE company SET is_demo = true WHERE id = :c").param("c", meta()).update();
        try {
            assertThatThrownBy(() -> priceSync.sync(fake, log::add)).hasMessageContaining("synthetic demo");
        } finally {
            jdbc.sql("UPDATE company SET is_demo = false WHERE id = :c").param("c", meta()).update();
        }
    }

    @Test
    void priceCorrectionsAreVersionedNotDropped() throws Exception {
        String v1 = "symbol,date,open,high,low,close,volume\nXLC,2023-03-01,60,61,59,60.00,100\nXLC,2023-03-02,60,61,59,61.00,100\n";
        var first = market.importPrices(v1.getBytes(StandardCharsets.UTF_8), "vendor-v1.csv", "vendor", false);
        assertThat(first.inserted()).isEqualTo(2);
        String v2 = "symbol,date,open,high,low,close,volume\nXLC,2023-03-01,60,61,59,60.00,100\nXLC,2023-03-02,60,61,59,61.50,120\n";
        var second = market.importPrices(v2.getBytes(StandardCharsets.UTF_8), "vendor-v2.csv", "vendor", false);
        assertThat(second.inserted()).isZero();
        assertThat(second.unchanged()).isEqualTo(1);
        assertThat(second.revised()).isEqualTo(1);
        Map<String, Object> bar = jdbc.sql("SELECT close, version FROM price_bar WHERE symbol = 'XLC' AND trade_date = '2023-03-02'").query().singleRow();
        assertThat(((java.math.BigDecimal) bar.get("close")).doubleValue()).isEqualTo(61.5);
        assertThat(bar.get("version")).isEqualTo(2);
        Map<String, Object> old = jdbc.sql("""
                SELECT r.close, r.version, d1.url AS was_from, d2.url AS replaced_by FROM price_bar_revision r
                JOIN source_document d1 ON d1.id = r.source_document_id JOIN source_document d2 ON d2.id = r.superseded_by_document_id
                WHERE r.symbol = 'XLC' AND r.trade_date = '2023-03-02'""").query().singleRow();
        assertThat(((java.math.BigDecimal) old.get("close")).doubleValue()).isEqualTo(61.0);
        assertThat(old.get("version")).isEqualTo(1);
        assertThat(old.get("was_from")).isEqualTo("file://vendor-v1.csv");
        assertThat(old.get("replaced_by")).isEqualTo("file://vendor-v2.csv");
        // re-importing the corrected file again changes nothing
        assertThat(market.importPrices(v2.getBytes(StandardCharsets.UTF_8), "vendor-v2.csv", "vendor", false).revised()).isZero();
    }

    @Test
    @SuppressWarnings("unchecked")
    void revisedFilingIsVisibleOnlyAfterItsAcceptance() throws Exception {
        Path sec = TMP.resolve("sec");
        Files.createDirectories(sec.resolve("submissions"));
        Files.createDirectories(sec.resolve("companyfacts"));
        // the amendment sits on an older paged index file, which ingestion must follow
        Files.writeString(sec.resolve("submissions/CIK0000050863.json"), """
                {"cik":"0000050863","name":"Intel","tickers":["INTC"],"filings":{"recent":{
                  "accessionNumber":["0000050863-25-000010"], "filingDate":["2025-01-30"], "reportDate":["2024-12-28"],
                  "acceptanceDateTime":["2025-01-30T21:05:00.000Z"], "form":["10-K"], "primaryDocument":[""], "items":[""]},
                  "files":[{"name":"CIK0000050863-submissions-001.json","filingCount":1,"filingFrom":"2025-03-14","filingTo":"2025-03-14"},
                           {"name":"CIK0000050863-submissions-000.json","filingCount":1,"filingFrom":"1994-01-01","filingTo":"2001-01-01"}]}}""");
        Files.writeString(sec.resolve("submissions/CIK0000050863-submissions-001.json"), """
                {"accessionNumber":["0000050863-25-000099"], "filingDate":["2025-03-14"], "reportDate":["2024-12-28"],
                 "acceptanceDateTime":["2025-03-14T20:00:00.000Z"], "form":["10-K/A"], "primaryDocument":[""], "items":[""]}""");
        Files.writeString(sec.resolve("companyfacts/CIK0000050863.json"), """
                {"cik":50863,"entityName":"Intel","facts":{"us-gaap":{"Revenues":{"units":{"USD":[
                  {"start":"2023-12-31","end":"2024-12-28","val":53100000000,"accn":"0000050863-25-000010","fy":2024,"fp":"FY","form":"10-K","filed":"2025-01-30"},
                  {"start":"2023-12-31","end":"2024-12-28","val":51500000000,"accn":"0000050863-25-000099","fy":2024,"fp":"FY","form":"10-K/A","filed":"2025-03-14"}]}}}}}""");
        long intc = tickers.companyByCik("0000050863").orElseThrow();
        List<String> log = new ArrayList<>();
        var res = filings.ingest(new FixtureSecClient(sec), intc, false, log::add);
        assertThat(res.facts()).isEqualTo(2);
        assertThat(res.filings()).isEqualTo(2);
        // the page outside the lookback window is not fetched (it does not exist; a fetch would be logged as missing)
        assertThat(log).noneMatch(l -> l.contains("submissions-000"));
        assertThat(jdbc.sql("SELECT amends_accession FROM filing WHERE form_type = '10-K/A'").query(String.class).single())
                .isEqualTo("0000050863-25-000010");

        Map<String, Object> beforeOriginal = companies.financials("INTC", OffsetDateTime.parse("2025-01-30T21:00:00Z"));
        assertThat((List<?>) beforeOriginal.get("series")).isEmpty();

        Map<String, Object> mid = companies.financials("INTC", OffsetDateTime.parse("2025-02-15T00:00:00Z"));
        Map<String, Object> p1 = ((List<Map<String, Object>>) ((List<Map<String, Object>>) mid.get("series")).get(0).get("points")).get(0);
        assertThat(p1.get("value")).isEqualTo(5.31e10);
        assertThat(p1.get("revised")).isEqualTo(false);

        Map<String, Object> after = companies.financials("INTC", OffsetDateTime.parse("2025-03-15T00:00:00Z"));
        Map<String, Object> p2 = ((List<Map<String, Object>>) ((List<Map<String, Object>>) after.get("series")).get(0).get("points")).get(0);
        assertThat(p2.get("value")).isEqualTo(5.15e10);
        assertThat(p2.get("revised")).isEqualTo(true);
        assertThat(p2.get("originalValue")).isEqualTo(5.31e10);
        assertThat(p2.get("accessionNo")).isEqualTo("0000050863-25-000099");

        // re-ingesting is idempotent
        assertThat(filings.ingest(new FixtureSecClient(sec), intc, false, log::add).facts()).isZero();
    }

    @Test
    void forecastsAreImmutableAndVersioned() {
        long mv = jdbc.sql("""
                INSERT INTO model_version (model_kind, algorithm, feature_names, trained_through, training_cutoff, n_samples, params, code_version)
                VALUES ('AUGMENTED', 'logistic_regression_l2', '[]', '2026-08-01', now(), 100, '{}', 'test') RETURNING id""").query(Long.class).single();
        Map<String, Object> p = new HashMap<>(Map.of("companyId", meta(), "symbol", "META", "benchmarkSymbol", "XLC", "modelKind", "AUGMENTED",
                "modelVersionId", mv, "probability", 0.42, "probLow", 0.38, "probHigh", 0.47, "horizonTradingDays", 21));
        p.put("asOfDate", "2026-09-30");
        p.put("asOf", "2026-09-30T21:00:00Z");
        p.put("target", "t");
        p.put("uncertaintyNote", "n");
        p.put("features", Map.of("trade_shock", -0.1));
        p.put("explanation", Map.of("factors", List.of()));
        p.put("sources", List.of());

        long v1 = forecasts.persist(p, "LIVE", "first").orElseThrow();
        assertThat(forecasts.persist(new HashMap<>(p), "LIVE", "same inputs")).isEmpty();

        Map<String, Object> changed = new HashMap<>(p);
        changed.put("probability", 0.35);
        changed.put("features", Map.of("trade_shock", -0.3));
        long v2 = forecasts.persist(changed, "LIVE", "New evidence: event #1").orElseThrow();
        Map<String, Object> row = jdbc.sql("SELECT version, supersedes_id, reason FROM forecast WHERE id = :id").param("id", v2).query().singleRow();
        assertThat(row.get("version")).isEqualTo(2);
        assertThat(((Number) row.get("supersedes_id")).longValue()).isEqualTo(v1);
        assertThat((String) row.get("reason")).contains("supersedes v1");

        assertThatThrownBy(() -> jdbc.sql("UPDATE forecast SET probability = 0.9 WHERE id = :id").param("id", v1).update())
                .hasMessageContaining("immutable");
        assertThatThrownBy(() -> jdbc.sql("DELETE FROM forecast WHERE id = :id").param("id", v1).update())
                .hasMessageContaining("immutable");
        assertThat(jdbc.sql("SELECT probability FROM forecast WHERE id = :id").param("id", v1).query(Double.class).single()).isEqualTo(0.42);
    }
}
