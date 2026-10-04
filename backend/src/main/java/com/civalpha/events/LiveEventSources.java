package com.civalpha.events;

import com.civalpha.config.AppProperties;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.parser.Parser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Official and discovery feeds (all optional, disabled by default):
 *  - Federal Register API (official trade/tariff notices, proclamations, rules)
 *  - Federal Reserve monetary-policy press RSS (FOMC statements; rate change parsed from the statement)
 *  - generic news RSS feeds (discovery only; stored as NEWS_ONLY until an official document is linked)
 */
@Component
public class LiveEventSources {

    private static final Logger log = LoggerFactory.getLogger(LiveEventSources.class);
    private static final Pattern TRADE = Pattern.compile("tariff|duties|duty|section 301|section 232|export control|import", Pattern.CASE_INSENSITIVE);
    private static final Pattern MONETARY = Pattern.compile("fomc|federal funds|interest rate|federal reserve", Pattern.CASE_INSENSITIVE);
    static final String FEDERAL_REGISTER = "https://www.federalregister.gov/api/v1/documents.json?per_page=40&order=newest"
            + "&conditions[term]=tariff&conditions[type][]=RULE&conditions[type][]=PRESDOCU&conditions[type][]=NOTICE";
    static final String FED_RSS = "https://www.federalreserve.gov/feeds/press_monetary.xml";

    private final HttpClient http;
    private final ObjectMapper om;
    private final AppProperties props;

    public LiveEventSources(HttpClient http, ObjectMapper om, AppProperties props) {
        this.http = http;
        this.om = om;
        this.props = props;
    }

    public List<EventDraft> collect(java.util.function.Consumer<String> logLine) {
        List<EventDraft> out = new ArrayList<>();
        if (props.events().federalRegisterEnabled()) out.addAll(safe("Federal Register", this::federalRegister, logLine));
        if (props.events().fedRssEnabled()) out.addAll(safe("Federal Reserve RSS", this::fedRss, logLine));
        List<String> feeds = props.events().newsFeeds();
        if (feeds != null) {
            for (String f : feeds) if (!f.isBlank()) out.addAll(safe("news " + f, () -> news(f), logLine));
        }
        return out;
    }

    private List<EventDraft> safe(String name, java.util.concurrent.Callable<List<EventDraft>> c, java.util.function.Consumer<String> logLine) {
        try {
            List<EventDraft> l = c.call();
            logLine.accept(name + ": " + l.size() + " candidate events");
            return l;
        } catch (Exception e) {
            logLine.accept(name + ": failed (" + e.getMessage() + ")");
            log.warn("event source {} failed", name, e);
            return List.of();
        }
    }

    List<EventDraft> federalRegister() throws Exception {
        byte[] body = get(FEDERAL_REGISTER);
        List<EventDraft> out = new ArrayList<>();
        for (JsonNode r : om.readTree(body).path("results")) {
            String title = r.path("title").asString();
            String abs = r.path("abstract").asString("");
            if (!TRADE.matcher(title + " " + abs).find()) continue;
            String url = r.path("html_url").asString();
            LocalDate pub = LocalDate.parse(r.path("publication_date").asString());
            OffsetDateTime published = pub.atTime(13, 45).atOffset(ZoneOffset.UTC); // FR public inspection precedes; publication ~8:45 ET
            String agency = r.path("agencies").path(0).path("name").asString("Federal Register");
            Map<String, Object> attrs = new HashMap<>();
            attrs.put("severity", 0.5);
            attrs.put("auto_extracted", true);
            attrs.put("document_number", r.path("document_number").asString());
            String text = title + ". " + abs;
            out.add(new EventDraft("TRADE_TARIFF", TextSignals.tradeEventType(text), title, abs, pub, published, agency, attrs,
                    TextSignals.tradeTargets(text),
                    new EventDraft.Source(url, title, "Federal Register", "OFFICIAL_PRIMARY", published,
                            om.writeValueAsBytes(r), "application/json", false)));
        }
        return out;
    }

