package com.civalpha.sec;

import java.util.Optional;

/** Fetches SEC EDGAR resources by their canonical URL. Implementations: live HTTP or local fixtures. */
public interface SecClient {
    String SUBMISSIONS = "https://data.sec.gov/submissions/CIK%s.json";
    String COMPANY_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json";
    String COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json";
    String ARCHIVE = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s";

    /** @return body bytes, or empty when the resource does not exist (HTTP 404 / missing fixture). */
    Optional<byte[]> get(String url);

    String mode();

    static String archiveUrl(String cik, String accession, String document) {
        return ARCHIVE.formatted(Long.parseLong(cik), accession.replace("-", ""), document);
    }
}
