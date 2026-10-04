package com.civalpha.universe;

import com.civalpha.config.AppProperties;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/** Loads the configured universe and keeps ticker / CIK mappings as time-ranged history. */
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

    public Set<String> benchmarkSymbols() {
        return config().benchmarks().keySet();
    }

    /** Idempotent: companies are keyed by CIK. Returns number of companies created. */
    @Transactional
    public int load(boolean demo) {
        UniverseConfig cfg = config();
        int created = 0;
        for (UniverseConfig.Entry e : cfg.companies()) {
            String cik = TickerResolver.padCik(e.cik());
            Optional<Long> existing = tickers.companyByCik(cik);
            long id;
            if (existing.isPresent()) {
                id = existing.get();
                jdbc.sql("UPDATE company SET name = :n, sector = :s, industry = :i, benchmark_symbol = :b WHERE id = :id")
                        .param("n", e.name()).param("s", e.sector()).param("i", e.industry()).param("b", e.benchmark())
                        .param("id", id).update();
            } else {
                id = jdbc.sql("""
                        INSERT INTO company (name, sector, industry, benchmark_symbol, is_demo)
                        VALUES (:n, :s, :i, :b, :demo) RETURNING id""")
                        .param("n", e.name()).param("s", e.sector()).param("i", e.industry()).param("b", e.benchmark())
                        .param("demo", demo).query(Long.class).single();
                jdbc.sql("INSERT INTO cik_mapping (company_id, cik, valid_from, source) VALUES (:c, :cik, :f, 'universe config')")
                        .param("c", id).param("cik", cik).param("f", EPOCH).update();
                List<UniverseConfig.TickerSpan> hist = e.tickerHistory().isEmpty()
                        ? List.of(new UniverseConfig.TickerSpan(e.symbol(), EPOCH, null)) : e.tickerHistory();
                for (UniverseConfig.TickerSpan t : hist) {
                    jdbc.sql("""
                            INSERT INTO ticker_history (company_id, symbol, valid_from, valid_to, source)
                            VALUES (:c, :s, :f, :t, 'universe config')""")
                            .param("c", id).param("s", t.symbol()).param("f", t.validFrom()).param("t", t.validTo()).update();
                }
                created++;
            }
            int member = jdbc.sql("SELECT count(*) FROM universe_membership WHERE universe = :u AND company_id = :c AND valid_to IS NULL")
                    .param("u", cfg.universe()).param("c", id).query(Integer.class).single();
            if (member == 0) {
                jdbc.sql("INSERT INTO universe_membership (universe, company_id, valid_from) VALUES (:u, :c, :f)")
                        .param("u", cfg.universe()).param("c", id).param("f", cfg.startDate()).update();
            }
        }
        return created;
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
                .param("c", id).param("s", symbol).param("d", observedOn).param("src", source).update();
        return Optional.of(current.orElse("?") + " -> " + symbol);
    }

    public List<Map<String, Object>> companies() {
        return jdbc.sql("SELECT id, name, benchmark_symbol, industry, sector FROM company ORDER BY id").query().listOfRows();
    }
}
