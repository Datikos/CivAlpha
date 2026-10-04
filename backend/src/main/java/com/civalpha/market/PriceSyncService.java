package com.civalpha.market;

import com.civalpha.config.AppProperties;
import com.civalpha.storage.DocumentStore;
import com.civalpha.storage.SourceDocument;
import com.civalpha.universe.TickerResolver;
import com.civalpha.universe.UniverseService;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import java.net.http.HttpClient;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Downloads daily bars and corporate actions for every active universe member and every benchmark ETF from
 * the configured provider. Incremental: each symbol resumes a few days before its last stored bar (so late
 * corrections are picked up and versioned); symbols without prices start at `history-start`.
 */
@Service
public class PriceSyncService {

    private static final ZoneId NEW_YORK = ZoneId.of("America/New_York");
    private static final int OVERLAP_DAYS = 7;

    private final AppProperties props;
    private final HttpClient http;
    private final ObjectMapper om;
    private final JdbcClient jdbc;
    private final MarketDataService market;
    private final DocumentStore docs;
    private final UniverseService universe;
    private final TickerResolver tickers;

    public PriceSyncService(AppProperties props, HttpClient http, ObjectMapper om, JdbcClient jdbc, MarketDataService market,
                            DocumentStore docs, UniverseService universe, TickerResolver tickers) {
        this.props = props;
        this.http = http;
        this.om = om;
        this.jdbc = jdbc;
        this.market = market;
        this.docs = docs;
        this.universe = universe;
        this.tickers = tickers;
    }

    public boolean enabled() {
        return props.prices() != null && props.prices().enabled();
    }

    public String providerName() {
        return enabled() ? props.prices().provider().toLowerCase() : "none";
    }

    PriceProvider provider() {
        if (!enabled()) {
            throw new IllegalStateException("No price provider is configured. Set CIVALPHA_PRICE_PROVIDER=yahoo (no key; unofficial, "
                    + "personal research only) or CIVALPHA_PRICE_PROVIDER=tiingo with TIINGO_API_KEY in .env, then restart; "
                    + "or load the demo dataset / import a prices CSV.");
        }
        return switch (props.prices().provider().toLowerCase()) {
            case "yahoo" -> new YahooChartProvider(http, om);
            case "tiingo" -> new TiingoProvider(http, om, props.prices().tiingoApiKey());
            default -> throw new IllegalStateException("unknown CIVALPHA_PRICE_PROVIDER '" + props.prices().provider() + "' (use yahoo or tiingo)");
        };
    }

    public record Target(Long companyId, String symbol) {}

    /** Active members (current ticker) plus all benchmark ETFs. */
    public List<Target> targets() {
        List<Target> out = new ArrayList<>();
        for (Map<String, Object> c : universe.companies()) {
            long id = ((Number) c.get("id")).longValue();
            out.add(new Target(id, tickers.currentSymbol(id)));
        }
        for (String b : universe.benchmarkSymbols()) out.add(new Target(null, b));
        return out;
    }

    public record Summary(int symbols, int failed, int inserted, int corrected) {}

    public Summary sync(Consumer<String> log) {
        return sync(provider(), log);
    }

