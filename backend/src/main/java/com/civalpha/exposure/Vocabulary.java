package com.civalpha.exposure;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;

/** Country/product vocabulary shared by exposure extraction and event target extraction. */
public final class Vocabulary {

    public static final Map<String, Pattern> COUNTRIES = new LinkedHashMap<>();
    public static final Map<String, Pattern> PRODUCTS = new LinkedHashMap<>();

    static {
        c("CN", "china|chinese|people's republic of china|prc\\b");
        c("TW", "taiwan");
        c("HK", "hong kong");
        c("MX", "mexic");
        c("CA", "canad");
        c("JP", "japan");
        c("KR", "south korea|korea\\b|korean");
        c("VN", "vietnam|viet nam");
        c("IN", "\\bindia\\b|indian");
        c("MY", "malaysia");
        c("SG", "singapore");
        c("IE", "ireland|irish");
        c("DE", "germany|german\\b");
        c("EU", "european union|\\beurope\\b|european");
        c("PR", "puerto rico");
        c("US", "united states|\\bu\\.s\\.(?! dollar)");
        p("SEMICONDUCTORS", "semiconductor|\\bchips?\\b|integrated circuit|foundr");
        p("STEEL", "steel|aluminum|aluminium");
        p("AUTOS", "automotive|electric vehicle|\\bvehicles?\\b|auto parts");
        p("CONSUMER_ELECTRONICS", "smartphone|consumer electronic|personal computer|laptop|electronics");
    }

    private static void c(String code, String regex) {
        COUNTRIES.put(code, Pattern.compile(regex, Pattern.CASE_INSENSITIVE));
    }

    private static void p(String code, String regex) {
        PRODUCTS.put(code, Pattern.compile(regex, Pattern.CASE_INSENSITIVE));
    }

    /** Industry key (universe config) -> product key used by PRODUCT event targets. */
    public static Optional<String> productForIndustry(String industry) {
        if (industry == null) return Optional.empty();
        return Optional.ofNullable(switch (industry) {
            case "SEMICONDUCTORS", "SEMICONDUCTOR_EQUIPMENT" -> "SEMICONDUCTORS";
            case "CONSUMER_ELECTRONICS", "NETWORKING_HARDWARE" -> "CONSUMER_ELECTRONICS";
            case "AUTOS" -> "AUTOS";
            default -> null;
        });
    }

    /**
     * Maps an XBRL geographic member to a country/region code. Standard members look like "country:CN";
     * company-specific members are matched by name (e.g. aapl:GreaterChinaMember -> CN, flagged approximate).
     */
    public record GeoMatch(String code, boolean exact) {}

    public static Optional<GeoMatch> geoMember(String member) {
        if (member == null) return Optional.empty();
        if (member.startsWith("country:")) {
            return Optional.of(new GeoMatch(member.substring("country:".length()).toUpperCase(), true));
        }
        String local = member.contains(":") ? member.substring(member.indexOf(':') + 1) : member;
        String m = local.replace("Member", "").replaceAll("([a-z])([A-Z])", "$1 $2").toLowerCase();
        if (m.contains("rest of") || m.contains("other") || m.contains("americas") || m.contains("international")) return Optional.empty();
        for (Map.Entry<String, Pattern> e : COUNTRIES.entrySet()) {
            if (e.getValue().matcher(m).find()) return Optional.of(new GeoMatch(e.getKey(), false));
        }
        return Optional.empty();
    }

    private Vocabulary() {}
}
