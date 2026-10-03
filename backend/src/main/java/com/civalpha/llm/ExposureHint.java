package com.civalpha.llm;

/** An exposure suggested by an LLM from a filing passage. Always stored as ESTIMATED. */
public record ExposureHint(String targetType, String targetCode, String channel, String confidence, String rationale) {}
