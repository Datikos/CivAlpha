package com.civalpha.llm;

import com.civalpha.config.AppProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class LlmConfig {
    @Bean
    public LlmProvider llmProvider(AppProperties props) {
        return props.llm().enabled() ? new AnthropicLlmProvider(props.llm().apiKey(), props.llm().model()) : new NoopLlmProvider();
    }
}
