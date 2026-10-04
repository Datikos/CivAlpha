package com.civalpha.macro;

import com.civalpha.config.AppProperties;
import com.civalpha.storage.DocumentStore;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

/**
 * Macro series with full vintage history (ALFRED real-time periods), so a forecast for date D only sees
 * values as they were published on D, not later revisions.
 */
@Service
public class MacroService {

    private final NamedParameterJdbcTemplate named;
    private final DocumentStore docs;
    private final AppProperties props;
    private final HttpClient http;
    private final ObjectMapper om;

    public MacroService(NamedParameterJdbcTemplate named, DocumentStore docs, AppProperties props, HttpClient http, ObjectMapper om) {
        this.named = named;
        this.docs = docs;
        this.props = props;
        this.http = http;
        this.om = om;
    }

    public record Observation(String seriesId, LocalDate obsDate, Double value, LocalDate realtimeStart, LocalDate realtimeEnd) {}

    public void upsertSeries(String id, String title, String units, String frequency, String source) {
        named.update("""
                INSERT INTO macro_series (series_id, title, units, frequency, source) VALUES (:id, :t, :u, :f, :s)
                ON CONFLICT (series_id) DO UPDATE SET title = EXCLUDED.title, units = EXCLUDED.units, frequency = EXCLUDED.frequency""",
                new MapSqlParameterSource().addValue("id", id).addValue("t", title).addValue("u", units).addValue("f", frequency).addValue("s", source));
    }

    public int insert(List<Observation> obs, boolean demo) {
        MapSqlParameterSource[] batch = obs.stream().map(o -> new MapSqlParameterSource()
                .addValue("s", o.seriesId()).addValue("d", o.obsDate()).addValue("v", o.value())
                .addValue("rs", o.realtimeStart()).addValue("re", o.realtimeEnd()).addValue("demo", demo)).toArray(MapSqlParameterSource[]::new);
        int[] r = named.batchUpdate("""
                INSERT INTO macro_observation (series_id, obs_date, value, realtime_start, realtime_end, is_demo)
                VALUES (:s, :d, :v, :rs, :re, :demo)
                ON CONFLICT (series_id, obs_date, realtime_start) DO UPDATE SET realtime_end = EXCLUDED.realtime_end""", batch);
        int n = 0;
        for (int x : r) n += Math.max(0, x);
        return n;
    }

    /** CSV columns: series_id,obs_date,value,realtime_start,realtime_end (empty end = still current). */
    public static List<Observation> parseCsv(String csv) {
        List<Observation> out = new ArrayList<>();
        String[] lines = csv.split("\\R");
        for (int i = 1; i < lines.length; i++) {
            if (lines[i].isBlank()) continue;
            String[] c = lines[i].split(",", -1);
            out.add(new Observation(c[0], LocalDate.parse(c[1]), c[2].isBlank() || ".".equals(c[2]) ? null : Double.parseDouble(c[2]),
                    LocalDate.parse(c[3]), c.length > 4 && !c[4].isBlank() ? LocalDate.parse(c[4]) : null));
        }
        return out;
    }

    /** Fetches all vintages of a FRED series through the ALFRED real-time period parameters. Requires FRED_API_KEY. */
    public int fetchFred(String seriesId) throws IOException, InterruptedException {
        if (!props.fred().enabled()) throw new IllegalStateException("FRED_API_KEY is not set");
        String base = props.fred().baseUrl();
        String key = URLEncoder.encode(props.fred().apiKey(), StandardCharsets.UTF_8);
        JsonNode meta = om.readTree(get(base + "/series?file_type=json&series_id=" + seriesId + "&api_key=" + key)).path("seriess").path(0);
        upsertSeries(seriesId, meta.path("title").asString(seriesId), meta.path("units").asString(null), meta.path("frequency").asString(null), "FRED/ALFRED");
        String url = base + "/series/observations?file_type=json&series_id=" + seriesId
                + "&realtime_start=1776-07-04&realtime_end=9999-12-31&observation_start=2015-01-01&api_key=" + key;
        byte[] body = get(url);
        // the stored locator omits the API key
        docs.store(new DocumentStore.NewDocument("MACRO", "FRED/ALFRED (Federal Reserve Bank of St. Louis)",
                base + "/series/observations?series_id=" + seriesId + "&realtime_start=1776-07-04&realtime_end=9999-12-31",
                null, seriesId + " all vintages", null, "application/json", body, false));
        List<Observation> obs = new ArrayList<>();
        for (JsonNode o : om.readTree(body).path("observations")) {
            String v = o.path("value").asString();
            String end = o.path("realtime_end").asString();
            obs.add(new Observation(seriesId, LocalDate.parse(o.path("date").asString()), ".".equals(v) ? null : Double.parseDouble(v),
                    LocalDate.parse(o.path("realtime_start").asString()), "9999-12-31".equals(end) ? null : LocalDate.parse(end)));
        }
        return insert(obs, false);
    }

    private byte[] get(String url) throws IOException, InterruptedException {
        HttpResponse<byte[]> r = http.send(HttpRequest.newBuilder(URI.create(url)).GET().build(), HttpResponse.BodyHandlers.ofByteArray());
        if (r.statusCode() != 200) throw new IOException("FRED HTTP " + r.statusCode());
        return r.body();
    }
}
