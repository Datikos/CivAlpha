package com.civalpha.universe;

import com.civalpha.config.AppProperties;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;

/**
 * The research universe lives in the database (company, ticker_history, cik_mapping, universe_membership).
 * config/universe.yml only seeds companies whose CIK is not in the database yet; after that, additions,
 * removals and edits made through the API are authoritative and are never undone by a pipeline run.
 *
 * Membership is time-ranged [valid_from, valid_to): removing a stock closes its span, so past forecasts,
 * backtests and point-in-time features still see it as a member for the dates it was one.
 */
@Service
public class UniverseService {

    private static final LocalDate EPOCH = LocalDate.of(1990, 1, 1);
    private final JdbcClient jdbc;
    private final AppProperties props;
    private final TickerResolver tickers;

    public UniverseService(JdbcClient jdbc, AppProperties props, TickerResolver tickers) {
        this.jdbc = jdbc;
        this.props = props;
        this.tickers = tickers;
    }

    public UniverseConfig config() {
        try {
            return UniverseConfig.load(Path.of(props.universeFile()));
        } catch (IOException e) {
            throw new IllegalStateException("cannot read universe file " + props.universeFile(), e);
        }
    }

    /** Universe name used for membership rows (from the config file; "default" without one). */
    public String universeName() {
        Path p = Path.of(props.universeFile());
        return Files.isRegularFile(p) ? config().universe() : "default";
    }

    /** Benchmark ETFs: those configured plus any benchmark assigned to a company in the database. */
    public Set<String> benchmarkSymbols() {
        Set<String> out = new TreeSet<>(jdbc.sql("SELECT DISTINCT benchmark_symbol FROM company").query(String.class).list());
        if (Files.isRegularFile(Path.of(props.universeFile()))) out.addAll(config().benchmarks().keySet());
        return out;
    }

    /**
     * Seeds companies from config/universe.yml whose CIK is not in the database yet. Existing companies are
     * left exactly as managed through the API (including removed ones). Returns the number created.
     */
    @Transactional
    public int load(boolean demo) {
        UniverseConfig cfg = config();
        int created = 0;
        for (UniverseConfig.Entry e : cfg.companies()) {
            String cik = TickerResolver.padCik(e.cik());
            if (tickers.companyByCik(cik).isPresent()) continue;
            List<UniverseConfig.TickerSpan> hist = e.tickerHistory().isEmpty()
                    ? List.of(new UniverseConfig.TickerSpan(e.symbol(), EPOCH, null)) : e.tickerHistory();
            create(e.name(), cik, e.sector(), e.industry(), e.benchmark(), hist, cfg.startDate(), demo, "universe config");
            created++;
        }
        return created;
    }

    // ------------------------------------------------------------------ management

    public record NewCompany(String symbol, String name, String cik, String sector, String industry, String benchmarkSymbol,
                             LocalDate memberSince) {}

    @Transactional
    public long add(NewCompany c) {
        String symbol = required(c.symbol(), "symbol").toUpperCase().trim();
        if (!symbol.matches("[A-Z0-9.\\-]{1,10}")) throw new IllegalArgumentException("symbol must be 1-10 letters, digits, '.' or '-'");
        String cik = TickerResolver.padCik(required(c.cik(), "cik"));
        if (cik.equals("0000000000") || cik.length() != 10) throw new IllegalArgumentException("cik must be the SEC Central Index Key (up to 10 digits)");
        String benchmark = required(c.benchmarkSymbol(), "benchmarkSymbol").toUpperCase().trim();
        LocalDate today = LocalDate.now();
        LocalDate since = c.memberSince() == null ? today : c.memberSince();
        if (since.isAfter(today)) throw new IllegalArgumentException("memberSince cannot be in the future");
        Optional<Long> byCik = tickers.companyByCik(cik);
        if (byCik.isPresent()) {
            throw new IllegalArgumentException("CIK " + cik + " already belongs to " + tickers.currentSymbol(byCik.get())
                    + " (company " + byCik.get() + "); restore or edit it instead");
        }
        Optional<Long> bySymbol = tickers.companyAt(symbol, today);
        if (bySymbol.isPresent()) {
            throw new IllegalArgumentException(symbol + " is already the current ticker of company " + bySymbol.get());
        }
        return create(required(c.name(), "name").trim(), cik, required(c.sector(), "sector").trim(), blankToNull(c.industry()),
                benchmark, List.of(new UniverseConfig.TickerSpan(symbol, EPOCH, null)), since, false, "manual");
    }

