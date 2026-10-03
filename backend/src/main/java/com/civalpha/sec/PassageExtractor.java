package com.civalpha.sec;

import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Rule-based (keyword) extraction of source-linked passages from filing HTML. Deterministic and
 * inspectable; every passage keeps its section heading and character offsets in the extracted text.
 */
public final class PassageExtractor {

    public static final String VERSION = "rules-v1";
    private static final int MAX_PASSAGES = 60;
    private static final int MAX_CHARS = 1500;
    private static final Pattern ITEM_HEADING = Pattern.compile("^(item\\s+\\d+[a-z]?\\.?)(.{0,160})$", Pattern.CASE_INSENSITIVE);

    public record Passage(String section, String topic, String text, int charStart, int charEnd) {}

    /** Topic rules in priority order. */
    private static final List<Rule> RULES = List.of(
            new Rule("TRADE", "tariff|export control|trade restriction|import dut|trade polic|trade war|sanction|countervailing|anti-dumping"),
            new Rule("GEOGRAPHIC_REVENUE", "(net sales|revenue|net revenue)[^.]{0,120}(geographic|countr|region|china|europe|japan|taiwan|mexico|asia)|attributed to countries|by geographic"),
            new Rule("SEGMENT", "reportable segment|segment information|operating segment"),
            new Rule("RATES", "interest rate|basis point|floating[- ]rate|variable[- ]rate"),
            new Rule("DEBT", "long-term debt|senior notes|notes due|credit facility|commercial paper|borrowings"),
            new Rule("COSTS", "cost of (sales|revenue|goods)|raw material|commodit|component (cost|shortage)|supplier|manufactur|assembl|foundr"),
            new Rule("RISK", "could (adversely|materially|negatively) affect|adverse effect"));

    record Rule(String topic, Pattern pattern) {
        Rule(String topic, String regex) {
            this(topic, Pattern.compile(regex, Pattern.CASE_INSENSITIVE));
        }
    }

    public static List<Passage> extract(String html) {
        Document doc = Jsoup.parse(html);
        doc.select("script, style, ix\\:header").remove();
        StringBuilder text = new StringBuilder();
        List<Passage> out = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        String section = null;
        for (Element el : doc.select("h1, h2, h3, h4, p, div:not(:has(div, p)), td:not(:has(div, p, table))")) {
            String t = el.text().replace(' ', ' ').trim();
            if (t.isEmpty()) continue;
            boolean heading = el.tagName().matches("h[1-4]") || (t.length() < 200 && ITEM_HEADING.matcher(t).matches());
            int start = text.length();
            text.append(t).append('\n');
            if (heading) {
                section = t.length() > 200 ? t.substring(0, 200) : t;
                continue;
            }
            if (t.length() < 60) continue;
            for (Rule r : RULES) {
                if (r.pattern().matcher(t).find()) {
                    String body = t.length() > MAX_CHARS ? t.substring(0, MAX_CHARS) + "…" : t;
                    if (seen.add(body)) {
                        out.add(new Passage(section, r.topic(), body, start, start + t.length()));
                    }
                    break;
                }
            }
            if (out.size() >= MAX_PASSAGES) break;
        }
        return out;
    }

    private PassageExtractor() {}
}
