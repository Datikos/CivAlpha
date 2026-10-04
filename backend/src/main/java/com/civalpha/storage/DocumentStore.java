package com.civalpha.storage;

import com.civalpha.config.AppProperties;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.Optional;

/**
 * Stores original source documents in the persistent documents volume (content-addressed by SHA-256)
 * and records their provenance in source_document. Re-ingesting the same locator with different bytes
 * creates a new version linked to the previous one; identical bytes return the existing record.
 */
@Service
public class DocumentStore {

    private final JdbcClient jdbc;
    private final Path root;

    public DocumentStore(JdbcClient jdbc, AppProperties props) {
        this.jdbc = jdbc;
        this.root = Path.of(props.storage().documentsDir());
    }

    public record NewDocument(String sourceType, String publisher, String url, String accessionNo, String title,
                              OffsetDateTime publishedAt, String contentType, byte[] content, boolean demo) {}

    @Transactional
    public SourceDocument store(NewDocument d) {
        if (d.url() == null && d.accessionNo() == null) {
            throw new IllegalArgumentException("a source document needs a URL or an accession number");
        }
        String sha = d.content() == null ? null : sha256(d.content());
        Optional<SourceDocument> latest = latestByLocator(d.url(), d.accessionNo());
        if (latest.isPresent() && sha != null && sha.equals(latest.get().contentSha256())) {
            return latest.get();
        }
        if (latest.isPresent() && sha == null) {
            return latest.get();
        }
        String rel = null;
        if (d.content() != null) {
            rel = sha.substring(0, 2) + "/" + sha + extension(d.contentType());
            write(root.resolve(rel), d.content());
        }
        int version = latest.map(s -> s.version() + 1).orElse(1);
        Long supersedes = latest.map(SourceDocument::id).orElse(null);
        long id = jdbc.sql("""
                INSERT INTO source_document (source_type, publisher, url, accession_no, title, published_at, version,
                                             content_sha256, content_type, storage_path, supersedes_id, is_demo)
                VALUES (:t, :p, :u, :a, :title, :pub, :v, :sha, :ct, :path, :sup, :demo) RETURNING id""")
                .param("t", d.sourceType()).param("p", d.publisher()).param("u", d.url()).param("a", d.accessionNo())
                .param("title", d.title()).param("pub", d.publishedAt()).param("v", version).param("sha", sha)
                .param("ct", d.contentType()).param("path", rel).param("sup", supersedes).param("demo", d.demo())
                .query(Long.class).single();
        return get(id).orElseThrow();
    }

    public Optional<SourceDocument> get(long id) {
        return jdbc.sql("SELECT * FROM source_document WHERE id = :id").param("id", id).query(this::map).optional();
    }

    public Optional<SourceDocument> latestByLocator(String url, String accession) {
        if (url != null) {
            return jdbc.sql("SELECT * FROM source_document WHERE url = :u ORDER BY version DESC LIMIT 1")
                    .param("u", url).query(this::map).optional();
        }
        return jdbc.sql("SELECT * FROM source_document WHERE url IS NULL AND accession_no = :a ORDER BY version DESC LIMIT 1")
                .param("a", accession).query(this::map).optional();
    }

    public Optional<byte[]> content(SourceDocument d) {
        if (d.storagePath() == null) return Optional.empty();
        try {
            return Optional.of(Files.readAllBytes(root.resolve(d.storagePath())));
        } catch (IOException e) {
            return Optional.empty();
        }
    }

    private SourceDocument map(java.sql.ResultSet rs, int n) throws java.sql.SQLException {
        Long sup = rs.getObject("supersedes_id", Long.class);
        return new SourceDocument(rs.getLong("id"), rs.getString("source_type"), rs.getString("publisher"),
                rs.getString("url"), rs.getString("accession_no"), rs.getString("title"),
                rs.getObject("published_at", OffsetDateTime.class), rs.getObject("ingested_at", OffsetDateTime.class),
                rs.getInt("version"), rs.getString("content_sha256"), rs.getString("content_type"),
                rs.getString("storage_path"), sup, rs.getBoolean("is_demo"));
    }

    private static void write(Path p, byte[] content) {
        try {
            Files.createDirectories(p.getParent());
            if (!Files.exists(p)) Files.write(p, content);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private static String extension(String contentType) {
        if (contentType == null) return "";
        if (contentType.contains("json")) return ".json";
        if (contentType.contains("html")) return ".html";
        if (contentType.contains("xml")) return ".xml";
        if (contentType.contains("csv")) return ".csv";
        return "";
    }

    public static String sha256(byte[] b) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(b));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