    private long create(String name, String cik, String sector, String industry, String benchmark,
                        List<UniverseConfig.TickerSpan> tickerSpans, LocalDate memberSince, boolean demo, String source) {
        long id = jdbc.sql("""
                INSERT INTO company (name, sector, industry, benchmark_symbol, is_demo)
                VALUES (:n, :s, :i, :b, :demo) RETURNING id""")
                .param("n", name).param("s", sector).param("i", industry).param("b", benchmark).param("demo", demo)
                .query(Long.class).single();
        jdbc.sql("INSERT INTO cik_mapping (company_id, cik, valid_from, source) VALUES (:c, :cik, :f, :src)")
                .param("c", id).param("cik", cik).param("f", EPOCH).param("src", source).update();
        for (UniverseConfig.TickerSpan t : tickerSpans) {
            jdbc.sql("INSERT INTO ticker_history (company_id, symbol, valid_from, valid_to, source) VALUES (:c, :s, :f, :t, :src)")
                    .param("c", id).param("s", t.symbol().toUpperCase()).param("f", t.validFrom()).param("t", t.validTo())
                    .param("src", source).update();
        }
        jdbc.sql("INSERT INTO universe_membership (universe, company_id, valid_from) VALUES (:u, :c, :f)")
                .param("u", universeName()).param("c", id).param("f", memberSince).update();
        return id;
    }

    public record CompanyEdit(String name, String sector, String industry, String benchmarkSymbol) {}

    @Transactional
    public void edit(long companyId, CompanyEdit e) {
        requireCompany(companyId);
        jdbc.sql("""
                UPDATE company SET name = coalesce(:n, name), sector = coalesce(:s, sector), industry = coalesce(:i, industry),
                       benchmark_symbol = coalesce(:b, benchmark_symbol) WHERE id = :id""")
                .param("n", blankToNull(e.name())).param("s", blankToNull(e.sector())).param("i", blankToNull(e.industry()))
                .param("b", e.benchmarkSymbol() == null || e.benchmarkSymbol().isBlank() ? null : e.benchmarkSymbol().toUpperCase().trim())
                .param("id", companyId).update();
    }

    /** Ends membership on `effective` (exclusive): the stock is no longer a member from that date on. */
    @Transactional
    public void remove(long companyId, LocalDate effective) {
        requireCompany(companyId);
        LocalDate on = effective == null ? LocalDate.now() : effective;
        int n = jdbc.sql("""
                UPDATE universe_membership SET valid_to = :d
                WHERE company_id = :c AND universe = :u AND valid_to IS NULL AND valid_from < :d""")
                .param("d", on).param("c", companyId).param("u", universeName()).update();
        if (n == 0) {
            // a span that starts on/after the removal date never took effect
            int pending = jdbc.sql("DELETE FROM universe_membership WHERE company_id = :c AND universe = :u AND valid_to IS NULL AND valid_from >= :d")
                    .param("c", companyId).param("u", universeName()).param("d", on).update();
            if (pending == 0) throw new IllegalArgumentException("company " + companyId + " is not an active member");
        }
    }

    /** Opens a new membership span from `effective` (removal history is kept). */
    @Transactional
    public void restore(long companyId, LocalDate effective) {
        requireCompany(companyId);
        if (isActive(companyId)) throw new IllegalArgumentException("company " + companyId + " is already an active member");
        LocalDate on = effective == null ? LocalDate.now() : effective;
        LocalDate lastEnd = jdbc.sql("SELECT max(valid_to) FROM universe_membership WHERE company_id = :c AND universe = :u")
                .param("c", companyId).param("u", universeName()).query(LocalDate.class).optional().orElse(null);
        if (lastEnd != null && on.isBefore(lastEnd)) throw new IllegalArgumentException("restore date must be on or after " + lastEnd);
        jdbc.sql("INSERT INTO universe_membership (universe, company_id, valid_from) VALUES (:u, :c, :f)")
                .param("u", universeName()).param("c", companyId).param("f", on).update();
    }

    /** Records a ticker change effective on a date; earlier data stays attached under the old symbol. */
    @Transactional
    public void changeTicker(long companyId, String newSymbol, LocalDate effective) {
        requireCompany(companyId);
        String symbol = required(newSymbol, "symbol").toUpperCase().trim();
        LocalDate on = effective == null ? LocalDate.now() : effective;
        Optional<Long> other = tickers.companyAt(symbol, on);
        if (other.isPresent() && other.get() != companyId) throw new IllegalArgumentException(symbol + " is used by company " + other.get());
        String cik = tickers.cikOf(companyId).orElseThrow();
        if (recordObservedTicker(cik, symbol, on, "manual").isEmpty()) throw new IllegalArgumentException(symbol + " is already the ticker on " + on);
    }

