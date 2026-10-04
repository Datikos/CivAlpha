package com.civalpha.events;

import com.civalpha.exposure.Vocabulary;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Deterministic extraction of event attributes from official text (no LLM required). */
public final class TextSignals {

    private static final Pattern RANGE = Pattern.compile(
            "(raise|lower|maintain|increase|decrease|reduce|cut)\\w*\\s+the\\s+target\\s+range\\s+for\\s+the\\s+federal\\s+funds\\s+rate"
                    + "(?:\\s+by\\s+(\\d+(?:/\\d+)?)\\s+(?:basis\\s+points?|percentage\\s+points?))?"
                    + "[^.]*?\\bto\\s+(\\d+(?:[-\\u2010-\\u2014\\s]\\d/\\d)?(?:\\.\\d+)?)\\s+to\\s+(\\d+(?:[-\\u2010-\\u2014\\s]\\d/\\d)?(?:\\.\\d+)?)\\s+percent",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern PERCENT = Pattern.compile("(\\d{1,3}(?:\\.\\d+)?)\\s*(?:%|percent)", Pattern.CASE_INSENSITIVE);

    public record RateDecision(int changeBps, double lower, double upper) {}

    /** Parses an FOMC statement sentence like "decided to lower the target range ... by 1/4 percentage point to 4 to 4-1/4 percent". */
    public static Optional<RateDecision> rateDecision(String text) {
        Matcher m = RANGE.matcher(text.replace('\n', ' '));
        if (!m.find()) return Optional.empty();
        String verb = m.group(1).toLowerCase();
        double lo = fraction(m.group(3)), hi = fraction(m.group(4));
        int bps = 0;
        if (m.group(2) != null) {
            double v = fraction(m.group(2));
            bps = (int) Math.round(m.group(0).toLowerCase().contains("percentage point") ? v * 100 : v);
        }
        if (verb.startsWith("lower") || verb.startsWith("decrease") || verb.startsWith("reduce") || verb.startsWith("cut")) bps = -Math.abs(bps);
        if (verb.startsWith("maintain")) bps = 0;
        return Optional.of(new RateDecision(bps, lo, hi));
    }

    static double fraction(String s) {
        s = s.trim().replaceAll("[\\u2010-\\u2014]", "-");
        if (s.contains("/")) {
            String[] whole = s.split("[- ]");
            double v = 0;
            for (String part : whole) {
                if (part.contains("/")) {
                    String[] f = part.split("/");
                    v += Double.parseDouble(f[0]) / Double.parseDouble(f[1]);
                } else if (!part.isBlank()) {
                    v += Double.parseDouble(part);
                }
            }
            return v;
        }
        return Double.parseDouble(s);
    }

    /** Country and product targets mentioned in trade text, with the largest percentage as magnitude. */
    public static List<EventDraft.Target> tradeTargets(String text) {
        Double magnitude = null;
        Matcher pm = PERCENT.matcher(text);
        while (pm.find()) {
            double v = Double.parseDouble(pm.group(1));
            if (v <= 200 && (magnitude == null || v > magnitude)) magnitude = v;
        }
        List<EventDraft.Target> out = new ArrayList<>();
        for (Map.Entry<String, Pattern> c : Vocabulary.COUNTRIES.entrySet()) {
            if (!"US".equals(c.getKey()) && c.getValue().matcher(text).find()) out.add(new EventDraft.Target("COUNTRY", c.getKey(), magnitude));
        }
        for (Map.Entry<String, Pattern> p : Vocabulary.PRODUCTS.entrySet()) {
            if (p.getValue().matcher(text).find()) out.add(new EventDraft.Target("PRODUCT", p.getKey(), magnitude));
        }
        return out;
    }

    public static String tradeEventType(String text) {
        String t = text.toLowerCase();
        if (t.contains("export control") || t.contains("entity list")) return "EXPORT_CONTROL";
        if (t.contains("exclusion") || t.contains("suspend") || t.contains("reduc") || t.contains("pause") || t.contains("remov")) return "TARIFF_REDUCED";
        if (t.contains("request for comment") || t.contains("proposed") || t.contains("hearing")) return "TARIFF_PROPOSED";
        return "TARIFF_IMPOSED";
    }

    private TextSignals() {}
}
