package com.civalpha.api;

import com.civalpha.storage.DocumentStore;
import com.civalpha.storage.SourceDocument;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class DocumentController {

    private final DocumentStore docs;

    public DocumentController(DocumentStore docs) {
        this.docs = docs;
    }

    /** Serves the stored original. HTML is sent as text/plain-safe sandboxed content to avoid script execution. */
    @GetMapping("/api/documents/{id}")
    public ResponseEntity<byte[]> get(@PathVariable long id) {
        SourceDocument d = docs.get(id).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
        byte[] body = docs.content(d).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "document stored as metadata only"));
        MediaType type;
        try {
            type = d.contentType() == null ? MediaType.APPLICATION_OCTET_STREAM : MediaType.parseMediaType(d.contentType());
        } catch (RuntimeException e) {
            type = MediaType.APPLICATION_OCTET_STREAM;
        }
        return ResponseEntity.ok()
                .contentType(type)
                .header("Content-Security-Policy", "sandbox; default-src 'none'; img-src * data:; style-src 'unsafe-inline' *")
                .header("X-Content-Type-Options", "nosniff")
                .header("X-Source-Url", d.url() == null ? "" : d.url())
                .header("X-Source-Version", String.valueOf(d.version()))
                .header(HttpHeaders.CACHE_CONTROL, "private, max-age=3600")
                .body(body);
    }
}
