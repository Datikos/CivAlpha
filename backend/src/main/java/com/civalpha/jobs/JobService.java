package com.civalpha.jobs;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/** Runs pipeline steps in the background, one at a time, with a persisted log. */
@Service
public class JobService {

    private static final Logger log = LoggerFactory.getLogger(JobService.class);
    private final JdbcClient jdbc;
    private final ObjectMapper om;
    private final ExecutorService executor = Executors.newSingleThreadExecutor(Thread.ofVirtual().name("job-", 0).factory());

    public JobService(JdbcClient jdbc, ObjectMapper om) {
        this.jdbc = jdbc;
        this.om = om;
        // jobs interrupted by a restart can never finish
        jdbc.sql("UPDATE pipeline_job SET status = 'FAILED', finished_at = now(), log = log || 'interrupted by restart\n' WHERE status = 'RUNNING'").update();
    }

    public interface Body {
        void run(Consumer<String> log) throws Exception;
    }

    public Map<String, Object> submit(String type, Map<String, Object> params, Body body) {
        long id = jdbc.sql("INSERT INTO pipeline_job (job_type, status, params) VALUES (:t, 'RUNNING', CAST(:p AS jsonb)) RETURNING id")
                .param("t", type).param("p", om.writeValueAsString(params)).query(Long.class).single();
        executor.submit(() -> {
            Consumer<String> logLine = line -> {
                log.info("job {} {}: {}", id, type, line);
                jdbc.sql("UPDATE pipeline_job SET log = log || :l WHERE id = :id")
                        .param("l", OffsetDateTime.now(ZoneOffset.UTC).withNano(0) + "  " + line + "\n").param("id", id).update();
            };
            try {
                body.run(logLine);
                jdbc.sql("UPDATE pipeline_job SET status = 'SUCCEEDED', finished_at = now() WHERE id = :id").param("id", id).update();
            } catch (Throwable e) {
                log.error("job {} failed", id, e);
                logLine.accept("FAILED: " + e);
                jdbc.sql("UPDATE pipeline_job SET status = 'FAILED', finished_at = now() WHERE id = :id").param("id", id).update();
            }
        });
        return get(id);
    }

    public boolean running(String type) {
        return jdbc.sql("SELECT exists(SELECT 1 FROM pipeline_job WHERE job_type = :t AND status = 'RUNNING')")
                .param("t", type).query(Boolean.class).single();
    }

    public Map<String, Object> get(long id) {
        return jdbc.sql("SELECT * FROM pipeline_job WHERE id = :id").param("id", id).query().singleRow();
    }

    public List<Map<String, Object>> recent() {
        return jdbc.sql("SELECT * FROM pipeline_job ORDER BY id DESC LIMIT 30").query().listOfRows();
    }
}
