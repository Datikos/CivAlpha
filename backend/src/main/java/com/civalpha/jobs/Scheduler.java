package com.civalpha.jobs;

import com.civalpha.demo.Pipeline;
import com.civalpha.forecast.MlClient;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

import java.util.Map;

/**
 * Optional recurring runs, disabled unless a cron is configured (CIVALPHA_PIPELINE_CRON / CIVALPHA_OUTCOMES_CRON).
 * Runs go through JobService, so they are logged like manual runs and never overlap a running job of the same type.
 */
@Configuration
@EnableScheduling
public class Scheduler {

    private final JobService jobs;
    private final Pipeline pipeline;
    private final MlClient ml;

    public Scheduler(JobService jobs, Pipeline pipeline, MlClient ml) {
        this.jobs = jobs;
        this.pipeline = pipeline;
        this.ml = ml;
    }

    @Scheduled(cron = "${civalpha.schedule.pipeline-cron:-}", zone = "${civalpha.schedule.zone:America/New_York}")
    public void pipeline() {
        if (jobs.running("PIPELINE_RUN")) return;
        jobs.submit("PIPELINE_RUN", Map.of("trigger", "schedule"), pipeline::runConfigured);
    }

    @Scheduled(cron = "${civalpha.schedule.outcomes-cron:-}", zone = "${civalpha.schedule.zone:America/New_York}")
    public void outcomes() {
        if (jobs.running("RESOLVE_OUTCOMES")) return;
        jobs.submit("RESOLVE_OUTCOMES", Map.of("trigger", "schedule"), log -> log.accept(String.valueOf(ml.resolveOutcomes())));
    }
}
