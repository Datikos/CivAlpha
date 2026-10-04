package com.civalpha.sec;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SecParsersTest {

    private final JsonMapper om = JsonMapper.builder().build();

    @Test
    void submissionsUseAcceptanceTimeAndFallBackConservatively() {
        String json = """
                {"cik":"0000320193","name":"Apple Inc.","tickers":["AAPL"],
                 "filings":{"recent":{
                   "accessionNumber":["0000320193-24-000123","0000320193-24-000081"],
                   "filingDate":["2024-11-01","2024-08-02"],
                   "reportDate":["2024-09-28","2024-06-29"],
                   "acceptanceDateTime":["2024-11-01T06:01:36.000Z",""],
                   "form":["10-K","10-Q"],
                   "primaryDocument":["aapl-20240928.htm","aapl-20240629.htm"],
                   "items":["",""]}}}""";
        var s = SubmissionsParser.parse(om, json.getBytes(StandardCharsets.UTF_8));
        assertThat(s.tickers()).containsExactly("AAPL");
        assertThat(s.filings()).hasSize(2);
        FilingMeta k = s.filings().get(0);
        assertThat(k.acceptedAt()).isEqualTo(OffsetDateTime.parse("2024-11-01T06:01:36Z"));
        assertThat(k.isAnnual()).isTrue();
        // missing acceptance time -> end of the filing day in New York (never earlier than the real acceptance)
        assertThat(s.filings().get(1).acceptedAt()).isEqualTo(OffsetDateTime.parse("2024-08-03T03:59:59Z"));
    }

    @Test
    void submissionsListOlderPagesAndPagesParseLikeRecent() {
        String root = """
                {"cik":"0000320193","name":"Apple Inc.","tickers":["AAPL"],"filings":{
                  "recent":{"accessionNumber":[],"filingDate":[],"reportDate":[],"acceptanceDateTime":[],"form":[],"primaryDocument":[],"items":[]},
                  "files":[{"name":"CIK0000320193-submissions-001.json","filingCount":1200,"filingFrom":"1994-01-26","filingTo":"2014-07-21"}]}}""";
        var s = SubmissionsParser.parse(om, root.getBytes(StandardCharsets.UTF_8));
        assertThat(s.olderPages()).singleElement().satisfies(p -> {
            assertThat(p.name()).isEqualTo("CIK0000320193-submissions-001.json");
            assertThat(p.filingTo()).isEqualTo(LocalDate.parse("2014-07-21"));
        });
        String page = """
                {"accessionNumber":["0001193125-14-277160"],"filingDate":["2014-07-23"],"reportDate":["2014-06-28"],
                 "acceptanceDateTime":["2014-07-23T16:31:21.000Z"],"form":["10-Q"],"primaryDocument":["d735836d10q.htm"],"items":[""]}""";
        assertThat(SubmissionsParser.parsePage(om, page.getBytes(StandardCharsets.UTF_8)))
                .singleElement().satisfies(f -> assertThat(f.form()).isEqualTo("10-Q"));
    }

    @Test
    void locatesXbrlInstanceFromFilingIndex() {
        String index = """
                {"directory":{"name":"/Archives/edgar/data/320193/000032019324000123","item":[
                  {"name":"0000320193-24-000123-index.htm"},{"name":"FilingSummary.xml"},{"name":"aapl-20240928.htm"},
                  {"name":"aapl-20240928.xsd"},{"name":"aapl-20240928_cal.xml"},{"name":"aapl-20240928_htm.xml"}]}}""";
        var names = XbrlInstanceLocator.names(om, index.getBytes(StandardCharsets.UTF_8));
        assertThat(XbrlInstanceLocator.pick(names, "aapl-20240928.htm")).contains("aapl-20240928_htm.xml");
        // pre-inline filings: standalone instance next to linkbases and schema
        var old = List.of("FilingSummary.xml", "aapl-20100925.xml", "aapl-20100925.xsd", "aapl-20100925_lab.xml", "aapl-20100925_pre.xml");
        assertThat(XbrlInstanceLocator.pick(old, "d10k.htm")).contains("aapl-20100925.xml");
        assertThat(XbrlInstanceLocator.pick(List.of("FilingSummary.xml", "x.xsd"), "d.htm")).isEmpty();
        assertThat(XbrlInstanceLocator.conventional("msft-10k_20240630.htm")).isEqualTo("msft-10k_20240630_htm.xml");
    }

    @Test
    void companyFactsKeepsAccessionFiledDateAndPeriods() {
        String json = """
                {"cik":320193,"entityName":"Apple Inc.","facts":{"us-gaap":{
                  "Revenues":{"units":{"USD":[
                     {"start":"2023-10-01","end":"2023-12-30","val":119575000000,"accn":"0000320193-24-000006","fy":2024,"fp":"Q1","form":"10-Q","filed":"2024-02-02"},
                     {"start":"2023-10-01","end":"2023-12-30","val":119000000000,"accn":"0000320193-25-000008","fy":2025,"fp":"Q1","form":"10-Q","filed":"2025-01-31"}]}},
                  "Assets":{"units":{"USD":[{"end":"2023-12-30","val":353514000000,"accn":"0000320193-24-000006","form":"10-Q","filed":"2024-02-02"}]}},
                  "SomethingElse":{"units":{"USD":[{"end":"2023-12-30","val":1,"accn":"x","form":"10-Q","filed":"2024-02-02"}]}}}}}""";
        List<FactRow> rows = CompanyFactsParser.parse(om, json.getBytes(StandardCharsets.UTF_8), CompanyFactsParser.CONCEPTS);
        assertThat(rows).hasSize(3);
        assertThat(rows).filteredOn(r -> r.concept().equals("Revenues")).extracting(FactRow::accessionNo)
                .containsExactly("0000320193-24-000006", "0000320193-25-000008"); // the comparative restatement is kept as its own row
        FactRow assets = rows.stream().filter(r -> r.concept().equals("Assets")).findFirst().orElseThrow();
        assertThat(assets.periodStart()).isNull();
        assertThat(assets.dimsKey()).isEmpty();
    }

    @Test
    void xbrlInstanceExtractsGeographicDimensions() {
        String xml = """
                <?xml version="1.0"?>
                <xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
                  xmlns:us-gaap="http://fasb.org/us-gaap/2024" xmlns:srt="http://fasb.org/srt/2024" xmlns:country="http://xbrl.sec.gov/country/2024"
                  xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
                  <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
                  <xbrli:context id="FY"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier></xbrli:entity>
                    <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
                  <xbrli:context id="CN"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>
                    <xbrli:segment><xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">country:CN</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
                    <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
                  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="FY" unitRef="usd" decimals="-6">1000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
                  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="CN" unitRef="usd" decimals="-6">250</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
                  <us-gaap:Assets contextRef="FY" unitRef="usd">5</us-gaap:Assets>
                </xbrli:xbrl>""";
        List<FactRow> rows = XbrlInstanceParser.parse(xml.getBytes(StandardCharsets.UTF_8),
                Set.of("RevenueFromContractWithCustomerExcludingAssessedTax"), "acc-1", "10-K", LocalDate.of(2025, 2, 1));
        assertThat(rows).hasSize(2);
        FactRow cn = rows.stream().filter(r -> !r.dimensions().isEmpty()).findFirst().orElseThrow();
        assertThat(cn.dimensions()).containsEntry("srt:StatementGeographicalAxis", "country:CN");
        assertThat(cn.dimsKey()).isEqualTo("srt:StatementGeographicalAxis=country:CN");
        assertThat(cn.unit()).isEqualTo("USD");
        assertThat(cn.taxonomy()).isEqualTo("us-gaap");
    }

    @Test
    void xbrlParserRejectsExternalEntities() {
        String xxe = "<?xml version=\"1.0\"?><!DOCTYPE x [<!ENTITY e SYSTEM \"file:///etc/passwd\">]><x>&e;</x>";
        assertThatThrownBy(() -> XbrlInstanceParser.parse(xxe.getBytes(StandardCharsets.UTF_8), null, "a", "10-K", LocalDate.now()))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void passagesKeepSectionTopicAndOffsets() {
        String html = """
                <html><body><h2>Item 1A. Risk Factors</h2>
                <p>We rely on manufacturing partners in China for substantially all of our products; tariffs or export controls affecting China could increase our costs.</p>
                <p>Short.</p>
                <h2>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</h2>
                <p>Interest rate risk: a 100 basis point increase in rates would change interest expense on our floating-rate debt.</p></body></html>""";
        var ps = PassageExtractor.extract(html);
        assertThat(ps).hasSize(2);
        assertThat(ps.get(0).topic()).isEqualTo("TRADE");
        assertThat(ps.get(0).section()).isEqualTo("Item 1A. Risk Factors");
        assertThat(ps.get(1).topic()).isEqualTo("RATES");
        assertThat(ps.get(0).charEnd()).isGreaterThan(ps.get(0).charStart());
    }

    @Test
    void liveClientRequiresDeclaredUserAgentAndSaneRate() {
        var http = java.net.http.HttpClient.newHttpClient();
        assertThatThrownBy(() -> new LiveSecClient(http, "", 5)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new LiveSecClient(http, "Mozilla/5.0", 5)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new LiveSecClient(http, "CivAlpha research jane@example.com", 20)).isInstanceOf(IllegalArgumentException.class);
        new LiveSecClient(http, "CivAlpha research jane@example.com", 5);
    }

    @Test
    void rateLimiterSpacesRequests() {
        RateLimiter l = new RateLimiter(10);
        long t0 = System.nanoTime();
        for (int i = 0; i < 6; i++) l.acquire();
        assertThat((System.nanoTime() - t0) / 1_000_000).isGreaterThanOrEqualTo(450);
    }

    @Test
    void fixtureClientMapsSecUrlsAndBlocksTraversal(@org.junit.jupiter.api.io.TempDir java.nio.file.Path dir) throws Exception {
        java.nio.file.Files.createDirectories(dir.resolve("submissions"));
        java.nio.file.Files.writeString(dir.resolve("submissions/CIK0000000001.json"), "{}");
        FixtureSecClient c = new FixtureSecClient(dir);
        assertThat(c.get(SecClient.SUBMISSIONS.formatted("0000000001"))).isPresent();
        assertThat(c.get("https://www.sec.gov/Archives/../../etc/passwd")).isEmpty();
        assertThat(c.get("https://example.com/submissions/CIK0000000001.json")).isEmpty();
    }
}
