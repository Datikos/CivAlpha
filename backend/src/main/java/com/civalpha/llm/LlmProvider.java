package com.civalpha.llm;

import java.util.List;
import java.util.Map;

/** Optional language-model assistance. The application must work with the no-op implementation. */
public interface LlmProvider {
    boolean enabled();

    String name();

    /** Suggest company exposures described in a passage. Returns an empty list when unavailable. */
    List<ExposureHint> extractExposures(String companyName, String passage);

    /**
     * Plain-language explanation of a decision the model already made. It must only restate the decision's
     * own inputs; it never changes the decision. Returns null when unavailable.
     */
    String explainDecision(String companyName, String symbol, Map<String, Object> decision);
}
