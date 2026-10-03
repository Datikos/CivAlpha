package com.civalpha.api;

import com.civalpha.events.EventDraft;
import com.civalpha.events.EventService;
import com.civalpha.forecast.ForecastService;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

@RestController
public class EventController {

    private static final Set<String> CATEGORIES = Set.of("MONETARY_POLICY", "TRADE_TARIFF");
    private final JdbcClient jdbc;
    private final Rows rows;
    private final EventService events;
    private final ForecastService forecasts;
    private final HttpClient http;

    public EventController(JdbcClient jdbc, Rows rows, EventService events, ForecastService forecasts, HttpClient http) {
        this.jdbc = jdbc;
        this.rows = rows;
        this.events = events;
        this.forecasts = forecasts;
        this.http = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).connectTimeout(Duration.ofSeconds(10)).build();
    }

    private static final String LIST_SQL = """
            SELECT e.id, e.category, e.event_type, e.title, e.event_date, e.published_at, e.first_seen_at, e.evidence_status,
                   a.name AS actor_name, e.version, e.is_demo, e.summary, e.attributes,
                   (SELECT count(*) FROM event_source s WHERE s.event_id = e.id) AS source_count,
                   (SELECT count(DISTINCT x.company_id) FROM event_target t JOIN company_exposure x
                      ON x.target_type = t.target_type AND x.target_code = t.target_code WHERE t.event_id = e.id) AS affected_company_count
            FROM policy_event e LEFT JOIN policy_actor a ON a.id = e.actor_id""";

    @GetMapping("/api/events")
    public List<Map<String, Object>> list(@RequestParam(required = false) String category) {
        List<Map<String, Object>> list = rows.camel(jdbc.sql(LIST_SQL + " WHERE (CAST(:cat AS text) IS NULL OR e.category = :cat) ORDER BY e.published_at DESC")
                .param("cat", category).query().listOfRows());
        Map<Long, List<Map<String, Object>>> targets = new LinkedHashMap<>();
        for (Map<String, Object> t : rows.camel(jdbc.sql("SELECT event_id, target_type, target_code, magnitude FROM event_target ORDER BY id").query().listOfRows())) {
            targets.computeIfAbsent(((Number) t.remove("eventId")).longValue(), k -> new ArrayList<>()).add(t);
        }
        for (Map<String, Object> e : list) {
            e.remove("summary");
            e.remove("attributes");
            e.put("targets", targets.getOrDefault(((Number) e.get("id")).longValue(), List.of()));
        }
        return list;
    }

    @GetMapping("/api/events/{id}")
    public Map<String, Object> get(@PathVariable long id) {
        Map<String, Object> e = rows.camel(jdbc.sql(LIST_SQL + " WHERE e.id = :id").param("id", id).query().listOfRows().stream()
                .findFirst().orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND)));
        e.put("targets", rows.camel(jdbc.sql("SELECT target_type, target_code, magnitude, note FROM event_target WHERE event_id = :id ORDER BY id")
                .param("id", id).query().listOfRows()));
        Long actorId = jdbc.sql("SELECT actor_id FROM policy_event WHERE id = :id").param("id", id).query(Long.class).optional().orElse(null);
        if (actorId != null) {
            Map<String, Object> actor = rows.camel(jdbc.sql("SELECT id, name, actor_type, authority, affiliation, profile_note FROM policy_actor WHERE id = :a")
                    .param("a", actorId).query().singleRow());
            actor.put("records", jdbc.sql("""
                    SELECT r.record_type, r.occurred_at, r.summary, sd.id AS doc_id, sd.url, sd.title, sd.is_demo
                    FROM actor_record r JOIN source_document sd ON sd.id = r.source_document_id
                    WHERE r.actor_id = :a ORDER BY r.occurred_at DESC LIMIT 25""").param("a", actorId).query().listOfRows().stream().map(r -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("recordType", r.get("record_type"));
                m.put("occurredAt", rows.value(r.get("occurred_at")));
                m.put("summary", r.get("summary"));
                m.put("source", Map.of("id", r.get("doc_id"), "title", r.get("title") == null ? "" : r.get("title"),
                        "url", Boolean.TRUE.equals(r.get("is_demo")) ? "/api/documents/" + r.get("doc_id") : r.get("url")));
                return m;
            }).toList());
            e.put("actor", actor);
        } else {
            e.put("actor", null);
        }
        e.put("sources", jdbc.sql("""
                SELECT sd.id, s.role, sd.source_type, sd.publisher, sd.title, sd.url, sd.accession_no, sd.published_at, sd.ingested_at,
                       sd.version, sd.content_sha256, sd.storage_path, sd.is_demo
                FROM event_source s JOIN source_document sd ON sd.id = s.source_document_id WHERE s.event_id = :id ORDER BY s.linked_at""")
                .param("id", id).query().listOfRows().stream().map(r -> {
                    Map<String, Object> m = rows.camel(r);
                    Object path = m.remove("storagePath");
                    m.put("documentUrl", path == null ? null : "/api/documents/" + m.get("id"));
                    return m;
                }).toList());
        Map<String, Map<String, Object>> affected = new LinkedHashMap<>();
        for (Map<String, Object> r : jdbc.sql("""
                SELECT DISTINCT ON (x.company_id, x.target_type, x.target_code, x.exposure_channel)
                       x.company_id, c.name, (SELECT symbol FROM ticker_history th WHERE th.company_id = c.id ORDER BY valid_from DESC LIMIT 1) AS symbol,
                       t.target_type, t.target_code, x.id AS exposure_id, x.exposure_channel AS channel, x.basis, x.confidence, x.share, x.passage_id
                FROM event_target t
                JOIN company_exposure x ON x.target_type = t.target_type AND x.target_code = t.target_code
                JOIN policy_event e ON e.id = t.event_id
                JOIN company c ON c.id = x.company_id
                WHERE t.event_id = :id AND x.available_at <= greatest(e.published_at, now())
                ORDER BY x.company_id, x.target_type, x.target_code, x.exposure_channel, x.available_at DESC""").param("id", id).query().listOfRows()) {
            Map<String, Object> co = affected.computeIfAbsent((String) r.get("symbol"), s -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("symbol", s);
                m.put("name", r.get("name"));
                m.put("paths", new ArrayList<Map<String, Object>>());
                return m;
            });
            Map<String, Object> p = rows.camel(r);
            p.remove("companyId");
            p.remove("name");
            p.remove("symbol");
            @SuppressWarnings("unchecked") List<Map<String, Object>> paths = (List<Map<String, Object>>) co.get("paths");
            paths.add(p);
        }
        e.put("affectedCompanies", new ArrayList<>(affected.values()));
        return e;
    }

    public record TargetIn(String targetType, String targetCode, Double magnitude) {}

    public record SourceIn(String url, String title, String publisher, String role) {}

    public record EventIn(String category, String eventType, String title, String summary, LocalDate eventDate,
                          OffsetDateTime publishedAt, String actorName, Map<String, Object> attributes,
                          List<TargetIn> targets, SourceIn source) {}

    /** Refuse to fetch from loopback/private/link-local addresses (SSRF guard); the URL is still recorded. */
    static boolean publicHost(String host) {
        try {
            for (java.net.InetAddress a : java.net.InetAddress.getAllByName(host)) {
                if (a.isLoopbackAddress() || a.isSiteLocalAddress() || a.isLinkLocalAddress() || a.isAnyLocalAddress()
                        || a.isMulticastAddress() || (a.getAddress().length == 4 && (a.getAddress()[0] & 0xff) == 100 && (a.getAddress()[1] & 0xc0) == 64)) {
                    return false;
                }
            }
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /** Adds a sourced event. The source URL is mandatory; its content is fetched and stored when reachable. */
    @PostMapping("/api/events")
    public Map<String, Object> create(@RequestBody EventIn in) {
        if (in.source() == null || in.source().url() == null || !in.source().url().matches("https?://.+")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "source.url (http/https) is required: every event must link to its original evidence");
        }
        if (in.category() == null || !CATEGORIES.contains(in.category())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "category must be one of " + CATEGORIES);
        }
        if (in.title() == null || in.title().isBlank() || in.eventDate() == null || in.publishedAt() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "title, eventDate and publishedAt are required");
        }
        String role = in.source().role() == null ? "OFFICIAL_PRIMARY" : in.source().role();
        byte[] content = null;
        String contentType = null;
        try {
            if (!publicHost(URI.create(in.source().url()).getHost())) throw new IllegalArgumentException("non-public host");
            HttpResponse<byte[]> r = http.send(HttpRequest.newBuilder(URI.create(in.source().url())).timeout(Duration.ofSeconds(20))
                    .header("User-Agent", "CivAlpha research").GET().build(), HttpResponse.BodyHandlers.ofByteArray());
            if (r.statusCode() == 200 && r.body().length < 20_000_000) {
                content = r.body();
                contentType = r.headers().firstValue("Content-Type").orElse("application/octet-stream");
            }
        } catch (Exception ignored) {
            // stored as metadata-only evidence; the URL is still retained
        }
        EventDraft d = new EventDraft(in.category(), in.eventType() == null ? "OTHER" : in.eventType(), in.title(), in.summary(),
                in.eventDate(), in.publishedAt(), in.actorName(), in.attributes(),
                in.targets() == null ? List.of() : in.targets().stream().map(t -> new EventDraft.Target(t.targetType(), t.targetCode(), t.magnitude())).toList(),
                new EventDraft.Source(in.source().url(), in.source().title() == null ? in.title() : in.source().title(),
                        in.source().publisher() == null ? URI.create(in.source().url()).getHost() : in.source().publisher(), role,
                        in.publishedAt(), content, contentType, false));
        var res = events.ingest(d, false);
        List<Long> reissued = List.of();
        boolean official = jdbc.sql("SELECT evidence_status = 'OFFICIAL' FROM policy_event WHERE id = :id").param("id", res.eventId()).query(Boolean.class).single();
        if (official && (res.created() || res.upgraded())) {
            List<Long> affected = events.affectedCompanies(res.eventId());
            boolean anyForecasts = jdbc.sql("SELECT exists(SELECT 1 FROM forecast)").query(Boolean.class).single();
            if (!affected.isEmpty() && anyForecasts) {
                try {
                    reissued = forecasts.issueLive(affected, "New evidence: event #" + res.eventId() + " (" + in.title() + ")").ids();
                } catch (RuntimeException ex) {
                    reissued = List.of();
                }
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("event", get(res.eventId()));
        out.put("deduplicated", res.deduplicated());
        out.put("reissuedForecastIds", reissued);
        return out;
    }
}
