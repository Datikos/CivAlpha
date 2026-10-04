package com.civalpha.demo;

import com.civalpha.config.AppProperties;
import com.civalpha.events.EventDraft;
import com.civalpha.events.EventService;
import com.civalpha.events.LiveEventSources;
import com.civalpha.forecast.ForecastService;
import com.civalpha.forecast.MlClient;
import com.civalpha.macro.MacroService;
import com.civalpha.market.MarketDataService;
import com.civalpha.sec.FilingIngestionService;
import com.civalpha.sec.SecClient;
import com.civalpha.sec.SecClientFactory;
import com.civalpha.universe.UniverseService;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import java.util.stream.Stream;

/** End-to-end flows: the synthetic demo and the configured (live/credentialed) pipeline. */
@Service
public class Pipeline {

    private final AppProperties props;
    private final JdbcClient jdbc;
    private final ObjectMapper om;
    private final UniverseService universe;
    private final MarketDataService market;
    private final MacroService macro;
    private final SecClientFactory secFactory;
    private final FilingIngestionService filings;
    private final EventService events;
    private final LiveEventSources liveEvents;
    private final ForecastService forecasts;
    private final MlClient ml;

    public Pipeline(AppProperties props, JdbcClient jdbc, ObjectMapper om, UniverseService universe, MarketDataService market,
                    MacroService macro, SecClientFactory secFactory, FilingIngestionService filings, EventService events,
                    LiveEventSources liveEvents, ForecastService forecasts, MlClient ml) {
        this.props = props;
        this.jdbc = jdbc;
        this.om = om;
        this.universe = universe;
        this.market = market;
        this.macro = macro;
        this.secFactory = secFactory;
        this.filings = filings;
        this.events = events;
        this.liveEvents = liveEvents;
        this.forecasts = forecasts;
        this.ml = ml;
    }

    public boolean demoPresent() {
        return jdbc.sql("SELECT exists(SELECT 1 FROM company WHERE is_demo)").query(Boolean.class).single();
    }

    // ------------------------------------------------------------------ demo
    public void loadDemo(Consumer<String> log) throws Exception {
        boolean realData = jdbc.sql("SELECT exists(SELECT 1 FROM company WHERE NOT is_demo)").query(Boolean.class).single();
        if (realData) throw new IllegalStateException("the database already holds non-demo companies; use a separate database for the demo");
        Path dir = Path.of(props.demo().dataDir());
        log.accept("generating synthetic demo dataset in " + dir + " (ML service)");
        ml.generateDemo(dir.toString());
        log.accept("universe: " + universe.load(true) + " companies created");
        var pr = market.importPrices(Files.readAllBytes(dir.resolve("prices.csv")), "demo/prices.csv", "DEMO synthetic CSV", true);
        log.accept("prices: %d rows, %d inserted, %d unchanged, %d corrected, unknown symbols %s".formatted(pr.rows(), pr.inserted(), pr.unchanged(), pr.revised(), pr.unknownSymbols()));
        log.accept("corporate actions: " + market.importActions(Files.readAllBytes(dir.resolve("corporate_actions.csv")), "demo/corporate_actions.csv", "DEMO synthetic CSV", true));
        for (JsonNode s : om.readTree(Files.readAllBytes(dir.resolve("macro/series.json")))) {
            macro.upsertSeries(s.path("series_id").asString(), s.path("title").asString(), s.path("units").asString(), s.path("frequency").asString(), "DEMO synthetic");
        }
        log.accept("macro observations (vintages): " + macro.insert(MacroService.parseCsv(Files.readString(dir.resolve("macro/observations.csv"))), true));
        SecClient sec = secFactory.fixture(dir.resolve("sec"));
        for (Map<String, Object> c : universe.companies()) {
            filings.ingest(sec, ((Number) c.get("id")).longValue(), true, log);
        }
        loadDemoEvents(dir.resolve("events"), log);
        afterIngest(log, true);
    }

    private void loadDemoEvents(Path dir, Consumer<String> log) throws Exception {
        JsonNode root = om.readTree(Files.readAllBytes(dir.resolve("events.json")));
        for (JsonNode a : root.path("actors")) {
            events.upsertActor(a.path("name").asString(), a.path("actor_type").asString(), a.path("authority").asString(null), null, a.path("profile_note").asString(null));
        }
        int created = 0, merged = 0;
        for (JsonNode e : root.path("events")) {
            JsonNode s = e.path("sources").path(0);
            var r = events.ingest(draft(dir, e, s), true);
            if (r.created()) created++;
            else merged++;
        }
        for (JsonNode n : root.path("news")) {
            var r = events.ingest(draft(dir, n, n.path("source")), true);
            log.accept("news item '%s' -> %s event %d".formatted(n.path("title").asString(), r.deduplicated() ? "merged into existing" : "new NEWS_ONLY", r.eventId()));
        }
        log.accept("events: %d created, %d merged".formatted(created, merged));
    }

