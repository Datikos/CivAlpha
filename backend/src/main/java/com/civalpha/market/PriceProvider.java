package com.civalpha.market;

import java.time.LocalDate;
import java.util.List;

/**
 * Replaceable source of daily bars. The MVP ships a CSV implementation so it runs without a paid key.
 * A licensed vendor adapter implements this interface; whether its data may be used for model training
 * and shown publicly is a licensing/procurement decision recorded in configuration, not implied by API access.
 */
public interface PriceProvider {
    String name();

    List<PriceBarRow> dailyBars(String symbol, LocalDate from, LocalDate to);
}
