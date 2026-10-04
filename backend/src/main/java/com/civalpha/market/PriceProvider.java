package com.civalpha.market;

import java.time.LocalDate;
import java.util.List;

/**
 * Replaceable source of daily bars and corporate actions. Whether a provider's data may be used for model
 * training or shown publicly is a licensing decision recorded in configuration, not implied by API access.
 */
public interface PriceProvider {

    String name();

    /**
     * True when the provider's closes are already split-adjusted. Splits are then stored as SPLIT_INFO
     * (informational) so they are not applied a second time, and a newly announced split triggers a full
     * re-download so all stored bars share the same adjustment.
     */
    boolean splitAdjusted();

    Series fetch(String symbol, LocalDate from, LocalDate to) throws Exception;

    /** locator = the request URL with any credential removed (stored as provenance). */
    record Series(List<PriceBarRow> bars, List<CsvPrices.ActionRow> actions, byte[] raw, String contentType, String locator) {}

    /** Vendor symbols use '-' for share classes (BRK.B -> BRK-B). */
    static String vendorSymbol(String symbol) {
        return symbol.trim().toUpperCase().replace('.', '-');
    }
}
