package com.civalpha.market;

import com.civalpha.storage.DocumentStore;
import com.civalpha.storage.SourceDocument;
import com.civalpha.universe.TickerResolver;
import com.civalpha.universe.UniverseService;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;

/**
 * Imports daily bars and corporate actions. Symbols are resolved to companies by date through ticker
 * history (so FB rows before 2022-06-09 attach to the same company as META rows after); configured
 * benchmark ETFs are stored with company_id NULL. Prices are stored raw; adjustments come from the
 * corporate-action table. Re-importing an identical bar is a no-op; a bar with a different close is a
 * correction: the old values move to price_bar_revision and the bar becomes version n+1.
 */
@Service
public class MarketDataService {

    private final NamedParameterJdbcTemplate named;
    private final DocumentStore docs;
    private final TickerResolver tickers;
    private final UniverseService universe;

    public MarketDataService(NamedParameterJdbcTemplate named, DocumentStore docs, TickerResolver tickers, UniverseService universe) {
        this.named = named;
        this.docs = docs;
        this.tickers = tickers;
        this.universe = universe;
    }

    public record ImportResult(int rows, int inserted, int unchanged, int revised, Set<String> unknownSymbols) {}

    public ImportResult importPrices(byte[] csv, String fileName, String provider, boolean demo) throws IOException {
        SourceDocument d = docs.store(new DocumentStore.NewDocument("PRICE_FILE", provider, "file://" + fileName, null,
                "Price file " + fileName, null, "text/csv", csv, demo));
        List<PriceBarRow> rows = CsvPrices.parseBars(new ByteArrayInputStream(csv));
        Set<String> benchmarks = universe.benchmarkSymbols();
        Map<String, List<TickerResolver.Span>> spans = new HashMap<>();
        for (TickerResolver.Span sp : tickers.allSpans()) spans.computeIfAbsent(sp.symbol().toUpperCase(), k -> new ArrayList<>()).add(sp);
        Map<String, java.math.BigDecimal> existing = new HashMap<>();
        named.query("SELECT symbol, trade_date, close FROM price_bar WHERE symbol IN (:syms)",
                new MapSqlParameterSource("syms", rows.stream().map(PriceBarRow::symbol).distinct().toList().isEmpty()
                        ? List.of("") : rows.stream().map(PriceBarRow::symbol).distinct().toList()),
                rs -> { existing.put(rs.getString(1) + "|" + rs.getObject(2, LocalDate.class), rs.getBigDecimal(3)); });
        int unchanged = 0;
        List<MapSqlParameterSource> corrections = new ArrayList<>();
        Set<String> unknown = new TreeSet<>();
        List<MapSqlParameterSource> batch = new ArrayList<>();
        for (PriceBarRow r : rows) {
            Long companyId = null;
            if (!benchmarks.contains(r.symbol())) {
                Optional<Long> c = TickerResolver.resolve(spans.getOrDefault(r.symbol(), List.of()), r.date());
                if (c.isEmpty()) {
                    unknown.add(r.symbol());
                    continue;
                }
                companyId = c.get();
            }
            java.math.BigDecimal prev = existing.get(r.symbol() + "|" + r.date());
            MapSqlParameterSource p = new MapSqlParameterSource().addValue("c", companyId).addValue("s", r.symbol()).addValue("d", r.date())
                    .addValue("o", r.open()).addValue("h", r.high()).addValue("l", r.low()).addValue("cl", r.close())
                    .addValue("v", r.volume()).addValue("p", provider).addValue("doc", d.id()).addValue("demo", demo);
            if (prev == null) {
                batch.add(p);
            } else if (prev.compareTo(r.close()) == 0) {
                unchanged++;
            } else {
                corrections.add(p);
            }
        }
        // archive the replaced values, then apply the corrections as a new version of each bar
        named.batchUpdate("""
                INSERT INTO price_bar_revision (symbol, trade_date, company_id, version, open, high, low, close, volume, provider,
                                                source_document_id, ingested_at, superseded_by_document_id, is_demo)
                SELECT symbol, trade_date, company_id, version, open, high, low, close, volume, provider, source_document_id,
                       ingested_at, :doc, is_demo FROM price_bar WHERE symbol = :s AND trade_date = :d""",
                corrections.toArray(MapSqlParameterSource[]::new));
        named.batchUpdate("""
                UPDATE price_bar SET open = :o, high = :h, low = :l, close = :cl, volume = :v, provider = :p, source_document_id = :doc,
                       ingested_at = now(), version = version + 1 WHERE symbol = :s AND trade_date = :d""",
                corrections.toArray(MapSqlParameterSource[]::new));
        int[] res = named.batchUpdate("""
                INSERT INTO price_bar (company_id, symbol, trade_date, open, high, low, close, volume, provider, source_document_id, is_demo)
                VALUES (:c, :s, :d, :o, :h, :l, :cl, :v, :p, :doc, :demo) ON CONFLICT (symbol, trade_date) DO NOTHING""",
                batch.toArray(MapSqlParameterSource[]::new));
        int inserted = 0;
        for (int x : res) inserted += Math.max(0, x);
        return new ImportResult(rows.size(), inserted, unchanged, corrections.size(), unknown);
    }

