package com.civalpha.sec;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Finds the XBRL instance of a filing from its directory listing (Archives/.../index.json). Inline-XBRL
 * filings publish an extracted instance named "*_htm.xml"; older filings ship a standalone instance next to
 * the linkbases (_cal/_def/_lab/_pre.xml) and the schema (.xsd), which must be skipped.
 */
public final class XbrlInstanceLocator {

    private static final Pattern NOT_INSTANCE = Pattern.compile("(?i)(_cal|_def|_lab|_pre|_ref)\\.xml$|^FilingSummary\\.xml$|^MetaLinks|\\.xsd$");

    public static List<String> names(ObjectMapper om, byte[] indexJson) {
        List<String> out = new ArrayList<>();
        for (JsonNode item : om.readTree(indexJson).path("directory").path("item")) out.add(item.path("name").asString());
        return out;
    }

    public static Optional<String> pick(List<String> names, String primaryDocument) {
        if (primaryDocument != null) {
            String expected = primaryDocument.replaceAll("\\.htm$", "_htm.xml");
            if (names.contains(expected)) return Optional.of(expected);
        }
        Optional<String> htmXml = names.stream().filter(n -> n.endsWith("_htm.xml")).findFirst();
        if (htmXml.isPresent()) return htmXml;
        return names.stream().filter(n -> n.toLowerCase().endsWith(".xml") && !NOT_INSTANCE.matcher(n).find()).findFirst();
    }

    /** Conventional name used when no directory listing is available (e.g. fixtures). */
    public static String conventional(String primaryDocument) {
        return primaryDocument.replaceAll("\\.htm$", "_htm.xml");
    }

    private XbrlInstanceLocator() {}
}
