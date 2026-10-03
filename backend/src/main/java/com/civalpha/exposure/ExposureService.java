package com.civalpha.exposure;

import com.civalpha.llm.ExposureHint;
import com.civalpha.llm.LlmProvider;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Derives company exposures from one filing. Every exposure is tied to the filing (and a passage or XBRL
 * fact when available), becomes available at the filing's acceptance time, and is labelled either
 * DIRECTLY_REPORTED (a reported number, e.g. revenue by country) or ESTIMATED (inferred from text,
 * ratios, the company's industry, or an LLM) with a confidence level.
 */
@Service
public class ExposureService {

    private static final Pattern SUPPLY = Pattern.compile("manufactur|assembl|supplier|suppl(y|ies)|sourc|contract manufactur|foundr|fabricat", Pattern.CASE_INSENSITIVE);
    private static final Pattern REVENUE = Pattern.compile("revenue|net sales|customers in|demand", Pattern.CASE_INSENSITIVE);
    private static final Pattern SUBSTANTIAL = Pattern.compile("substantially all|majority of|most of", Pattern.CASE_INSENSITIVE);
    private static final Pattern SIGNIFICANT = Pattern.compile("significant portion|significant|material share|material portion", Pattern.CASE_INSENSITIVE);

    private final JdbcClient jdbc;
    private final LlmProvider llm;

    public ExposureService(JdbcClient jdbc, LlmProvider llm) {
        this.jdbc = jdbc;
        this.llm = llm;
    }

    record Candidate(String targetType, String targetCode, String channel, BigDecimal share, String basis, String confidence,
                     String method, Long passageId, Long factId, String rationale) {}

    @Transactional
    public int deriveForFiling(long filingId) {
        Map<String, Object> f = jdbc.sql("""
                SELECT f.id, f.company_id, f.form_type, f.accepted_at, f.period_of_report, f.is_demo, c.industry, c.name
                FROM filing f JOIN company c ON c.id = f.company_id WHERE f.id = :id""").param("id", filingId).query().singleRow();
        long companyId = ((Number) f.get("company_id")).longValue();
        String form = (String) f.get("form_type");
        OffsetDateTime acceptedAt = toOdt(f.get("accepted_at"));
        boolean demo = (Boolean) f.get("is_demo");
        LocalDate period = f.get("period_of_report") == null ? null : ((java.sql.Date) f.get("period_of_report")).toLocalDate();
        if (jdbc.sql("SELECT count(*) FROM company_exposure WHERE filing_id = :f").param("f", filingId).query(Integer.class).single() > 0) {
            return 0; // already derived (idempotent)
        }
        List<Candidate> cands = new ArrayList<>();
        if (form.startsWith("10-K")) {
            cands.addAll(fromGeographicFacts(filingId));
            cands.addAll(fromLeverage(filingId, companyId));
            Vocabulary.productForIndustry((String) f.get("industry")).ifPresent(p -> cands.add(new Candidate("PRODUCT", p, "REVENUE", null,
                    "ESTIMATED", "MEDIUM", "SECTOR_MAP", null, null, "Company industry (" + f.get("industry") + ") sells products in this category")));
        }
        if (form.startsWith("10-K") || form.startsWith("10-Q") || form.startsWith("8-K")) {
            cands.addAll(fromPassages(filingId, (String) f.get("name")));
        }
        // de-duplicate by (type, code, channel): prefer directly reported, then higher confidence
        Map<String, Candidate> best = new java.util.LinkedHashMap<>();
        for (Candidate c : cands) {
            String k = c.targetType() + "|" + c.targetCode() + "|" + c.channel();
            Candidate prev = best.get(k);
            if (prev == null || rank(c) > rank(prev)) best.put(k, c);
        }
        int n = 0;
        for (Candidate c : best.values()) {
            int version = jdbc.sql("""
                    SELECT coalesce(max(version), 0) + 1 FROM company_exposure
                    WHERE company_id = :c AND target_type = :t AND target_code = :code AND exposure_channel = :ch""")
                    .param("c", companyId).param("t", c.targetType()).param("code", c.targetCode()).param("ch", c.channel())
                    .query(Integer.class).single();
            jdbc.sql("""
                    INSERT INTO company_exposure (company_id, target_type, target_code, exposure_channel, share, basis, confidence,
                        method, filing_id, passage_id, xbrl_fact_id, available_at, period_end, rationale, version, is_demo)
                    VALUES (:c, :t, :code, :ch, :share, :basis, :conf, :m, :f, :p, :x, :at, :pe, :r, :v, :demo)""")
                    .param("c", companyId).param("t", c.targetType()).param("code", c.targetCode()).param("ch", c.channel())
                    .param("share", c.share()).param("basis", c.basis()).param("conf", c.confidence()).param("m", c.method())
                    .param("f", filingId).param("p", c.passageId()).param("x", c.factId()).param("at", acceptedAt)
                    .param("pe", period).param("r", c.rationale()).param("v", version).param("demo", demo).update();
            n++;
        }
        return n;
    }

    private static int rank(Candidate c) {
        int r = "DIRECTLY_REPORTED".equals(c.basis()) ? 100 : 0;
        r += switch (c.confidence()) { case "HIGH" -> 30; case "MEDIUM" -> 20; default -> 10; };
        return r + (c.share() != null ? 1 : 0);
    }

    /** Revenue by geography from dimensional XBRL facts: member value / consolidated total for the same period. */
    List<Candidate> fromGeographicFacts(long filingId) {
        List<Map<String, Object>> rows = jdbc.sql("""
                SELECT id, concept, value, period_start, period_end, dimensions->>'srt:StatementGeographicalAxis' AS member
                FROM xbrl_fact WHERE filing_id = :f
                  AND concept IN ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet')
                  AND (dims_key = '' OR dimensions->>'srt:StatementGeographicalAxis' IS NOT NULL)""").param("f", filingId).query().listOfRows();
        Long passage = firstPassage(filingId, "GEOGRAPHIC_REVENUE");
        List<Candidate> out = new ArrayList<>();
        for (Map<String, Object> r : rows) {
            String member = (String) r.get("member");
            if (member == null) continue;
            BigDecimal total = rows.stream().filter(t -> t.get("member") == null && t.get("concept").equals(r.get("concept"))
                            && t.get("period_end").equals(r.get("period_end")) && java.util.Objects.equals(t.get("period_start"), r.get("period_start")))
                    .map(t -> (BigDecimal) t.get("value")).findFirst().orElse(null);
            if (total == null || total.signum() <= 0) continue;
            Vocabulary.geoMember(member).ifPresent(g -> {
                BigDecimal share = ((BigDecimal) r.get("value")).divide(total, 4, RoundingMode.HALF_UP);
                out.add(new Candidate("COUNTRY", g.code(), "REVENUE", share, "DIRECTLY_REPORTED", g.exact() ? "HIGH" : "MEDIUM",
                        "XBRL_DIMENSION", passage, ((Number) r.get("id")).longValue(),
                        "Revenue tagged " + member + " / consolidated revenue" + (g.exact() ? "" : " (region mapped to nearest country code)")));
            });
        }
        return out;
    }

    /** Rate sensitivity proxy: long-term debt / assets reported in the filing (the ratio is reported; the sensitivity is inferred). */
    List<Candidate> fromLeverage(long filingId, long companyId) {
        List<Map<String, Object>> rows = jdbc.sql("""
                SELECT id, concept, value FROM xbrl_fact WHERE filing_id = :f AND dims_key = '' AND period_start IS NULL
                  AND concept IN ('LongTermDebtNoncurrent', 'LongTermDebt', 'Assets') ORDER BY period_end DESC""")
                .param("f", filingId).query().listOfRows();
        Map<String, Object> debt = rows.stream().filter(r -> ((String) r.get("concept")).startsWith("LongTermDebt")).findFirst().orElse(null);
        Map<String, Object> assets = rows.stream().filter(r -> "Assets".equals(r.get("concept"))).findFirst().orElse(null);
        if (debt == null || assets == null || ((BigDecimal) assets.get("value")).signum() <= 0) return List.of();
        BigDecimal ratio = ((BigDecimal) debt.get("value")).divide((BigDecimal) assets.get("value"), 4, RoundingMode.HALF_UP);
        Long passage = firstPassage(filingId, "RATES");
        if (passage == null) passage = firstPassage(filingId, "DEBT");
        return List.of(new Candidate("INTEREST_RATE", "US_POLICY_RATE", "FINANCING", ratio, "ESTIMATED", "MEDIUM", "XBRL_RATIO",
                passage, ((Number) debt.get("id")).longValue(), "Long-term debt / total assets = " + ratio + " (reported values); "
                + "sensitivity to policy rates is inferred"));
    }

    /** Keyword rules over extracted passages (and optional LLM hints). Always ESTIMATED. */
    List<Candidate> fromPassages(long filingId, String companyName) {
        List<Map<String, Object>> ps = jdbc.sql("SELECT id, topic, text FROM filing_passage WHERE filing_id = :f AND topic IN ('TRADE','COSTS','RISK','GEOGRAPHIC_REVENUE')")
                .param("f", filingId).query().listOfRows();
        List<Candidate> out = new ArrayList<>();
        Set<String> llmDone = new HashSet<>();
        for (Map<String, Object> p : ps) {
            long pid = ((Number) p.get("id")).longValue();
            String text = (String) p.get("text");
            for (String sentence : text.split("(?<=[.;])\\s+")) {
                boolean supply = SUPPLY.matcher(sentence).find();
                boolean revenue = REVENUE.matcher(sentence).find();
                if (!supply && !revenue) continue;
                String conf = SUBSTANTIAL.matcher(sentence).find() ? "MEDIUM" : (SIGNIFICANT.matcher(sentence).find() ? "MEDIUM" : "LOW");
                BigDecimal share = SUBSTANTIAL.matcher(sentence).find() ? new BigDecimal("0.60")
                        : (SIGNIFICANT.matcher(sentence).find() ? new BigDecimal("0.35") : null);
                for (Map.Entry<String, Pattern> c : Vocabulary.COUNTRIES.entrySet()) {
                    if ("US".equals(c.getKey())) continue; // domestic operations are not a trade exposure
                    Matcher m = c.getValue().matcher(sentence);
                    if (!m.find()) continue;
                    String channel = supply ? "SUPPLY_CHAIN" : "REVENUE";
                    out.add(new Candidate("COUNTRY", c.getKey(), channel, share, "ESTIMATED", conf, "RULE_KEYWORD", pid, null,
                            "Filing text: \"" + abbreviate(sentence) + "\""));
                }
                for (Map.Entry<String, Pattern> pr : Vocabulary.PRODUCTS.entrySet()) {
                    if (pr.getValue().matcher(sentence).find() && supply) {
                        out.add(new Candidate("PRODUCT", pr.getKey(), "COST_INPUT", null, "ESTIMATED", "LOW", "RULE_KEYWORD", pid, null,
                                "Filing text: \"" + abbreviate(sentence) + "\""));
                    }
                }
            }
            if (llm.enabled() && "TRADE".equals(p.get("topic")) && llmDone.add(text)) {
                for (ExposureHint h : llm.extractExposures(companyName, text)) {
                    out.add(new Candidate(h.targetType(), h.targetCode(), h.channel(), null, "ESTIMATED", h.confidence(), "LLM", pid, null,
                            llm.name() + ": " + abbreviate(h.rationale())));
                }
            }
        }
        return out;
    }

    private Long firstPassage(long filingId, String topic) {
        return jdbc.sql("SELECT id FROM filing_passage WHERE filing_id = :f AND topic = :t ORDER BY id LIMIT 1")
                .param("f", filingId).param("t", topic).query(Long.class).optional().orElse(null);
    }

    private static String abbreviate(String s) {
        if (s == null) return "";
        return s.length() > 300 ? s.substring(0, 300) + "…" : s;
    }

    static OffsetDateTime toOdt(Object o) {
        if (o instanceof OffsetDateTime odt) return odt;
        if (o instanceof java.sql.Timestamp ts) return ts.toInstant().atOffset(java.time.ZoneOffset.UTC);
        if (o instanceof java.time.Instant i) return i.atOffset(java.time.ZoneOffset.UTC);
        throw new IllegalArgumentException("unexpected timestamp type " + o);
    }
}
