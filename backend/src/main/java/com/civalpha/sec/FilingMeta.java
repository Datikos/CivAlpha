package com.civalpha.sec;

import java.time.LocalDate;
import java.time.OffsetDateTime;

public record FilingMeta(String accessionNo, String form, LocalDate filingDate, LocalDate reportDate,
                         OffsetDateTime acceptedAt, String primaryDocument, String items) {

    public boolean isPeriodic() {
        return form.startsWith("10-K") || form.startsWith("10-Q");
    }

    public boolean isAmendment() {
        return form.endsWith("/A");
    }

    public boolean isAnnual() {
        return form.startsWith("10-K");
    }
}