    public int importActions(byte[] csv, String fileName, String provider, boolean demo) throws IOException {
        SourceDocument d = docs.store(new DocumentStore.NewDocument("PRICE_FILE", provider, "file://" + fileName, null,
                "Corporate actions " + fileName, null, "text/csv", csv, demo));
        Set<String> benchmarks = universe.benchmarkSymbols();
        int n = 0;
        for (CsvPrices.ActionRow a : CsvPrices.parseActions(new ByteArrayInputStream(csv))) {
            Long companyId = benchmarks.contains(a.symbol()) ? null : tickers.companyAt(a.symbol(), a.exDate()).orElse(null);
            n += named.update("""
                    INSERT INTO corporate_action (company_id, symbol, ex_date, action_type, value, announced_at, provider, source_document_id, is_demo)
                    VALUES (:c, :s, :d, :t, :v, :a, :p, :doc, :demo) ON CONFLICT (symbol, ex_date, action_type) DO NOTHING""",
                    new MapSqlParameterSource().addValue("c", companyId).addValue("s", a.symbol()).addValue("d", a.exDate())
                            .addValue("t", a.type()).addValue("v", a.value()).addValue("a", a.announcedAt()).addValue("p", provider)
                            .addValue("doc", d.id()).addValue("demo", demo));
        }
        return n;
    }

    /** Configured benchmark ETFs (config/universe.yml) that have no price bars yet. */
    public List<String> missingBenchmarks() {
        Set<String> configured = new TreeSet<>(universe.benchmarkSymbols());
        List<String> present = named.getJdbcTemplate().queryForList(
                "SELECT DISTINCT symbol FROM price_bar WHERE company_id IS NULL", String.class);
        configured.removeAll(present);
        return new ArrayList<>(configured);
    }

    /**
     * Every forecast and evaluation is relative to a sector benchmark, so they need benchmark prices.
     * Fails with an actionable message when none are loaded; returns a warning when only some are missing.
     */
    public Optional<String> requireBenchmarks() {
        List<String> missing = missingBenchmarks();
        if (latestBenchmarkDate().isEmpty()) {
            throw new IllegalStateException("No benchmark ETF prices are loaded (" + String.join(", ", universe.benchmarkSymbols())
                    + "). Load the demo dataset, or import a prices CSV that includes these ETFs, then try again.");
        }
        return missing.isEmpty() ? Optional.empty() : Optional.of("Warning: no prices for benchmark(s) " + String.join(", ", missing)
                + "; companies benchmarked against them are skipped. Import their prices to include them.");
    }

    public Optional<LocalDate> latestBenchmarkDate() {
        return Optional.ofNullable(named.getJdbcTemplate().queryForObject("SELECT max(trade_date) FROM price_bar WHERE company_id IS NULL", LocalDate.class));
    }
}
