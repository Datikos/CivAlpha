package com.civalpha.sec;

import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import java.io.ByteArrayInputStream;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Extracts numeric facts, including dimensional ones (e.g. srt:StatementGeographicalAxis), from an XBRL
 * instance document. companyfacts JSON omits dimensional facts, so geographic and segment revenue
 * comes from here. External entities are disabled (XXE-safe).
 */
public final class XbrlInstanceParser {

    private static final String XBRLI = "http://www.xbrl.org/2003/instance";
    private static final String XBRLDI = "http://xbrl.org/2006/xbrldi";

    record Context(LocalDate start, LocalDate end, Map<String, String> dims) {}

    public static List<FactRow> parse(byte[] xml, Set<String> concepts, String accession, String form, LocalDate filed) {
        Document doc = read(xml);
        Map<String, Context> contexts = new HashMap<>();
        NodeList ctx = doc.getElementsByTagNameNS(XBRLI, "context");
        for (int i = 0; i < ctx.getLength(); i++) {
            Element c = (Element) ctx.item(i);
            Map<String, String> dims = new LinkedHashMap<>();
            NodeList members = c.getElementsByTagNameNS(XBRLDI, "explicitMember");
            for (int j = 0; j < members.getLength(); j++) {
                Element m = (Element) members.item(j);
                dims.put(m.getAttribute("dimension"), m.getTextContent().trim());
            }
            LocalDate start = childDate(c, "startDate");
            LocalDate end = childDate(c, "endDate");
            LocalDate instant = childDate(c, "instant");
            contexts.put(c.getAttribute("id"), new Context(start, end != null ? end : instant, dims));
        }
        Map<String, String> units = new HashMap<>();
        NodeList us = doc.getElementsByTagNameNS(XBRLI, "unit");
        for (int i = 0; i < us.getLength(); i++) {
            Element u = (Element) us.item(i);
            NodeList measures = u.getElementsByTagNameNS(XBRLI, "measure");
            String m = measures.getLength() > 0 ? measures.item(0).getTextContent().trim() : u.getAttribute("id");
            units.put(u.getAttribute("id"), m.contains(":") ? m.substring(m.indexOf(':') + 1) : m);
        }
        List<FactRow> out = new ArrayList<>();
        NodeList all = doc.getDocumentElement().getChildNodes();
        for (int i = 0; i < all.getLength(); i++) {
            Node n = all.item(i);
            if (!(n instanceof Element e) || !e.hasAttribute("contextRef")) continue;
            String concept = e.getLocalName();
            if (concepts != null && !concepts.contains(concept)) continue;
            Context c = contexts.get(e.getAttribute("contextRef"));
            if (c == null || c.end() == null) continue;
            BigDecimal v;
            try {
                v = new BigDecimal(e.getTextContent().trim());
            } catch (NumberFormatException ex) {
                continue;
            }
            String prefix = e.getPrefix() == null ? "unknown" : e.getPrefix();
            out.add(new FactRow(prefix, concept, units.getOrDefault(e.getAttribute("unitRef"), "pure"), v, c.start(), c.end(),
                    null, null, form, filed, accession, c.dims()));
        }
        return out;
    }

    private static LocalDate childDate(Element ctx, String name) {
        NodeList l = ctx.getElementsByTagNameNS(XBRLI, name);
        return l.getLength() == 0 ? null : LocalDate.parse(l.item(0).getTextContent().trim().substring(0, 10));
    }

    private static Document read(byte[] xml) {
        try {
            DocumentBuilderFactory f = DocumentBuilderFactory.newInstance();
            f.setNamespaceAware(true);
            f.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
            f.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            f.setFeature("http://xml.org/sax/features/external-general-entities", false);
            f.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            f.setExpandEntityReferences(false);
            DocumentBuilder b = f.newDocumentBuilder();
            return b.parse(new ByteArrayInputStream(xml));
        } catch (Exception e) {
            throw new IllegalArgumentException("invalid XBRL instance: " + e.getMessage(), e);
        }
    }

    private XbrlInstanceParser() {}
}
