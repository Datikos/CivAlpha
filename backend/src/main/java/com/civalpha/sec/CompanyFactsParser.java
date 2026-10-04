package com.civalpha.sec;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Parses https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json for a whitelist of concepts. */
public final class CompanyFactsParser {

    public static final Set<String> CONCEPTS = Set.of(
            "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "CostOfRevenue",
            "GrossProfit", "OperatingIncomeLoss", "NetIncomeLoss", "ResearchAndDevelopmentExpense", "InterestExpense",
            "Assets", "Liabilities", "LongTermDebtNoncurrent", "LongTermDebt", "CashAndCashEquivalentsAtCarryingValue",
            "EarningsPerShareDiluted", "EntityCommonStockSharesOutstanding");

    public static List<FactRow> parse(ObjectMapper om, byte[] json, Set<String> concepts) {
        JsonNode facts = om.readTree(json).path("facts");
        List<FactRow> out = new ArrayList<>();
        for (Map.Entry<String, JsonNode> tax : facts.properties()) {
            for (Map.Entry<String, JsonNode> concept : tax.getValue().properties()) {
                if (concepts != null && !concepts.contains(concept.getKey())) continue;
                for (Map.Entry<String, JsonNode> unit : concept.getValue().path("units").properties()) {
                    for (JsonNode e : unit.getValue()) {
                        if (!e.hasNonNull("accn") || !e.hasNonNull("end") || !e.hasNonNull("val")) continue;
                        out.add(new FactRow(tax.getKey(), concept.getKey(), unit.getKey(), e.get("val").decimalValue(),
                                e.hasNonNull("start") ? LocalDate.parse(e.get("start").asString()) : null,
                                LocalDate.parse(e.get("end").asString()),
                                e.hasNonNull("fy") ? e.get("fy").asInt() : null,
                                e.hasNonNull("fp") ? e.get("fp").asString() : null,
                                e.path("form").asString(), LocalDate.parse(e.get("filed").asString()),
                                e.get("accn").asString(), Map.of()));
                    }
                }
            }
        }
        return out;
    }

    private CompanyFactsParser() {}
}
