package com.civalpha.api;

import org.postgresql.util.PGobject;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Converts JDBC rows to API maps: snake_case -> camelCase, SQL types -> JSON-friendly types, is_demo -> isDemo. */
@Component
public class Rows {

    private final ObjectMapper om;

    public Rows(ObjectMapper om) {
        this.om = om;
    }

    public Map<String, Object> camel(Map<String, Object> row) {
        Map<String, Object> out = new LinkedHashMap<>();
        row.forEach((k, v) -> out.put(camelKey(k), value(v)));
        return out;
    }

    public List<Map<String, Object>> camel(List<Map<String, Object>> rows) {
        return rows.stream().map(this::camel).toList();
    }

    public Object value(Object v) {
        if (v instanceof java.sql.Timestamp ts) return ts.toInstant().atOffset(ZoneOffset.UTC);
        if (v instanceof java.sql.Date d) return d.toLocalDate();
        if (v instanceof BigDecimal b) return b.doubleValue();
        if (v instanceof PGobject pg && pg.getValue() != null && pg.getType() != null && pg.getType().startsWith("json")) {
            return om.readValue(pg.getValue(), Object.class);
        }
        if (v instanceof PGobject pg) return pg.getValue();
        return v;
    }

    static String camelKey(String k) {
        if (k.equals("is_demo")) return "isDemo";
        StringBuilder b = new StringBuilder();
        boolean up = false;
        for (char c : k.toCharArray()) {
            if (c == '_') {
                up = true;
            } else {
                b.append(up ? Character.toUpperCase(c) : c);
                up = false;
            }
        }
        return b.toString();
    }
}
