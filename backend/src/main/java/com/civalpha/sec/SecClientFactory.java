package com.civalpha.sec;

import com.civalpha.config.AppProperties;
import org.springframework.stereotype.Component;

import java.net.http.HttpClient;
import java.nio.file.Path;

@Component
public class SecClientFactory {

    private final AppProperties props;
    private final HttpClient http;
    private volatile SecClient live;

    public SecClientFactory(AppProperties props, HttpClient http) {
        this.props = props;
        this.http = http;
    }

    /** The configured client: live EDGAR (requires SEC_USER_AGENT) or fixtures. */
    public SecClient configured() {
        if (props.sec().live()) {
            if (live == null) {
                synchronized (this) {
                    if (live == null) {
                        live = new LiveSecClient(http, props.sec().userAgent(), props.sec().maxRequestsPerSecond());
                    }
                }
            }
            return live;
        }
        return fixture(Path.of(props.sec().fixtureDir()));
    }

    public SecClient fixture(Path dir) {
        return new FixtureSecClient(dir);
    }
}
