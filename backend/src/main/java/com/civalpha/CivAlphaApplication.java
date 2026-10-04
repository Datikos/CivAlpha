package com.civalpha;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class CivAlphaApplication {
    public static void main(String[] args) {
        SpringApplication.run(CivAlphaApplication.class, args);
    }
}
