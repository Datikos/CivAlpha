package com.civalpha.market;

import java.math.BigDecimal;
import java.time.LocalDate;

public record PriceBarRow(String symbol, LocalDate date, BigDecimal open, BigDecimal high, BigDecimal low, BigDecimal close, Long volume) {}
