package com.civalpha.llm;

import java.util.List;

public class NoopLlmProvider implements LlmProvider {
    @Override
    public boolean enabled() {
        return false;
    }

    @Override
    public String name() {
        return "none";
    }

    @Override
    public List<ExposureHint> extractExposures(String companyName, String passage) {
        return List.of();
    }
}
