package com.civalpha.events;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

/** A candidate policy event with exactly one piece of evidence (its source). */
public record EventDraft(String category, String eventType, String title, String summary, LocalDate eventDate,
                         OffsetDateTime publishedAt, String actorName, Map<String, Object> attributes,
                         List<Target> targets, Source source) {

    public record Target(String targetType, String targetCode, Double magnitude) {}

    /** role: OFFICIAL_PRIMARY | OFFICIAL_SUPPORTING | NEWS_DISCOVERY. content may be null (metadata only). */
    public record Source(String url, String title, String publisher, String role, OffsetDateTime publishedAt,
                         byte[] content, String contentType, boolean demo) {
        public boolean official() {
            return role != null && role.startsWith("OFFICIAL");
        }
    }
}
