package com.civalpha.llm;

import java.util.List;

/** Optional language-model assistance. The application must work with the no-op implementation. */
public interface LlmProvider {
    boolean enabled();

    String name();

    /** Suggest company exposures described in a passage. Returns an empty list when unavailable. */
    List<ExposureHint> extractExposures(String companyName, String passage);
}
