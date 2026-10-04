package com.civalpha.api;

import com.civalpha.demo.Pipeline;
import com.civalpha.forecast.ForecastService;
import com.civalpha.forecast.MlClient;
import com.civalpha.jobs.JobService;
import com.civalpha.market.MarketDataService;
import com.civalpha.sec.FilingIngestionService;
import com.civalpha.sec.SecClientFactory;
import com.civalpha.universe.TickerResolver;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/admin")
public class AdminController {

    private final JobService jobs;
    private final Pipeline pipeline;
    private final ForecastService forecasts;
    private final MlClient ml;
    private final MarketDataService market;
    private final FilingIngestionService filings;
    private final SecClientFactory sec;
    private final TickerResolver tickers;
    private final com.civalpha.universe.UniverseService universe;
    private final Rows rows;

    public AdminController(JobService jobs, Pipeline pipeline, ForecastService forecasts, MlClient ml, MarketDataService market,
                           FilingIngestionService filings, SecClientFactory sec, TickerResolver tickers,
                           com.civalpha.universe.UniverseService universe, Rows rows) {
        this.jobs = jobs;
        this.pipeline = pipeline;
        this.forecasts = forecasts;
        this.ml = ml;
        this.market = market;
        this.filings = filings;
        this.sec = sec;
        this.tickers = tickers;
        this.universe = universe;
        this.rows = rows;
    }

    @GetMapping("/jobs")
    public List<Map<String, Object>> jobs() {
        return rows.camel(jobs.recent());
    }

    @PostMapping("/demo/load")
    public Map<String, Object> demo() {
        return rows.camel(jobs.submit("DEMO_LOAD", Map.of(), pipeline::loadDemo));
    }

    @PostMapping("/pipeline/run")
    public Map<String, Object> run() {
        return rows.camel(jobs.submit("PIPELINE_RUN", Map.of(), pipeline::runConfigured));
    }

    @PostMapping("/evaluate")
    public Map<String, Object> evaluate() {
        return rows.camel(jobs.submit("EVALUATE", Map.of(), log -> {
            market.requireBenchmarks().ifPresent(log);
            log.accept(String.valueOf(ml.evaluate().get("verdict")));
        }));
    }

    public record IssueIn(LocalDate asOfDate) {}

    @PostMapping("/forecasts/issue")
    public Map<String, Object> issue(@RequestBody(required = false) IssueIn in) {
        LocalDate d = in == null ? null : in.asOfDate();
        return rows.camel(jobs.submit("ISSUE_FORECASTS", d == null ? Map.of() : Map.of("asOfDate", d.toString()), log -> {
            market.requireBenchmarks().ifPresent(log);
            var r = d == null ? forecasts.issueLive(null, "Manual issue") : forecasts.issueAt(d, "Manual issue for " + d);
            log.accept("%d created, %d unchanged".formatted(r.created(), r.unchanged()));
        }));
    }

    @PostMapping("/outcomes/resolve")
    public Map<String, Object> resolve() {
        return rows.camel(jobs.submit("RESOLVE_OUTCOMES", Map.of(), log -> log.accept(String.valueOf(ml.resolveOutcomes()))));
    }

    public record SecIn(String symbol) {}

    @PostMapping("/sec/ingest")
    public Map<String, Object> secIngest(@RequestBody SecIn in) {
        long id = tickers.companyEver(in.symbol()).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "unknown symbol"));
        return rows.camel(jobs.submit("SEC_INGEST", Map.of("symbol", in.symbol()), log -> filings.ingest(sec.configured(), id, false, log)));
    }

    @PostMapping("/prices/import")
    public Map<String, Object> importPrices(@RequestParam("file") MultipartFile file, @RequestParam(defaultValue = "CSV upload") String provider) throws Exception {
        byte[] bytes = file.getBytes();
        String name = file.getOriginalFilename() == null ? "upload.csv" : file.getOriginalFilename();
        return rows.camel(jobs.submit("PRICE_IMPORT", Map.of("file", name), log -> {
            if (universe.companies().isEmpty()) {
                // symbols resolve through the configured universe; on a fresh database load it first
                log.accept("universe: " + universe.load(false) + " companies created from config/universe.yml");
            }
            var r = market.importPrices(bytes, name, provider, false);
            log.accept("%d rows, %d inserted, %d unchanged, %d corrected (previous values archived), unknown symbols %s"
                    .formatted(r.rows(), r.inserted(), r.unchanged(), r.revised(), r.unknownSymbols()));
            var missing = market.missingBenchmarks();
            if (!missing.isEmpty()) {
                log.accept("Note: no prices yet for benchmark ETF(s) " + String.join(", ", missing)
                        + "; import them too before evaluating or issuing forecasts.");
            }
        }));
    }
}