    /**
     * Permanently deletes a company that has no data attached (e.g. a mistyped addition). Companies with
     * prices, filings, events links or forecasts can only be removed from the universe, never deleted.
     */
    @Transactional
    public void delete(long companyId) {
        requireCompany(companyId);
        Map<String, Object> used = jdbc.sql("""
                SELECT (SELECT count(*) FROM price_bar WHERE company_id = :c) AS prices,
                       (SELECT count(*) FROM filing WHERE company_id = :c) AS filings,
                       (SELECT count(*) FROM forecast WHERE company_id = :c) AS forecasts,
                       (SELECT count(*) FROM company_exposure WHERE company_id = :c) AS exposures,
                       (SELECT count(*) FROM corporate_action WHERE company_id = :c) AS actions,
                       (SELECT count(*) FROM backtest_prediction WHERE company_id = :c) AS backtests""")
                .param("c", companyId).query().singleRow();
        long total = used.values().stream().mapToLong(v -> ((Number) v).longValue()).sum();
        if (total > 0) {
            throw new IllegalArgumentException("company " + companyId + " has data attached " + used
                    + "; remove it from the universe instead (its history is kept)");
        }
        for (String t : List.of("universe_membership", "ticker_history", "cik_mapping")) {
            jdbc.sql("DELETE FROM " + t + " WHERE company_id = :c").param("c", companyId).update();
        }
        jdbc.sql("DELETE FROM company WHERE id = :c").param("c", companyId).update();
    }

    /**
     * Applies an observed (symbol, cik) mapping, e.g. from SEC company_tickers.json. A changed symbol closes
     * the current ticker span on `observedOn` and opens a new one, preserving history.
     */
    @Transactional
    public Optional<String> recordObservedTicker(String cik, String symbol, LocalDate observedOn, String source) {
        Optional<Long> company = tickers.companyByCik(cik);
        if (company.isEmpty()) return Optional.empty();
        long id = company.get();
        Optional<String> current = tickers.symbolAt(id, observedOn);
        if (current.isPresent() && current.get().equalsIgnoreCase(symbol)) return Optional.empty();
        jdbc.sql("UPDATE ticker_history SET valid_to = :d WHERE company_id = :c AND valid_to IS NULL AND valid_from < :d")
                .param("d", observedOn).param("c", id).update();
        jdbc.sql("INSERT INTO ticker_history (company_id, symbol, valid_from, source) VALUES (:c, :s, :d, :src)")
                .param("c", id).param("s", symbol.toUpperCase()).param("d", observedOn).param("src", source).update();
        return Optional.of(current.orElse("?") + " -> " + symbol.toUpperCase());
    }

    // ------------------------------------------------------------------ queries

    /** Companies that are members today (pipeline steps act on these). */
    public List<Map<String, Object>> companies() {
        return jdbc.sql("""
                SELECT c.id, c.name, c.benchmark_symbol, c.industry, c.sector FROM company c
                WHERE EXISTS (SELECT 1 FROM universe_membership m WHERE m.company_id = c.id AND m.valid_from <= current_date
                                AND (m.valid_to IS NULL OR m.valid_to > current_date))
                ORDER BY c.id""").query().listOfRows();
    }

    public boolean isActive(long companyId) {
        return jdbc.sql("""
                SELECT exists(SELECT 1 FROM universe_membership WHERE company_id = :c AND universe = :u AND valid_to IS NULL)""")
                .param("c", companyId).param("u", universeName()).query(Boolean.class).single();
    }

    public boolean anyCompanies() {
        return jdbc.sql("SELECT exists(SELECT 1 FROM company)").query(Boolean.class).single();
    }

    private void requireCompany(long id) {
        if (!jdbc.sql("SELECT exists(SELECT 1 FROM company WHERE id = :id)").param("id", id).query(Boolean.class).single()) {
            throw new java.util.NoSuchElementException("company " + id + " not found");
        }
    }

    private static String required(String v, String field) {
        if (v == null || v.isBlank()) throw new IllegalArgumentException(field + " is required");
        return v;
    }

    private static String blankToNull(String v) {
        return v == null || v.isBlank() ? null : v.trim();
    }
}