    public Summary sync(PriceProvider p, Consumer<String> log) {
        if (jdbc.sql("SELECT exists(SELECT 1 FROM company WHERE is_demo)").query(Boolean.class).single()) {
            throw new IllegalStateException("This database holds the synthetic demo; real prices are never mixed into it. "
                    + "Start from an empty database (docker compose down -v && docker compose up -d), then use Run pipeline or Update prices.");
        }
        LocalDate to = lastCompletedSession();
        LocalDate start = props.prices().historyStart() == null ? LocalDate.of(2019, 1, 2) : props.prices().historyStart();
        int failed = 0, inserted = 0, corrected = 0, current = 0;
        String rateLimit = null;
        List<Target> targets = targets();
        log.accept("price sync from " + p.name() + " for " + targets.size() + " symbols through " + to);
        for (int i = 0; i < targets.size(); i++) {
            Target t = targets.get(i);
            try {
                LocalDate last = lastBar(t);
                if (last != null && !last.isBefore(latestWeekday(to))) {
                    current++; // already has the latest completed session: no request needed
                    continue;
                }
                LocalDate from = last == null ? start : last.minusDays(OVERLAP_DAYS);
                if (from.isAfter(to)) continue;
                PriceProvider.Series s = p.fetch(t.symbol(), from, to);
                if (p.splitAdjusted() && last != null && s.actions().stream().anyMatch(a -> "SPLIT".equals(a.type()) && a.exDate().isAfter(last))) {
                    // a new split re-scales the provider's whole history: re-download so stored bars stay consistent
                    log.accept(t.symbol() + ": new split detected; re-downloading full history");
                    s = p.fetch(t.symbol(), start, to);
                }
                List<PriceBarRow> bars = s.bars().stream().filter(b -> !b.date().isAfter(to) && b.close() != null).toList();
                SourceDocument d = docs.store(new DocumentStore.NewDocument("PRICE_FILE", p.name(), s.locator(), null,
                        t.symbol() + " daily prices " + from + ".." + to, null, s.contentType(), s.raw(), false));
                // store each bar under the ticker valid on its date (vendors report renamed stocks under today's symbol)
                List<TickerResolver.Span> spans = t.companyId() == null ? List.of()
                        : tickers.allSpans().stream().filter(sp -> sp.companyId() == t.companyId()).toList();
                List<MarketDataService.Resolved> resolved = bars.stream().map(b -> new MarketDataService.Resolved(t.companyId(),
                        spans.isEmpty() ? b : new PriceBarRow(symbolOn(spans, b.date(), b.symbol()), b.date(), b.open(), b.high(), b.low(), b.close(), b.volume())))
                        .toList();
                var r = market.store(resolved, p.name(), d.id(), false);
                List<CsvPrices.ActionRow> actions = s.actions().stream()
                        .map(a -> p.splitAdjusted() && "SPLIT".equals(a.type())
                                ? new CsvPrices.ActionRow(a.symbol(), a.exDate(), "SPLIT_INFO", a.value(), a.announcedAt()) : a)
                        .toList();
                int acts = market.storeActions(t.companyId(), actions, p.name(), d.id(), false);
                inserted += r.inserted();
                corrected += r.revised();
                log.accept("%s: %d new bars, %d corrected, %d corporate actions".formatted(t.symbol(), r.inserted(), r.revised(), acts));
                Thread.sleep(300); // be gentle with free endpoints
            } catch (PriceProvider.RateLimited e) {
                rateLimit = e.getMessage();
                int left = targets.size() - i;
                failed += left;
                log.accept("%s: %s. Stopping here so the remaining %d symbols do not use up more quota; they update on the next run."
                        .formatted(t.symbol(), e.getMessage(), left));
                break;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("price sync interrupted", e);
            } catch (Exception e) {
                failed++;
                log.accept(t.symbol() + ": FAILED " + e.getMessage());
            }
        }
        log.accept("price sync done: %d symbols, %d already current, %d failed, %d new bars, %d corrected"
                .formatted(targets.size(), current, failed, inserted, corrected));
        if (rateLimit != null && failed + current == targets.size() && inserted == 0) {
            throw new IllegalStateException(rateLimit + "; no prices were updated. Try again later (an hour on the free plan).");
        }
        if (failed == targets.size() && !targets.isEmpty()) {
            throw new IllegalStateException("price sync failed for every symbol; check network access and the provider settings");
        }
        return new Summary(targets.size(), failed, inserted, corrected);
    }

    static String symbolOn(List<TickerResolver.Span> spans, LocalDate date, String fallback) {
        return spans.stream().filter(sp -> !sp.validFrom().isAfter(date) && (sp.validTo() == null || sp.validTo().isAfter(date)))
                .map(TickerResolver.Span::symbol).findFirst().orElse(fallback);
    }

    private LocalDate lastBar(Target t) {
        return t.companyId() == null
                ? jdbc.sql("SELECT max(trade_date) FROM price_bar WHERE company_id IS NULL AND symbol = :s").param("s", t.symbol()).query(LocalDate.class).optional().orElse(null)
                : jdbc.sql("SELECT max(trade_date) FROM price_bar WHERE company_id = :c").param("c", t.companyId()).query(LocalDate.class).optional().orElse(null);
    }

    /** Today if the US session has closed (after 16:30 New York), otherwise yesterday: partial bars are never stored. */
    static LocalDate lastCompletedSession() {
        return lastCompletedSession(ZonedDateTime.now(NEW_YORK));
    }

    static LocalDate lastCompletedSession(ZonedDateTime nyNow) {
        return nyNow.toLocalTime().isBefore(LocalTime.of(16, 30)) ? nyNow.toLocalDate().minusDays(1) : nyNow.toLocalDate();
    }

    /** The last Monday-Friday on or before `d`: a Friday bar is current all weekend (holidays still cost one request). */
    static LocalDate latestWeekday(LocalDate d) {
        while (d.getDayOfWeek() == java.time.DayOfWeek.SATURDAY || d.getDayOfWeek() == java.time.DayOfWeek.SUNDAY) d = d.minusDays(1);
        return d;
    }
}
