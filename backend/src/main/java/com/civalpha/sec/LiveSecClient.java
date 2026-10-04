package com.civalpha.sec;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Optional;
import java.util.regex.Pattern;
import java.util.zip.GZIPInputStream;

/**
 * SEC EDGAR over HTTPS, following the SEC's automated-access rules: a declared User-Agent naming the
 * requester and a contact e-mail, at most 10 requests/second (default 5), gzip, and back-off on 429/503.
 */
public class LiveSecClient implements SecClient {

    private static final Logger log = LoggerFactory.getLogger(LiveSecClient.class);
    private static final Pattern UA = Pattern.compile(".+\\s+\\S+@\\S+\\.\\S+");
    private final HttpClient http;
    private final String userAgent;
    private final RateLimiter limiter;

    public LiveSecClient(HttpClient http, String userAgent, double maxRps) {
        if (userAgent == null || !UA.matcher(userAgent.trim()).matches()) {
            throw new IllegalArgumentException(
                    "SEC_USER_AGENT must identify you, e.g. 'CivAlpha research jane@example.com' (SEC automated access policy)");
        }
        this.http = http;
        this.userAgent = userAgent.trim();
        this.limiter = new RateLimiter(maxRps);
    }

    @Override
    public String mode() {
        return "live";
    }

    @Override
    public Optional<byte[]> get(String url) {
        for (int attempt = 0; attempt < 4; attempt++) {
            limiter.acquire();
            HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                    .header("User-Agent", userAgent)
                    .header("Accept-Encoding", "gzip")
                    .timeout(Duration.ofSeconds(60))
                    .GET().build();
            try {
                HttpResponse<byte[]> res = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
                int s = res.statusCode();
                if (s == 200) {
                    byte[] body = res.body();
                    if (res.headers().firstValue("Content-Encoding").map(v -> v.contains("gzip")).orElse(false)) {
                        try (GZIPInputStream in = new GZIPInputStream(new java.io.ByteArrayInputStream(body))) {
                            body = in.readAllBytes();
                        }
                    }
                    return Optional.of(body);
                }
                if (s == 404) return Optional.empty();
                if (s == 429 || s == 503) {
                    long backoff = (long) Math.pow(2, attempt + 1) * 1000;
                    log.warn("SEC returned {} for {}; backing off {} ms", s, url, backoff);
                    Thread.sleep(backoff);
                    continue;
                }
                throw new IllegalStateException("SEC request failed: HTTP " + s + " for " + url);
            } catch (IOException e) {
                log.warn("SEC request error for {}: {}", url, e.toString());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(e);
            }
        }
        throw new IllegalStateException("SEC request failed after retries: " + url);
    }
}
