"""Original source documents: content-addressed files (SHA-256) plus provenance rows in source_document.

Re-ingesting the same locator (URL, else accession number) with different bytes creates version n+1 linked to
version n; identical bytes (or no bytes) return the existing record.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .settings import settings
from .sql import Db, db


@dataclass(frozen=True)
class SourceDocument:
    id: int
    source_type: str
    publisher: str | None
    url: str | None
    accession_no: str | None
    title: str | None
    published_at: datetime | None
    ingested_at: datetime | None
    version: int
    content_sha256: str | None
    content_type: str | None
    storage_path: str | None
    supersedes_id: int | None
    demo: bool


@dataclass(frozen=True)
class NewDocument:
    source_type: str
    publisher: str | None
    url: str | None
    accession_no: str | None
    title: str | None
    published_at: datetime | None
    content_type: str | None
    content: bytes | None
    demo: bool


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _extension(content_type: str | None) -> str:
    if not content_type:
        return ""
    for key, ext in (("json", ".json"), ("html", ".html"), ("xml", ".xml"), ("csv", ".csv")):
        if key in content_type:
            return ext
    return ""


def _map(r: dict | None) -> SourceDocument | None:
    if r is None:
        return None
    return SourceDocument(id=r["id"], source_type=r["source_type"], publisher=r["publisher"], url=r["url"],
                          accession_no=r["accession_no"], title=r["title"], published_at=r["published_at"],
                          ingested_at=r["ingested_at"], version=r["version"], content_sha256=r["content_sha256"],
                          content_type=r["content_type"], storage_path=r["storage_path"], supersedes_id=r["supersedes_id"],
                          demo=bool(r["is_demo"]))


class DocumentStore:
    def __init__(self, database: Db | None = None, root: str | Path | None = None):
        self.db = database or db()
        self.root = Path(root or settings().documents_dir)

    def store(self, d: NewDocument) -> SourceDocument:
        if d.url is None and d.accession_no is None:
            raise ValueError("a source document needs a URL or an accession number")
        with self.db.transaction():
            sha = sha256(d.content) if d.content is not None else None
            latest = self.latest_by_locator(d.url, d.accession_no)
            if latest is not None and (sha is None or sha == latest.content_sha256):
                return latest
            rel = None
            if d.content is not None:
                rel = f"{sha[:2]}/{sha}{_extension(d.content_type)}"
                p = self.root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.write_bytes(d.content)
            new_id = self.db.scalar("""
                INSERT INTO source_document (source_type, publisher, url, accession_no, title, published_at, version,
                                             content_sha256, content_type, storage_path, supersedes_id, is_demo)
                VALUES (:t, :p, :u, :a, :title, :pub, :v, :sha, :ct, :path, :sup, :demo) RETURNING id""",
                t=d.source_type, p=d.publisher, u=d.url, a=d.accession_no, title=d.title, pub=d.published_at,
                v=(latest.version + 1) if latest else 1, sha=sha, ct=d.content_type, path=rel,
                sup=latest.id if latest else None, demo=d.demo)
            return self.get(new_id)

    def get(self, doc_id: int) -> SourceDocument | None:
        return _map(self.db.one("SELECT * FROM source_document WHERE id = :id", id=doc_id))

    def latest_by_locator(self, url: str | None, accession: str | None) -> SourceDocument | None:
        if url is not None:
            return _map(self.db.one("SELECT * FROM source_document WHERE url = :u ORDER BY version DESC LIMIT 1", u=url))
        return _map(self.db.one("SELECT * FROM source_document WHERE url IS NULL AND accession_no = :a ORDER BY version DESC LIMIT 1",
                                a=accession))

    def content(self, d: SourceDocument) -> bytes | None:
        if d.storage_path is None:
            return None
        try:
            return (self.root / d.storage_path).read_bytes()
        except OSError:
            return None
