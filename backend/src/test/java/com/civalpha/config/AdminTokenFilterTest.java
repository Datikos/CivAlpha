package com.civalpha.config;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.assertj.core.api.Assertions.assertThat;

class AdminTokenFilterTest {

    private static AppProperties props(String token) {
        return new AppProperties(null, null, null, null, null, null, null, null, new AppProperties.Security(token), null, null);
    }

    private static int status(AdminTokenFilter f, String method, String uri, String header, String value) throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest(method, uri);
        if (header != null) req.addHeader(header, value);
        MockHttpServletResponse res = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();
        f.doFilter(req, res, chain);
        return chain.getRequest() == null ? res.getStatus() : 200;
    }

    @Test
    void protectsAdminAndWritesWhenTokenConfigured() throws Exception {
        AdminTokenFilter f = new AdminTokenFilter(props("s3cret"));
        assertThat(status(f, "GET", "/api/forecasts/current", null, null)).isEqualTo(200);
        assertThat(status(f, "GET", "/api/admin/jobs", null, null)).isEqualTo(401);
        assertThat(status(f, "POST", "/api/events", null, null)).isEqualTo(401);
        assertThat(status(f, "POST", "/api/admin/demo/load", "X-Admin-Token", "wrong")).isEqualTo(401);
        assertThat(status(f, "POST", "/api/admin/demo/load", "X-Admin-Token", "s3cret")).isEqualTo(200);
        assertThat(status(f, "POST", "/api/events", "Authorization", "Bearer s3cret")).isEqualTo(200);
        assertThat(status(f, "GET", "/index.html", null, null)).isEqualTo(200);
    }

    @Test
    void openWhenNoTokenConfigured() throws Exception {
        AdminTokenFilter f = new AdminTokenFilter(props(""));
        assertThat(status(f, "POST", "/api/admin/demo/load", null, null)).isEqualTo(200);
    }
}
