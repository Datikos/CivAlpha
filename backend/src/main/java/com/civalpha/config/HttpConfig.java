package com.civalpha.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.net.http.HttpClient;
import java.time.Duration;

@Configuration
public class HttpConfig {

    @Bean
    public HttpClient httpClient() {
        return HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(20))
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    @Bean
    public RestClient mlRestClient(AppProperties props) {
        HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
        JdkClientHttpRequestFactory rf = new JdkClientHttpRequestFactory(client);
        rf.setReadTimeout(Duration.ofSeconds(props.ml().timeoutSeconds()));
        return RestClient.builder().baseUrl(props.ml().baseUrl()).requestFactory(rf).build();
    }
}
