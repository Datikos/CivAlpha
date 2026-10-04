package com.civalpha.api;

import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;

/** Time machine runs: forecasts made as of a past close, next to what actually happened. */
@RestController
public class TimeMachineController {

    private final JdbcClient jdbc;
    private final Rows rows;

    public TimeMachineController(JdbcClient jdbc, Rows rows) {
        this.jdbc = jdbc;
        this.rows = rows;
    }

    @GetMapping("/api/timemachine")
    public List<Map<String, Object>> runs() {
        return rows.camel(jdbc.sql("""
                SELECT id, as_of_date, run_at, data_cutoff, headline, is_demo FROM time_machine_run ORDER BY id DESC LIMIT 50""")
                .query().listOfRows());
    }

    @GetMapping("/api/timemachine/{id}")
    public Map<String, Object> run(@PathVariable long id) {
        return jdbc.sql("SELECT id, as_of_date, run_at, data_cutoff, headline, is_demo, result FROM time_machine_run WHERE id = :id")
                .param("id", id).query().listOfRows().stream().findFirst().map(rows::camel)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "no time machine run " + id));
    }
}
