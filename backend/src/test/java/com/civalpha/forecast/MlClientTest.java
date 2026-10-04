package com.civalpha.forecast;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;

class MlClientTest {

    @Test
    void surfacesTheMlServiceExplanationInsteadOfTheHttpException() {
        RestClient.Builder builder = RestClient.builder().baseUrl("http://ml");
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("http://ml/evaluate")).andRespond(withStatus(HttpStatus.CONFLICT)
                .contentType(MediaType.APPLICATION_JSON).body("{\"detail\":\"No benchmark ETF prices are loaded.\"}"));
        server.expect(requestTo("http://ml/outcomes/resolve")).andRespond(withStatus(HttpStatus.INTERNAL_SERVER_ERROR)
                .contentType(MediaType.TEXT_PLAIN).body("Internal Server Error"));
        MlClient ml = new MlClient(builder.build());

        assertThatThrownBy(ml::evaluate).isInstanceOf(IllegalStateException.class)
                .hasMessage("No benchmark ETF prices are loaded.");
        assertThatThrownBy(ml::resolveOutcomes).isInstanceOf(IllegalStateException.class)
                .hasMessage("ML service error 500: Internal Server Error");
    }
}
