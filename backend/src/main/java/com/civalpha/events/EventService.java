package com.civalpha.events;

import com.civalpha.storage.DocumentStore;
import com.civalpha.storage.SourceDocument;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Records policy events with their evidence. Every event keeps a link to at least one stored source
 * document. Reports of an event already on record are attached to it instead of creating a duplicate; an
 * official document arriving for a news-only event upgrades its evidence status (new event version).
 */
@Service
public class EventService {

    private final JdbcClient jdbc;
    private final DocumentStore docs;
    private final ObjectMapper om;

    public EventService(JdbcClient jdbc, DocumentStore docs, ObjectMapper om) {
        this.jdbc = jdbc;
        this.docs = docs;
        this.om = om;
    }

    public record IngestResult(long eventId, boolean deduplicated, boolean upgraded, boolean created) {}

    @Transactional
    public IngestResult ingest(EventDraft d, boolean demo) {
        if (d.source() == null || (d.source().url() == null)) {
            throw new IllegalArgumentException("an event needs a source URL (original evidence)");
        }
        SourceDocument doc = docs.store(new DocumentStore.NewDocument(d.source().official() ? "OFFICIAL_EVENT" : "NEWS",
                d.source().publisher(), d.source().url(), null, d.source().title(), d.source().publishedAt(),
                d.source().contentType(), d.source().content(), demo || d.source().demo()));
        List<EventDeduplicator.Existing> nearby = nearby(d.category(), d.eventDate());
        var dup = EventDeduplicator.findDuplicate(d, nearby);
        if (dup.isPresent()) {
            long id = dup.get();
            linkSource(id, doc.id(), d.source().role());
            boolean upgraded = false;
            String status = jdbc.sql("SELECT evidence_status FROM policy_event WHERE id = :id").param("id", id).query(String.class).single();
            if (d.source().official() && "NEWS_ONLY".equals(status)) {
                jdbc.sql("""
                        UPDATE policy_event SET evidence_status = 'OFFICIAL', published_at = :p, version = version + 1,
                               attributes = attributes || CAST(:a AS jsonb) WHERE id = :id""")
                        .param("p", d.publishedAt()).param("a", json(d.attributes())).param("id", id).update();
                insertTargets(id, d.targets());
                recordActor(id, d, doc, demo);
                upgraded = true;
            }
            return new IngestResult(id, true, upgraded, false);
        }
        Long actorId = d.actorName() == null ? null : actorId(d.actorName());
        long id = jdbc.sql("""
                INSERT INTO policy_event (category, event_type, title, summary, actor_id, event_date, published_at, evidence_status,
                                          attributes, dedup_key, is_demo)
                VALUES (:c, :t, :title, :s, :actor, :d, :p, :status, CAST(:a AS jsonb), :key, :demo) RETURNING id""")
                .param("c", d.category()).param("t", d.eventType()).param("title", d.title()).param("s", d.summary())
                .param("actor", actorId).param("d", d.eventDate()).param("p", d.publishedAt())
                .param("status", d.source().official() ? "OFFICIAL" : "NEWS_ONLY").param("a", json(d.attributes()))
                .param("key", d.category() + "|" + d.eventDate() + "|" + String.join(" ", EventDeduplicator.tokens(d.title())))
                .param("demo", demo).query(Long.class).single();
        insertTargets(id, d.targets());
        linkSource(id, doc.id(), d.source().role());
        if (d.source().official()) recordActor(id, d, doc, demo);
        return new IngestResult(id, false, false, true);
    }

    private List<EventDeduplicator.Existing> nearby(String category, LocalDate date) {
        List<Map<String, Object>> rows = jdbc.sql("""
                SELECT e.id, e.category, e.event_type, e.title, e.event_date,
                       (SELECT string_agg(t.target_type || ':' || t.target_code, ',') FROM event_target t WHERE t.event_id = e.id) AS targets,
                       (SELECT string_agg(sd.url, ' ') FROM event_source es JOIN source_document sd ON sd.id = es.source_document_id
                         WHERE es.event_id = e.id) AS urls
                FROM policy_event e WHERE e.category = :c AND e.event_date BETWEEN :from AND :to""")
                .param("c", category).param("from", date.minusDays(7)).param("to", date.plusDays(7)).query().listOfRows();
        return rows.stream().map(r -> new EventDeduplicator.Existing(((Number) r.get("id")).longValue(), (String) r.get("category"),
                (String) r.get("event_type"), (String) r.get("title"), ((java.sql.Date) r.get("event_date")).toLocalDate(),
                split((String) r.get("targets"), ","), split((String) r.get("urls"), " "))).toList();
    }

