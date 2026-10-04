package com.civalpha.api;

import com.civalpha.exposure.Vocabulary;
import com.civalpha.jobs.JobService;
import com.civalpha.sec.FilingIngestionService;
import com.civalpha.sec.SecClientFactory;
import com.civalpha.sec.SecTickerLookup;
import com.civalpha.universe.UniverseService;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/** Manage the research universe: add, edit, remove/restore, change ticker, delete unused companies. */
@RestController
@RequestMapping("/api/admin/universe")
public class UniverseAdminController {

    private final JdbcClient jdbc;
    private final Rows rows;
    private final UniverseService universe;
    private final SecTickerLookup lookup;
    private final JobService jobs;
    private final FilingIngestionService filings;
    private final SecClientFactory sec;

    public UniverseAdminController(JdbcClient jdbc, Rows rows, UniverseService universe, SecTickerLookup lookup, JobService jobs,
                                   FilingIngestionService filings, SecClientFactory sec) {
        this.jdbc = jdbc;
        this.rows = rows;
        this.universe = universe;
        this.lookup = lookup;
        this.jobs = jobs;
        this.filings = filings;
        this.sec = sec;
    }

    @GetMapping
    public Map<String, Object> list() {
        List<Map<String, Object>> companies = rows.camel(jdbc.sql("""
                SELECT c.id,
                       (SELECT symbol FROM ticker_history t WHERE t.company_id = c.id ORDER BY valid_from DESC LIMIT 1) AS symbol,
                       (SELECT string_agg(symbol, ', ' ORDER BY valid_from) FROM ticker_history t
                         WHERE t.company_id = c.id AND t.valid_to IS NOT NULL) AS former_symbols,
                       (SELECT cik FROM cik_mapping m WHERE m.company_id = c.id AND m.valid_to IS NULL LIMIT 1) AS cik,
                       c.name, c.sector, c.industry, c.benchmark_symbol, c.is_demo,
                       EXISTS (SELECT 1 FROM universe_membership m WHERE m.company_id = c.id AND m.valid_to IS NULL) AS active,
                       (SELECT max(valid_from) FROM universe_membership m WHERE m.company_id = c.id) AS member_since,
                       (SELECT max(valid_to) FROM universe_membership m WHERE m.company_id = c.id) AS removed_on,
                       (SELECT count(*) FROM price_bar p WHERE p.company_id = c.id) AS price_count,
                       (SELECT max(trade_date) FROM price_bar p WHERE p.company_id = c.id) AS last_price_date,
                       (SELECT count(*) FROM filing f WHERE f.company_id = c.id) AS filing_count,
                       (SELECT count(*) FROM forecast f WHERE f.company_id = c.id) AS forecast_count
                FROM company c ORDER BY active DESC, symbol""").query().listOfRows());
        for (Map<String, Object> c : companies) {
            if (Boolean.TRUE.equals(c.get("active"))) c.put("removedOn", null);
            boolean hasData = ((Number) c.get("priceCount")).longValue() + ((Number) c.get("filingCount")).longValue()
                    + ((Number) c.get("forecastCount")).longValue() > 0;
            c.put("deletable", !hasData);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("universe", universe.universeName());
        out.put("companies", companies);
        out.put("benchmarks", universe.benchmarkSymbols());
        out.put("sectors", new TreeSet<>(jdbc.sql("SELECT DISTINCT sector FROM company").query(String.class).list()));
        TreeSet<String> industries = new TreeSet<>(jdbc.sql("SELECT DISTINCT industry FROM company WHERE industry IS NOT NULL").query(String.class).list());
        industries.addAll(List.of("SEMICONDUCTORS", "SEMICONDUCTOR_EQUIPMENT", "CONSUMER_ELECTRONICS", "NETWORKING_HARDWARE", "AUTOS"));
        out.put("industries", industries);
        out.put("productIndustries", industries.stream().filter(i -> Vocabulary.productForIndustry(i).isPresent()).toList());
        return out;
    }

    /** CIK and registrant name for a ticker from SEC company_tickers.json. */
    @GetMapping("/lookup")
    public SecTickerLookup.Match lookup(@RequestParam String symbol) {
        try {
            return lookup.lookup(symbol).orElseThrow(() -> new java.util.NoSuchElementException(
                    symbol.toUpperCase() + " is not in SEC company_tickers.json (in fixture mode only the demo companies are known); enter the CIK manually"));
        } catch (IllegalStateException e) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "SEC lookup unavailable: " + e.getMessage());
        }
    }

    public record AddIn(String symbol, String name, String cik, String sector, String industry, String benchmarkSymbol,
                        LocalDate memberSince, Boolean ingestSec) {}

    @PostMapping("/companies")
    public Map<String, Object> add(@RequestBody AddIn in) {
        long id = universe.add(new UniverseService.NewCompany(in.symbol(), in.name(), in.cik(), in.sector(), in.industry(),
                in.benchmarkSymbol(), in.memberSince()));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        out.put("symbol", in.symbol().toUpperCase().trim());
        List<String> next = new ArrayList<>();
        next.add("Import daily prices for " + in.symbol().toUpperCase().trim() + " (at least ~6 months of history is needed for features).");
        if (!universe.benchmarkSymbols().isEmpty() && jdbc.sql("SELECT count(*) FROM price_bar WHERE company_id IS NULL AND symbol = :b")
                .param("b", in.benchmarkSymbol().toUpperCase().trim()).query(Integer.class).single() == 0) {
            next.add("Import prices for benchmark " + in.benchmarkSymbol().toUpperCase().trim() + " too (none loaded yet).");
        }
        if (Boolean.TRUE.equals(in.ingestSec())) {
            out.put("job", rows.camel(jobs.submit("SEC_INGEST", Map.of("symbol", in.symbol()), log -> filings.ingest(sec.configured(), id, false, log))));
        } else {
            next.add("Ingest SEC filings (Data & pipeline → SEC ingest) so fundamentals and exposures are available.");
        }
        out.put("nextSteps", next);
        return out;
    }

    public record EditIn(String name, String sector, String industry, String benchmarkSymbol) {}

    @PutMapping("/companies/{id}")
    public Map<String, Object> edit(@PathVariable long id, @RequestBody EditIn in) {
        universe.edit(id, new UniverseService.CompanyEdit(in.name(), in.sector(), in.industry(), in.benchmarkSymbol()));
        return Map.of("id", id, "updated", true);
    }

    public record DateIn(LocalDate effectiveDate) {}

    @PostMapping("/companies/{id}/remove")
    public Map<String, Object> remove(@PathVariable long id, @RequestBody(required = false) DateIn in) {
        universe.remove(id, in == null ? null : in.effectiveDate());
        return Map.of("id", id, "active", false);
    }

    @PostMapping("/companies/{id}/restore")
    public Map<String, Object> restore(@PathVariable long id, @RequestBody(required = false) DateIn in) {
        universe.restore(id, in == null ? null : in.effectiveDate());
        return Map.of("id", id, "active", true);
    }

    public record TickerIn(String symbol, LocalDate effectiveDate) {}

    @PostMapping("/companies/{id}/ticker")
    public Map<String, Object> ticker(@PathVariable long id, @RequestBody TickerIn in) {
        universe.changeTicker(id, in.symbol(), in.effectiveDate());
        return Map.of("id", id, "symbol", in.symbol().toUpperCase().trim());
    }

    @DeleteMapping("/companies/{id}")
    public Map<String, Object> delete(@PathVariable long id) {
        universe.delete(id);
        return Map.of("id", id, "deleted", true);
    }

    @PostMapping("/seed")
    public Map<String, Object> seed() {
        return Map.of("created", universe.load(false));
    }
}
