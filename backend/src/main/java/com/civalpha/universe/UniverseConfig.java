package com.civalpha.universe;

import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Parsed config/universe.yml. */
public record UniverseConfig(String universe, LocalDate startDate, Map<String, String> benchmarks, List<Entry> companies) {

    public record TickerSpan(String symbol, LocalDate validFrom, LocalDate validTo) {}

    public record Entry(String symbol, String name, String cik, String sector, String industry, String benchmark,
                        List<TickerSpan> tickerHistory) {}

    @SuppressWarnings("unchecked")
    public static UniverseConfig load(Path file) throws IOException {
        Map<String, Object> y = new Yaml(new SafeConstructor(new LoaderOptions())).load(Files.readString(file));
        List<Entry> entries = new ArrayList<>();
        for (Map<String, Object> c : (List<Map<String, Object>>) y.get("companies")) {
            List<TickerSpan> hist = new ArrayList<>();
            Object th = c.get("ticker_history");
            if (th instanceof List<?> l) {
                for (Object o : l) {
                    Map<String, Object> m = (Map<String, Object>) o;
                    hist.add(new TickerSpan((String) m.get("symbol"), date(m.get("valid_from")), date(m.get("valid_to"))));
                }
            }
            entries.add(new Entry((String) c.get("symbol"), (String) c.get("name"), String.valueOf(c.get("cik")),
                    (String) c.get("sector"), (String) c.get("industry"), (String) c.get("benchmark"), hist));
        }
        return new UniverseConfig((String) y.get("universe"), date(y.get("start_date")),
                (Map<String, String>) y.get("benchmarks"), entries);
    }

    private static LocalDate date(Object o) {
        if (o == null) return null;
        if (o instanceof java.util.Date d) return d.toInstant().atZone(java.time.ZoneOffset.UTC).toLocalDate();
        if (o instanceof LocalDate d) return d;
        return LocalDate.parse(o.toString());
    }
}
