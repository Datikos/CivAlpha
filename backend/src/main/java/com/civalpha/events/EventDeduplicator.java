package com.civalpha.events;

import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Decides whether a draft describes an event already on record (e.g. a news wire re-reporting an official
 * notice). Deterministic rules, in order:
 *   1. a shared source URL;
 *   2. monetary policy: same category, same event type and same decision date (one decision per meeting);
 *   3. within +/-3 days and same category: title similarity >= 0.5, or similarity >= 0.2 with
 *      overlapping targets, or (draft without targets) similarity >= 0.3 within +/-2 days.
 */
public final class EventDeduplicator {

    public record Existing(long id, String category, String eventType, String title, LocalDate eventDate,
                           Set<String> targetKeys, Set<String> sourceUrls) {}

    private static final Set<String> STOP = Set.of("the", "a", "an", "of", "on", "and", "to", "for", "in", "new", "from", "with",
            "by", "report", "wire", "announced", "announces", "says", "demo", "is", "are", "at", "its", "as", "notice", "statement");

    public static Optional<Long> findDuplicate(EventDraft d, List<Existing> existing) {
        Set<String> dTargets = d.targets() == null ? Set.of()
                : d.targets().stream().map(t -> t.targetType() + ":" + t.targetCode()).collect(Collectors.toSet());
        Long best = null;
        double bestScore = 0;
        for (Existing e : existing) {
            if (d.source() != null && d.source().url() != null && e.sourceUrls().contains(d.source().url())) return Optional.of(e.id());
            if (!e.category().equals(d.category())) continue;
            long days = Math.abs(ChronoUnit.DAYS.between(e.eventDate(), d.eventDate()));
            if ("MONETARY_POLICY".equals(d.category()) && days == 0 && e.eventType().equals(d.eventType())) return Optional.of(e.id());
            if (days > 3) continue;
            double sim = similarity(d.title(), e.title());
            boolean match = sim >= 0.5
                    || (sim >= 0.2 && !dTargets.isEmpty() && jaccard(dTargets, e.targetKeys()) >= 0.5)
                    || (sim >= 0.3 && dTargets.isEmpty() && days <= 2);
            if (match && sim > bestScore) {
                best = e.id();
                bestScore = sim;
            }
        }
        return Optional.ofNullable(best);
    }

    public static double similarity(String a, String b) {
        return jaccard(tokens(a), tokens(b));
    }

    static Set<String> tokens(String s) {
        if (s == null) return Set.of();
        return Arrays.stream(s.toLowerCase().replaceAll("[^a-z0-9 ]", " ").split("\\s+"))
                .filter(t -> t.length() > 1 && !STOP.contains(t))
                .map(EventDeduplicator::stem).collect(Collectors.toSet());
    }

    private static String stem(String t) {
        if (t.endsWith("ies") && t.length() > 4) return t.substring(0, t.length() - 3) + "y";
        if (t.endsWith("es") && t.length() > 4) return t.substring(0, t.length() - 2);
        if (t.endsWith("s") && t.length() > 3) return t.substring(0, t.length() - 1);
        return t;
    }

    static double jaccard(Set<String> a, Set<String> b) {
        if (a.isEmpty() && b.isEmpty()) return 0;
        Set<String> i = new HashSet<>(a);
        i.retainAll(b);
        Set<String> u = new HashSet<>(a);
        u.addAll(b);
        return (double) i.size() / u.size();
    }

    private EventDeduplicator() {}
}
