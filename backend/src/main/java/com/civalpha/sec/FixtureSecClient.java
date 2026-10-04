package com.civalpha.sec;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;

/**
 * Serves SEC URLs from a local directory laid out like the SEC hosts:
 * data.sec.gov/submissions/... -> submissions/..., data.sec.gov/api/xbrl/companyfacts/... -> companyfacts/...,
 * www.sec.gov/Archives/... -> Archives/..., www.sec.gov/files/... -> files/...
 * Used for the demo dataset and for offline tests; parsing code is identical to live mode.
 */
public class FixtureSecClient implements SecClient {

    private final Path root;

    public FixtureSecClient(Path root) {
        this.root = root;
    }

    @Override
    public String mode() {
        return "fixture";
    }

    @Override
    public Optional<byte[]> get(String url) {
        Path p = resolve(url);
        if (p == null || !Files.isRegularFile(p)) return Optional.empty();
        try {
            return Optional.of(Files.readAllBytes(p));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    Path resolve(String url) {
        URI u = URI.create(url);
        String path = u.getPath();
        String rel;
        if ("data.sec.gov".equals(u.getHost()) && path.startsWith("/submissions/")) {
            rel = path.substring(1);
        } else if ("data.sec.gov".equals(u.getHost()) && path.startsWith("/api/xbrl/companyfacts/")) {
            rel = "companyfacts/" + path.substring("/api/xbrl/companyfacts/".length());
        } else if ("www.sec.gov".equals(u.getHost()) && (path.startsWith("/Archives/") || path.startsWith("/files/"))) {
            rel = path.substring(1);
        } else {
            return null;
        }
        Path p = root.resolve(rel).normalize();
        return p.startsWith(root.normalize()) ? p : null;
    }
}
