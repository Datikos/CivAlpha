package com.civalpha.universe;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.util.Optional;

/**
 * Resolves a ticker symbol to a company as of a date. Ticker history is time-ranged
 * ([valid_from, valid_to)), so "FB" on 2021-01-04 and "META" on 2023-01-03 resolve to the same company,
 * while a symbol later reused by another issuer resolves to the right one for each date.
 */
@Component
public class TickerResolver {

    private final JdbcClient jdbc;

    public TickerResolver(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<Long> companyAt(String symbol, LocalDate date) {
        return jdbc.sql("""
                SELECT company_id FROM ticker_history
                WHERE upper(symbol) = upper(:s) AND valid_from <= :d AND (valid_to IS NULL OR valid_to > :d)
                ORDER BY valid_from DESC LIMIT 1""")
                .param("s", symbol).param("d", date).query(Long.class).optional();
    }

    /** Latest company that ever used the symbol (for URL lookups such as /companies/FB). */
    public Optional<Long> companyEver(String symbol) {
        return jdbc.sql("""
                SELECT company_id FROM ticker_history WHERE upper(symbol) = upper(:s)
                ORDER BY valid_from DESC LIMIT 1""")
                .param("s", symbol).query(Long.class).optional();
    }

    public Optional<String> symbolAt(long companyId, LocalDate date) {
        return jdbc.sql("""
                SELECT symbol FROM ticker_history
                WHERE company_id = :c AND valid_from <= :d AND (valid_to IS NULL OR valid_to > :d)
                ORDER BY valid_from DESC LIMIT 1""")
                .param("c", companyId).param("d", date).query(String.class).optional();
    }

    public String currentSymbol(long companyId) {
        return jdbc.sql("SELECT symbol FROM ticker_history WHERE company_id = :c ORDER BY valid_from DESC LIMIT 1")
                .param("c", companyId).query(String.class).single();
    }

    public Optional<Long> companyByCik(String cik) {
        return jdbc.sql("SELECT company_id FROM cik_mapping WHERE cik = :c AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1")
                .param("c", padCik(cik)).query(Long.class).optional();
    }

    public Optional<String> cikOf(long companyId) {
        return jdbc.sql("SELECT cik FROM cik_mapping WHERE company_id = :c AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1")
                .param("c", companyId).query(String.class).optional();
    }

    public record Span(long companyId, String symbol, LocalDate validFrom, LocalDate validTo) {
        boolean covers(LocalDate d) {
            return !validFrom.isAfter(d) && (validTo == null || validTo.isAfter(d));
        }
    }

    public java.util.List<Span> allSpans() {
        return jdbc.sql("SELECT company_id, symbol, valid_from, valid_to FROM ticker_history")
                .query((rs, n) -> new Span(rs.getLong(1), rs.getString(2), rs.getObject(3, LocalDate.class), rs.getObject(4, LocalDate.class)))
                .list();
    }

    /** In-memory equivalent of {@link #companyAt} for bulk imports. */
    public static Optional<Long> resolve(java.util.List<Span> spansForSymbol, LocalDate date) {
        return spansForSymbol.stream().filter(s -> s.covers(date))
                .max(java.util.Comparator.comparing(Span::validFrom)).map(Span::companyId);
    }

    public static String padCik(String cik) {
        String digits = cik.replaceAll("\\D", "");
        return "0".repeat(Math.max(0, 10 - digits.length())) + digits;
    }
}
