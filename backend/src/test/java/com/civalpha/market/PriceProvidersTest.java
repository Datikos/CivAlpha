package com.civalpha.market;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZonedDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class PriceProvidersTest {

    private final JsonMapper om = JsonMapper.builder().build();

    @Test
    void parsesYahooChartWithEventsAndSkipsEmptyBars() {
        // 2024-06-07 and 2024-06-10 at 09:30 New York (13:30 UTC); a null bar in between
        String json = """
                {"chart":{"result":[{"meta":{"symbol":"NVDA","exchangeTimezoneName":"America/New_York","gmtoffset":-14400},
                  "timestamp":[1717767000,1717853400,1718026200],
                  "events":{"dividends":{"1718026200":{"amount":0.01,"date":1718026200}},
                            "splits":{"1718026200":{"date":1718026200,"numerator":10,"denominator":1,"splitRatio":"10:1"}}},
                  "indicators":{"quote":[{"open":[119.77,null,120.37],"high":[121.0,null,123.1],"low":[118.6,null,117.01],
                                          "close":[120.888,null,121.79],"volume":[412386000,null,314162700]}]}}],"error":null}}""";
        var s = YahooChartProvider.parse(om, json.getBytes(StandardCharsets.UTF_8), "NVDA", "u");
        assertThat(s.bars()).hasSize(2);
        assertThat(s.bars().get(0).date()).isEqualTo(LocalDate.parse("2024-06-07"));
        assertThat(s.bars().get(0).close()).isEqualByComparingTo("120.8880");
        assertThat(s.bars().get(1).date()).isEqualTo(LocalDate.parse("2024-06-10"));
        assertThat(s.actions()).extracting(CsvPrices.ActionRow::type).containsExactlyInAnyOrder("CASH_DIVIDEND", "SPLIT");
        assertThat(s.actions()).filteredOn(a -> a.type().equals("SPLIT")).singleElement()
                .satisfies(a -> assertThat(a.value()).isEqualByComparingTo(BigDecimal.TEN));
    }

    @Test
    void parsesTiingoRawClosesWithSplitFactorAndDividends() {
        String json = """
                [{"date":"2024-06-07T00:00:00.000Z","close":1208.88,"high":1216.9,"low":1180.22,"open":1197.7,"volume":41238600,
                  "adjClose":120.86,"divCash":0.0,"splitFactor":1.0},
                 {"date":"2024-06-10T00:00:00.000Z","close":121.79,"high":123.1,"low":117.01,"open":120.37,"volume":314162700,
                  "adjClose":121.77,"divCash":0.01,"splitFactor":10.0}]""";
        var s = TiingoProvider.parse(om, json.getBytes(StandardCharsets.UTF_8), "NVDA", "u");
        assertThat(s.bars()).extracting(PriceBarRow::date).containsExactly(LocalDate.parse("2024-06-07"), LocalDate.parse("2024-06-10"));
        assertThat(s.bars().get(0).close()).isEqualByComparingTo("1208.88"); // raw, not split-adjusted
        assertThat(s.actions()).extracting(CsvPrices.ActionRow::type).containsExactlyInAnyOrder("SPLIT", "CASH_DIVIDEND");
    }

    @Test
    void neverStoresTheCurrentSessionBeforeTheClose() {
        ZoneId ny = ZoneId.of("America/New_York");
        assertThat(PriceSyncService.lastCompletedSession(ZonedDateTime.of(2026, 10, 2, 15, 0, 0, 0, ny))).isEqualTo(LocalDate.parse("2026-10-01"));
        assertThat(PriceSyncService.lastCompletedSession(ZonedDateTime.of(2026, 10, 2, 17, 0, 0, 0, ny))).isEqualTo(LocalDate.parse("2026-10-02"));
        assertThat(PriceProvider.vendorSymbol("brk.b")).isEqualTo("BRK-B");
    }

    @Test
    void aFridayBarIsCurrentOverTheWeekend() {
        assertThat(PriceSyncService.latestWeekday(java.time.LocalDate.parse("2026-10-03"))).isEqualTo("2026-10-02");
        assertThat(PriceSyncService.latestWeekday(java.time.LocalDate.parse("2026-10-04"))).isEqualTo("2026-10-02");
        assertThat(PriceSyncService.latestWeekday(java.time.LocalDate.parse("2026-10-05"))).isEqualTo("2026-10-05");
    }
}