    @SuppressWarnings("unchecked")
    private EventDraft draft(Path dir, JsonNode e, JsonNode s) throws Exception {
        List<EventDraft.Target> targets = new ArrayList<>();
        for (JsonNode t : e.path("targets")) {
            targets.add(new EventDraft.Target(t.path("target_type").asString(), t.path("target_code").asString(),
                    t.path("magnitude").isNumber() ? t.path("magnitude").asDouble() : null));
        }
        Map<String, Object> attrs = e.has("attributes") ? om.convertValue(e.path("attributes"), HashMap.class) : Map.of();
        String doc = s.path("doc").asString();
        OffsetDateTime pub = OffsetDateTime.parse(e.path("published_at").asString());
        return new EventDraft(e.path("category").asString(), e.path("event_type").asString(), e.path("title").asString(),
                e.path("summary").asString(null), LocalDate.parse(e.path("event_date").asString()), pub,
                e.path("actor").asString(null), attrs, targets,
                new EventDraft.Source("demo://events/" + doc, s.path("title").asString(), s.path("publisher").asString(),
                        s.path("role").asString(), pub, Files.readAllBytes(dir.resolve(doc)), "text/html", true));
    }

    // ------------------------------------------------------------------ configured pipeline
    public void runConfigured(Consumer<String> log) throws Exception {
        log.accept("universe: " + universe.load(false) + " companies created");
        Path imports = Path.of(props.storage().importsDir());
        if (Files.isDirectory(imports)) {
            try (Stream<Path> files = Files.list(imports)) {
                for (Path f : files.sorted().toList()) {
                    String name = f.getFileName().toString();
                    if (name.startsWith("prices") && name.endsWith(".csv")) {
                        var r = market.importPrices(Files.readAllBytes(f), name, "CSV import", false);
                        log.accept("%s: %d inserted, %d corrected, unknown %s".formatted(name, r.inserted(), r.revised(), r.unknownSymbols()));
                    } else if (name.startsWith("corporate_actions") && name.endsWith(".csv")) {
                        log.accept(name + ": " + market.importActions(Files.readAllBytes(f), name, "CSV import", false) + " actions");
                    }
                }
            }
        } else {
            log.accept("no imports directory " + imports + " (put prices*.csv / corporate_actions*.csv there)");
        }
        if (props.sec().live()) {
            SecClient sec = secFactory.configured();
            for (Map<String, Object> c : universe.companies()) {
                try {
                    filings.ingest(sec, ((Number) c.get("id")).longValue(), false, log);
                } catch (RuntimeException e) {
                    log.accept("SEC ingest failed for company " + c.get("id") + ": " + e.getMessage());
                }
            }
        } else {
            log.accept("SEC mode is '" + props.sec().mode() + "'; set CIVALPHA_SEC_MODE=live and SEC_USER_AGENT to fetch EDGAR");
        }
        if (props.fred().enabled()) {
            for (String s : props.fred().series()) log.accept("FRED " + s + ": " + macro.fetchFred(s.trim()) + " observations");
        } else {
            log.accept("FRED_API_KEY not set; macro series skipped");
        }
        for (EventDraft d : liveEvents.collect(log)) {
            var r = events.ingest(d, false);
            if (r.created()) log.accept("event " + r.eventId() + ": " + d.title());
        }
        afterIngest(log, false);
    }

    private void afterIngest(Consumer<String> log, boolean demo) {
        if (market.latestBenchmarkDate().isEmpty()) {
            log.accept("no benchmark prices; skipping evaluation and forecasts");
            return;
        }
        log.accept("walk-forward evaluation: " + ml.evaluate().get("verdict"));
        if (demo) {
            List<LocalDate> dates = replayDates(props.demo().replayMonths());
            var r = forecasts.issueReplay(dates, "Demo replay: issued with data available at each historical cutoff");
            log.accept("replayed forecasts for %d month-end dates: %d created".formatted(dates.size(), r.created()));
        }
        var live = forecasts.issueLive(null, "Scheduled issue");
        log.accept("live forecasts: %d created, %d unchanged".formatted(live.created(), live.unchanged()));
        log.accept("outcomes: " + ml.resolveOutcomes());
    }

    /** Last trading day of each of the previous N months (from benchmark bars). */
    List<LocalDate> replayDates(int months) {
        return jdbc.sql("""
                SELECT max(trade_date) FROM price_bar WHERE company_id IS NULL
                GROUP BY date_trunc('month', trade_date) ORDER BY 1 DESC LIMIT :n""")
                .param("n", months + 1).query(LocalDate.class).list().stream().skip(1).sorted().toList();
    }
}
