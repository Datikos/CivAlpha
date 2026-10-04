package com.civalpha.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Optional shared-secret protection. When CIVALPHA_ADMIN_TOKEN is set, /api/admin/** (any method) and every
 * non-GET /api request must carry it as "X-Admin-Token: <token>" or "Authorization: Bearer <token>".
 * Read-only endpoints stay public. Without a token everything is open, which is only safe on localhost.
 */
@Component
public class AdminTokenFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(AdminTokenFilter.class);
    private final byte[] token;

    public AdminTokenFilter(AppProperties props) {
        this.token = props.security() != null && props.security().enabled()
                ? props.security().adminToken().getBytes(StandardCharsets.UTF_8) : null;
        if (token == null) {
            log.warn("CIVALPHA_ADMIN_TOKEN is not set: admin and write endpoints are unauthenticated (keep the stack on localhost)");
        }
    }

    static boolean protectedRequest(HttpServletRequest req) {
        String path = req.getRequestURI();
        if (!path.startsWith("/api/")) return false;
        if (path.startsWith("/api/admin/")) return true;
        String m = req.getMethod();
        return !("GET".equals(m) || "HEAD".equals(m) || "OPTIONS".equals(m));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain) throws ServletException, IOException {
        if (token == null || !protectedRequest(req)) {
            chain.doFilter(req, res);
            return;
        }
        String presented = req.getHeader("X-Admin-Token");
        String auth = req.getHeader("Authorization");
        if (presented == null && auth != null && auth.startsWith("Bearer ")) presented = auth.substring(7).trim();
        if (presented != null && MessageDigest.isEqual(token, presented.getBytes(StandardCharsets.UTF_8))) {
            chain.doFilter(req, res);
            return;
        }
        res.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        res.setContentType("application/json");
        res.getWriter().write("{\"error\":\"admin token required (X-Admin-Token header)\"}");
    }
}
