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
    @SuppressWarnings("unchecked")
    void revisedFilingIsVisibleOnlyAfterItsAcceptance() throws Exception {
        Path sec = TMP.resolve("sec");
        Files.createDirectories(sec.resolve("submissions"));
        Files.createDirectories(sec.resolve("companyfacts"));
        Files.writeString(sec.resolve("submissions/CIK0000050863.json"), """
                {"cik":"0000050863","name":"Intel","tickers":["INTC"],"filings":{"recent":{
                  "accessionNumber":["0000050863-25-000010","0000050863-25-000099"],
                  "filingDate":["2025-01-30","2025-03-14"], "reportDate":["2024-12-28","2024-12-28"],
                  "acceptanceDateTime":["2025-01-30T21:05:00.000Z","2025-03-14T20:00:00.000Z"],
                  "form":["10-K","10-K/A"], "primaryDocument":["",""], "items":["",""]}}}""");
        Files.writeString(sec.resolve("companyfacts/CIK0000050863.json"), """
                {"cik":50863,"entityName":"Intel","facts":{"us-gaap":{"Revenues":{"units":{"USD":[
                  {"start":"2023-12-31","end":"2024-12-28","val":53100000000,"accn":"0000050863-25-000010","fy":2024,"fp":"FY","form":"10-K","filed":"2025-01-30"},
                  {"start":"2023-12-31","end":"2024-12-28","val":51500000000,"accn":"0000050863-25-000099","fy":2024,"fp":"FY","form":"10-K/A","filed":"2025-03-14"}]}}}}}""");
        long intc = tickers.companyByCik("0000050863").orElseThrow();
        List<String> log = new ArrayList<>();
        var res = filings.ingest(new FixtureSecClient(sec), intc, false, log::add);
        assertThat(res.facts()).isEqualTo(2);
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
