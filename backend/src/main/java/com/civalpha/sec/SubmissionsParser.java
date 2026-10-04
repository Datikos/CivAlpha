package com.civalpha.sec;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;

/** Parses https://data.sec.gov/submissions/CIK##########.json ("filings.recent" column arrays). */
public final class SubmissionsParser {

    public record Submissions(String cik, String name, List<String> tickers, List<FilingMeta> filings) {}

    private static final ZoneId NEW_YORK = ZoneId.of("America/New_York");

    public static Submissions parse(ObjectMapper om, byte[] json) {
        JsonNode root = om.readTree(json);
        JsonNode r = root.path("filings").path("recent");
        JsonNode acc = r.path("accessionNumber");
        List<FilingMeta> out = new ArrayList<>();
        for (int i = 0; i < acc.size(); i++) {
            LocalDate filed = date(r.path("filingDate").path(i));
            OffsetDateTime accepted = acceptance(r.path("acceptanceDateTime").path(i), filed);
            out.add(new FilingMeta(acc.path(i).asString(), r.path("form").path(i).asString(), filed,
                    date(r.path("reportDate").path(i)), accepted, text(r.path("primaryDocument").path(i)),
                    text(r.path("items").path(i))));
        }
        List<String> tickers = new ArrayList<>();
        root.path("tickers").forEach(t -> tickers.add(t.asString()));
        return new Submissions(root.path("cik").asString(), root.path("name").asString(), tickers, out);
    }

    /**
     * EDGAR acceptance time. When it is missing we fall back to the end of the filing date in New York,
     * which is never earlier than the true acceptance (conservative for point-in-time use).
     */
    static OffsetDateTime acceptance(JsonNode n, LocalDate filed) {
        String s = text(n);
        if (s != null && !s.isBlank()) {
            return OffsetDateTime.parse(s.endsWith("Z") || s.contains("+") ? s : s + "Z");
        }
        return filed.atTime(23, 59, 59).atZone(NEW_YORK).toOffsetDateTime();
    }

    private static LocalDate date(JsonNode n) {
        String s = text(n);
        return s == null || s.isBlank() ? null : LocalDate.parse(s);
    }

    private static String text(JsonNode n) {
        return n == null || n.isMissingNode() || n.isNull() ? null : n.asString();
    }

    private SubmissionsParser() {}
}