    List<EventDraft> fedRss() throws Exception {
        Document rss = Jsoup.parse(new String(get(FED_RSS), StandardCharsets.UTF_8), "", Parser.xmlParser());
        List<EventDraft> out = new ArrayList<>();
        for (Element item : rss.select("item")) {
            String title = item.selectFirst("title").text();
            if (!title.toLowerCase().contains("fomc statement")) continue;
            String link = item.selectFirst("link").text();
            OffsetDateTime published = ZonedDateTime.parse(item.selectFirst("pubDate").text(), DateTimeFormatter.RFC_1123_DATE_TIME).toOffsetDateTime();
            byte[] html = get(link);
            String text = Jsoup.parse(new String(html, StandardCharsets.UTF_8)).text();
            var decision = TextSignals.rateDecision(text);
            Map<String, Object> attrs = new HashMap<>();
            decision.ifPresent(d -> {
                attrs.put("rate_change_bps", d.changeBps());
                attrs.put("target_lower_pct", d.lower());
                attrs.put("target_upper_pct", d.upper());
            });
            String verb = decision.map(d -> d.changeBps() > 0 ? "raised" : d.changeBps() < 0 ? "lowered" : "maintained").orElse("statement");
            out.add(new EventDraft("MONETARY_POLICY", "RATE_DECISION", "FOMC statement: target range " + verb, title,
                    published.toLocalDate(), published, "Federal Open Market Committee", attrs,
                    List.of(new EventDraft.Target("INTEREST_RATE", "US_POLICY_RATE", decision.map(d -> (double) d.changeBps()).orElse(null))),
                    new EventDraft.Source(link, title, "Board of Governors of the Federal Reserve System", "OFFICIAL_PRIMARY", published,
                            html, "text/html", false)));
        }
        return out;
    }

    List<EventDraft> news(String feedUrl) throws Exception {
        Document rss = Jsoup.parse(new String(get(feedUrl), StandardCharsets.UTF_8), "", Parser.xmlParser());
        List<EventDraft> out = new ArrayList<>();
        for (Element item : rss.select("item")) {
            String title = item.selectFirst("title") == null ? "" : item.selectFirst("title").text();
            String desc = item.selectFirst("description") == null ? "" : Jsoup.parse(item.selectFirst("description").text()).text();
            String text = title + ". " + desc;
            boolean trade = TRADE.matcher(text).find();
            if (!trade && !MONETARY.matcher(text).find()) continue;
            String link = item.selectFirst("link") == null ? null : item.selectFirst("link").text();
            if (link == null || link.isBlank()) continue;
            OffsetDateTime published = item.selectFirst("pubDate") == null ? OffsetDateTime.now(ZoneOffset.UTC)
                    : ZonedDateTime.parse(item.selectFirst("pubDate").text(), DateTimeFormatter.RFC_1123_DATE_TIME).toOffsetDateTime();
            out.add(new EventDraft(trade ? "TRADE_TARIFF" : "MONETARY_POLICY", trade ? TextSignals.tradeEventType(text) : "RATE_DECISION",
                    title, desc, published.toLocalDate(), published, null, Map.of("severity", 0.3),
                    trade ? TextSignals.tradeTargets(text) : List.of(),
                    new EventDraft.Source(link, title, URI.create(feedUrl).getHost(), "NEWS_DISCOVERY", published, null, null, false)));
        }
        return out;
    }

    private byte[] get(String url) throws Exception {
        HttpResponse<byte[]> r = http.send(HttpRequest.newBuilder(URI.create(url))
                .header("User-Agent", props.sec().userAgent() == null || props.sec().userAgent().isBlank() ? "CivAlpha research" : props.sec().userAgent())
                .GET().build(), HttpResponse.BodyHandlers.ofByteArray());
        if (r.statusCode() != 200) throw new IllegalStateException("HTTP " + r.statusCode() + " for " + url);
        return r.body();
    }
}