    private static Set<String> split(String s, String sep) {
        return s == null ? Set.of() : new HashSet<>(Arrays.asList(s.split(sep)));
    }

    private void insertTargets(long eventId, List<EventDraft.Target> targets) {
        if (targets == null) return;
        for (EventDraft.Target t : targets) {
            int exists = jdbc.sql("SELECT count(*) FROM event_target WHERE event_id = :e AND target_type = :t AND target_code = :c")
                    .param("e", eventId).param("t", t.targetType()).param("c", t.targetCode()).query(Integer.class).single();
            if (exists == 0) {
                jdbc.sql("INSERT INTO event_target (event_id, target_type, target_code, magnitude) VALUES (:e, :t, :c, :m)")
                        .param("e", eventId).param("t", t.targetType()).param("c", t.targetCode()).param("m", t.magnitude()).update();
            }
        }
    }

    private void linkSource(long eventId, long docId, String role) {
        jdbc.sql("INSERT INTO event_source (event_id, source_document_id, role) VALUES (:e, :d, :r) ON CONFLICT DO NOTHING")
                .param("e", eventId).param("d", docId).param("r", role).update();
    }

    /** Public decision profile: documented actions only (the official document itself), never inferred traits. */
    private void recordActor(long eventId, EventDraft d, SourceDocument doc, boolean demo) {
        if (d.actorName() == null) return;
        long actor = actorId(d.actorName());
        jdbc.sql("UPDATE policy_event SET actor_id = :a WHERE id = :e AND actor_id IS NULL").param("a", actor).param("e", eventId).update();
        jdbc.sql("""
                INSERT INTO actor_record (actor_id, record_type, occurred_at, summary, source_document_id, is_demo)
                VALUES (:a, :t, :at, :s, :d, :demo)""")
                .param("a", actor).param("t", "MONETARY_POLICY".equals(d.category()) ? "VOTE" : "ACTION")
                .param("at", d.publishedAt()).param("s", d.title() + (votes(d.attributes()))).param("d", doc.id()).param("demo", demo).update();
    }

    private static String votes(Map<String, Object> attrs) {
        if (attrs == null || !attrs.containsKey("votes_for")) return "";
        return " (recorded vote: " + attrs.get("votes_for") + " for, " + attrs.getOrDefault("votes_against", 0) + " against)";
    }

    public long actorId(String name) {
        return jdbc.sql("SELECT id FROM policy_actor WHERE name = :n").param("n", name).query(Long.class).optional()
                .orElseGet(() -> jdbc.sql("INSERT INTO policy_actor (name, actor_type) VALUES (:n, 'INSTITUTION') RETURNING id")
                        .param("n", name).query(Long.class).single());
    }

    public void upsertActor(String name, String type, String authority, String affiliation, String note) {
        long id = actorId(name);
        jdbc.sql("UPDATE policy_actor SET actor_type = :t, authority = :a, affiliation = :f, profile_note = :n WHERE id = :id")
                .param("t", type).param("a", authority).param("f", affiliation).param("n", note).param("id", id).update();
    }

    /** Companies whose current exposures match the event's targets. */
    public List<Long> affectedCompanies(long eventId) {
        return jdbc.sql("""
                SELECT DISTINCT x.company_id FROM event_target t
                JOIN company_exposure x ON x.target_type = t.target_type AND x.target_code = t.target_code
                WHERE t.event_id = :e ORDER BY 1""").param("e", eventId).query(Long.class).list();
    }

    private String json(Map<String, Object> m) {
        return om.writeValueAsString(m == null ? Map.of() : m);
    }

    public static OffsetDateTime now() {
        return OffsetDateTime.now(java.time.ZoneOffset.UTC);
    }
}
