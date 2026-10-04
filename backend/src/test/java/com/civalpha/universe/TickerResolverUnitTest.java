package com.civalpha.universe;

import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class TickerResolverUnitTest {

    @Test
    void resolvesTickerByDateIncludingReusedSymbols() {
        // symbol "XYZ" used by company 1 until 2020-01-01, then reassigned to company 2
        var spans = List.of(new TickerResolver.Span(1, "XYZ", LocalDate.parse("2010-01-01"), LocalDate.parse("2020-01-01")),
                new TickerResolver.Span(2, "XYZ", LocalDate.parse("2021-06-01"), null));
        assertThat(TickerResolver.resolve(spans, LocalDate.parse("2019-12-31"))).contains(1L);
        assertThat(TickerResolver.resolve(spans, LocalDate.parse("2020-01-01"))).isEmpty(); // valid_to is exclusive, gap
        assertThat(TickerResolver.resolve(spans, LocalDate.parse("2022-01-03"))).contains(2L);
        assertThat(TickerResolver.padCik("320193")).isEqualTo("0000320193");
    }
}
