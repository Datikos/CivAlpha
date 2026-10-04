package com.civalpha.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

@ConfigurationProperties(prefix = "civalpha")
public record AppProperties(
        String universeFile,
        Storage storage,
        Sec sec,
        Ml ml,
        Demo demo,
        Fred fred,
        Events events,
        Llm llm,
        Security security,
        Schedule schedule) {

    public record Storage(String documentsDir, String importsDir) {}

    /** SEC EDGAR access. Live mode requires a User-Agent that identifies the requester (SEC fair-access policy). */
    public record Sec(String mode, String userAgent, double maxRequestsPerSecond, String fixtureDir, int lookbackYears) {
        public boolean live() { return "live".equalsIgnoreCase(mode); }
    }

    public record Ml(String baseUrl, int timeoutSeconds) {}

    public record Demo(String dataDir, int replayMonths) {}

    public record Fred(String apiKey, String baseUrl, List<String> series) {
        public boolean enabled() { return apiKey != null && !apiKey.isBlank(); }
    }

    public record Events(boolean federalRegisterEnabled, boolean fedRssEnabled, List<String> newsFeeds) {}

    /** adminToken: when set, admin endpoints and all write requests need it (header X-Admin-Token or Bearer). */
    public record Security(String adminToken) {
        public boolean enabled() { return adminToken != null && !adminToken.isBlank(); }
    }

    /** Spring cron expressions ("-" disables). Times are evaluated in `zone`. */
    public record Schedule(String pipelineCron, String outcomesCron, String zone) {}

    public record Llm(String provider, String model, String apiKey) {
        public boolean enabled() { return "anthropic".equalsIgnoreCase(provider) && apiKey != null && !apiKey.isBlank(); }
    }
}
