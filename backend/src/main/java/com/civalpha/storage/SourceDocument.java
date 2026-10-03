package com.civalpha.storage;

import java.time.OffsetDateTime;

public record SourceDocument(
        long id,
        String sourceType,
        String publisher,
        String url,
        String accessionNo,
        String title,
        OffsetDateTime publishedAt,
        OffsetDateTime ingestedAt,
        int version,
        String contentSha256,
        String contentType,
        String storagePath,
        Long supersedesId,
        boolean demo) {}
