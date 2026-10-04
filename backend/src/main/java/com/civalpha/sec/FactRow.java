package com.civalpha.sec;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.Map;

/** One reported XBRL value. dimensions is empty for the consolidated (non-dimensional) value. */
public record FactRow(String taxonomy, String concept, String unit, BigDecimal value, LocalDate periodStart,
                      LocalDate periodEnd, Integer fiscalYear, String fiscalPeriod, String form, LocalDate filed,
                      String accessionNo, Map<String, String> dimensions) {

    public String dimsKey() {
        if (dimensions == null || dimensions.isEmpty()) return "";
        return dimensions.entrySet().stream().sorted(Map.Entry.comparingByKey())
                .map(e -> e.getKey() + "=" + e.getValue()).reduce((a, b) -> a + "|" + b).orElse("");
    }
}
