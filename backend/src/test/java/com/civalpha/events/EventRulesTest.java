package com.civalpha.events;

import com.civalpha.exposure.Vocabulary;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

class EventRulesTest {

    private static EventDraft draft(String cat, String type, String title, String date, List<EventDraft.Target> targets, String url) {
        return new EventDraft(cat, type, title, null, LocalDate.parse(date), OffsetDateTime.parse(date + "T20:00:00Z"), null, Map.of(),
                targets, new EventDraft.Source(url, title, "x", "NEWS_DISCOVERY", null, null, null, false));
    }

    private final EventDeduplicator.Existing official = new EventDeduplicator.Existing(7, "TRADE_TARIFF", "TARIFF_IMPOSED",
            "New duties on electronics assembled in China and Vietnam", LocalDate.parse("2026-09-22"),
            Set.of("COUNTRY:CN", "COUNTRY:VN", "PRODUCT:CONSUMER_ELECTRONICS"), Set.of("https://www.federalregister.gov/d/2026-1"));

    @Test
    void newsReportOfSameEventIsMerged() {
        var d = draft("TRADE_TARIFF", "TARIFF_IMPOSED", "Wire report: new duties on electronics assembled in China and Vietnam", "2026-09-22", List.of(), "https://news/1");
        assertThat(EventDeduplicator.findDuplicate(d, List.of(official))).contains(7L);
    }

    @Test
    void sameUrlIsAlwaysTheSameEvent() {
        var d = draft("TRADE_TARIFF", "TARIFF_IMPOSED", "Completely different words", "2026-01-01", List.of(), "https://www.federalregister.gov/d/2026-1");
        assertThat(EventDeduplicator.findDuplicate(d, List.of(official))).contains(7L);
    }

    @Test
    void differentEventNearbyIsNotMerged() {
        var d = draft("TRADE_TARIFF", "TARIFF_PROPOSED", "Officials weigh duties on Mexican auto parts", "2026-09-25",
                List.of(new EventDraft.Target("COUNTRY", "MX", null), new EventDraft.Target("PRODUCT", "AUTOS", null)), "https://news/2");
        assertThat(EventDeduplicator.findDuplicate(d, List.of(official))).isEmpty();
    }

    @Test
    void sameStoryOutsideDateWindowIsNotMerged() {
        var d = draft("TRADE_TARIFF", "TARIFF_IMPOSED", "New duties on electronics assembled in China and Vietnam", "2026-10-10", List.of(), "https://news/3");
        assertThat(EventDeduplicator.findDuplicate(d, List.of(official))).isEmpty();
    }

    @Test
    void oneRateDecisionPerMeetingDate() {
        var fomc = new EventDeduplicator.Existing(3, "MONETARY_POLICY", "RATE_DECISION", "FOMC statement", LocalDate.parse("2025-09-17"), Set.of(), Set.of());
        var d = draft("MONETARY_POLICY", "RATE_DECISION", "Fed cuts rates by a quarter point", "2025-09-17", List.of(), "https://news/4");
        assertThat(EventDeduplicator.findDuplicate(d, List.of(fomc))).contains(3L);
        var next = draft("MONETARY_POLICY", "RATE_DECISION", "Fed cuts rates by a quarter point", "2025-10-29", List.of(), "https://news/5");
        assertThat(EventDeduplicator.findDuplicate(next, List.of(fomc))).isEmpty();
    }

    @Test
    void parsesFomcStatementRateDecisions() {
        assertThat(TextSignals.rateDecision("the Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 4 to 4-1/4 percent."))
                .hasValueSatisfying(r -> {
                    assertThat(r.changeBps()).isEqualTo(-25);
                    assertThat(r.lower()).isEqualTo(4.0);
                    assertThat(r.upper()).isEqualTo(4.25);
                });
        assertThat(TextSignals.rateDecision("the Committee decided to raise the target range for the federal funds rate to 5-1/4 to 5-1/2 percent."))
                .hasValueSatisfying(r -> assertThat(r.upper()).isEqualTo(5.5));
        assertThat(TextSignals.rateDecision("the Committee decided to maintain the target range for the federal funds rate at 4-1/4 to 4-1/2 percent and to 4-1/4 to 4-1/2 percent."))
                .hasValueSatisfying(r -> assertThat(r.changeBps()).isZero());
    }

    @Test
    void extractsTradeTargets() {
        var t = TextSignals.tradeTargets("Notice of action: 25% additional duties on semiconductors from China and Taiwan.");
        assertThat(t).extracting(EventDraft.Target::targetCode).contains("CN", "TW", "SEMICONDUCTORS");
        assertThat(t.get(0).magnitude()).isEqualTo(25.0);
        assertThat(TextSignals.tradeEventType("Notice of product exclusions and suspension of duties")).isEqualTo("TARIFF_REDUCED");
    }

    @Test
    void mapsXbrlGeographicMembers() {
        assertThat(Vocabulary.geoMember("country:CN")).contains(new Vocabulary.GeoMatch("CN", true));
        assertThat(Vocabulary.geoMember("aapl:GreaterChinaSegmentMember")).contains(new Vocabulary.GeoMatch("CN", false));
        assertThat(Vocabulary.geoMember("civdemo:EuropeMember")).contains(new Vocabulary.GeoMatch("EU", false));
        assertThat(Vocabulary.geoMember("civdemo:RestOfWorldMember")).isEmpty();
        assertThat(Vocabulary.geoMember("us-gaap:AmericasMember")).isEmpty();
    }
}
