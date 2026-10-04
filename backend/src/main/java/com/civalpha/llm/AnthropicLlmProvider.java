package com.civalpha.llm;

import com.anthropic.client.AnthropicClient;
import com.anthropic.client.okhttp.AnthropicOkHttpClient;
import com.anthropic.models.messages.Message;
import com.anthropic.models.messages.MessageCreateParams;
import com.anthropic.models.messages.OutputConfig;
import com.anthropic.models.messages.StopReason;
import com.anthropic.models.messages.StructuredMessageCreateParams;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.databind.json.JsonMapper;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Optional exposure extraction with Claude via the official Anthropic Java SDK. Output is constrained to a
 * JSON schema derived from {@link Hints}. Results are stored as ESTIMATED with at most MEDIUM confidence.
 * Any failure (network, refusal, schema) degrades to "no hints" so ingestion never depends on the LLM.
 */
public class AnthropicLlmProvider implements LlmProvider {

    private static final Logger log = LoggerFactory.getLogger(AnthropicLlmProvider.class);
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final Set<String> TYPES = Set.of("COUNTRY", "PRODUCT", "COST_INPUT", "SECTOR");
    private static final Set<String> CHANNELS = Set.of("REVENUE", "SUPPLY_CHAIN", "COST_INPUT", "FINANCING");

    public record Hint(String targetType, String targetCode, String channel, String confidence, String rationale) {}

    public record Hints(List<Hint> exposures) {}

    private final AnthropicClient client;
    private final String model;

    public AnthropicLlmProvider(String apiKey, String model) {
        this.client = AnthropicOkHttpClient.builder().apiKey(apiKey).build();
        this.model = model;
    }

    @Override
    public boolean enabled() {
        return true;
    }

    @Override
    public String name() {
        return "anthropic:" + model;
    }

    @Override
    public List<ExposureHint> extractExposures(String companyName, String passage) {
        String prompt = """
                You read one passage from an SEC filing by %s. List only exposures the passage itself states or \
                directly implies: countries (ISO 3166 alpha-2 codes, or EU for the European Union) where the company \
                earns revenue or sources/manufactures products, and products or cost inputs (UPPER_SNAKE_CASE, e.g. \
                SEMICONDUCTORS, STEEL, AUTOS, CONSUMER_ELECTRONICS) that trade measures could affect. targetType is one \
                of COUNTRY, PRODUCT, COST_INPUT, SECTOR; channel is one of REVENUE, SUPPLY_CHAIN, COST_INPUT, FINANCING; \
                confidence is LOW or MEDIUM. Quote the supporting words in rationale. Return an empty list if none.

                <passage>
                %s
                </passage>""".formatted(companyName, passage);
        try {
            StructuredMessageCreateParams<Hints> params = MessageCreateParams.builder()
                    .model(model)
                    .maxTokens(4000L)
                    .outputConfig(OutputConfig.builder().effort(OutputConfig.Effort.LOW).build())
                    .outputConfig(Hints.class)
                    .addUserMessage(prompt)
                    .build();
            return client.messages().create(params).content().stream()
                    .flatMap(b -> b.text().stream())
                    .flatMap(t -> t.text().exposures().stream())
                    .filter(h -> TYPES.contains(h.targetType()) && CHANNELS.contains(h.channel()) && h.targetCode() != null)
                    .map(h -> new ExposureHint(h.targetType(), h.targetCode().toUpperCase(), h.channel(),
                            "MEDIUM".equals(h.confidence()) ? "MEDIUM" : "LOW", h.rationale()))
                    .toList();
        } catch (RuntimeException e) {
            log.warn("LLM exposure extraction failed; continuing without hints: {}", e.toString());
            return List.of();
        }
    }

    @Override
    public String explainDecision(String companyName, String symbol, Map<String, Object> decision) {
        String prompt = """
                A machine-learning model has already decided what to do with %s (%s) today. Explain that decision \
                in 3 to 5 plain sentences for an investor who is not a data scientist. Use only the facts in the JSON \
                below: the action, the model's probability against its entry and exit thresholds, the factors that \
                moved the probability most (contribution = change in probability versus a typical value), and which \
                classic trading rules currently agree ("ruleVotes": true means that rule would hold the stock). Do \
                not add news, forecasts, price targets or advice of your own, and do not second-guess the decision.

                <decision>
                %s
                </decision>""";
        try {
            prompt = prompt.formatted(companyName, symbol, JSON.writeValueAsString(decision));
            MessageCreateParams params = MessageCreateParams.builder()
                    .model(model)
                    .maxTokens(4000L)
                    .outputConfig(OutputConfig.builder().effort(OutputConfig.Effort.LOW).build())
                    .addUserMessage(prompt)
                    .build();
            Message response = client.messages().create(params);
            if (response.stopReason().filter(StopReason.REFUSAL::equals).isPresent()) {
                log.warn("LLM declined to explain the decision for {}", symbol);
                return null;
            }
            String text = response.content().stream()
                    .flatMap(b -> b.text().stream())
                    .map(t -> t.text().strip())
                    .collect(Collectors.joining("\n\n"))
                    .strip();
            return text.isEmpty() ? null : text;
        } catch (RuntimeException e) {
            log.warn("LLM decision explanation failed; continuing without it: {}", e.toString());
            return null;
        }
    }
}
