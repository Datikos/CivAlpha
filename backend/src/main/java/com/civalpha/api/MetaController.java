package com.civalpha.api;

import com.civalpha.config.AppProperties;
import com.civalpha.demo.Pipeline;
import com.civalpha.llm.LlmProvider;
import com.civalpha.market.MarketDataService;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
public class MetaController {

    private final JdbcClient jdbc;
    private final Pipeline pipeline;
    private final AppProperties props;
    private final LlmProvider llm;
    private final MarketDataService market;

    public MetaController(JdbcClient jdbc, Pipeline pipeline, AppProperties props, LlmProvider llm, MarketDataService market) {
        this.jdbc = jdbc;
        this.pipeline = pipeline;
        this.props = props;
        this.llm = llm;
        this.market = market;
    }

    @GetMapping("/api/meta")
    public Map<String, Object> meta() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("demoDataPresent", pipeline.demoPresent());
        m.put("llmEnabled", llm.enabled());
        m.put("llmProvider", llm.name());
        m.put("secMode", props.sec().mode());
        m.put("fredEnabled", props.fred().enabled());
        m.put("adminTokenRequired", props.security() != null && props.security().enabled());
        m.put("missingBenchmarks", market.missingBenchmarks());
        m.put("priceProvider", props.prices() != null && props.prices().enabled() ? props.prices().provider().toLowerCase() : "none");
        m.put("dataCutoff", jdbc.sql("SELECT max(trade_date) FROM price_bar").query(LocalDate.class).optional().orElse(null));
        m.put("target", "P(21-trading-day total return of the stock > total return of its sector benchmark ETF), "
                + "measured from the close of the as-of date to the close 21 trading days later");
        m.put("disclaimers", List.of(
                "Research software. Not investment advice. No brokerage connection or order placement.",
                "Forecasts are model outputs with stated uncertainty; recorded facts and model estimates are labelled separately.",
                "Profitability is not claimed unless the cost-adjusted walk-forward evidence supports it.",
                "Market data use for model training and public display depends on your data licence."));
        return m;
    }
}
