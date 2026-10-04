"""Schema migrations: applies db/migration/V<n>__<name>.sql in version order. Run: python -m civalpha.platform.migrate

Compatible with Flyway, which ran these migrations before the move to Python: it uses the same history table
(flyway_schema_history) and the same checksum (CRC32 over each line without its line break, as a signed 32-bit int),
so databases created earlier continue seamlessly. An applied script whose checksum changed is an error: migrations
are append-only; add a new V<n+1> file instead of editing an old one.
"""
from __future__ import annotations

import getpass
import logging
import os
import re
import sys
import time
import zlib
from pathlib import Path

from sqlalchemy import Engine, text

from .sql import engine

log = logging.getLogger("civalpha.migrate")

FILE = re.compile(r"^V(\d+(?:[._]\d+)*)__(.+)\.sql$")
HISTORY = """
CREATE TABLE IF NOT EXISTS flyway_schema_history (
    installed_rank INT NOT NULL PRIMARY KEY,
    version        VARCHAR(50),
    description    VARCHAR(200) NOT NULL,
    type           VARCHAR(20) NOT NULL,
    script         VARCHAR(1000) NOT NULL,
    checksum       INT,
    installed_by   VARCHAR(100) NOT NULL,
    installed_on   TIMESTAMP NOT NULL DEFAULT now(),
    execution_time INT NOT NULL,
    success        BOOLEAN NOT NULL
)"""
LOCK_ID = 7_351_204_118  # advisory lock: one migrator at a time


def default_dir() -> Path:
    env = os.environ.get("CIVALPHA_MIGRATIONS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for p in (here.parents[3] / "db" / "migration", Path("/app/db/migration")):
        if p.is_dir():
            return p
    return Path("/app/db/migration")


def checksum(content: str) -> int:
    """Flyway's checksum: CRC32 over each line (BOM and line breaks removed), as a signed int."""
    if content.startswith("﻿"):
        content = content[1:]
    lines = re.split(r"\r\n|\r|\n", content)
    if lines and lines[-1] == "":
        lines.pop()          # BufferedReader.readLine() returns no empty last line
    crc = 0
    for line in lines:
        crc = zlib.crc32(line.encode("utf-8"), crc)
    return crc - (1 << 32) if crc >= (1 << 31) else crc


def scripts(directory: Path) -> list[tuple[tuple[int, ...], str, str, Path]]:
    out = []
    for p in directory.glob("V*__*.sql"):
        m = FILE.match(p.name)
        if m:
            version = tuple(int(x) for x in re.split(r"[._]", m.group(1)))
            out.append((version, ".".join(map(str, version)), m.group(2).replace("_", " "), p))
    return sorted(out)


def migrate(eng: Engine | None = None, directory: Path | None = None) -> int:
    """Applies pending migrations; returns how many were applied."""
    eng = eng or engine()
    directory = directory or default_dir()
    pending = scripts(directory)
    with eng.begin() as c:
        c.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_ID})
    try:
        with eng.begin() as c:
            c.execute(text(HISTORY))
            applied = {r.version: r for r in c.execute(text(
                "SELECT installed_rank, version, script, checksum, success FROM flyway_schema_history WHERE version IS NOT NULL"))}
            rank = c.execute(text("SELECT coalesce(max(installed_rank), 0) FROM flyway_schema_history")).scalar()
        failed = [v for v, r in applied.items() if not r.success]
        if failed:
            raise RuntimeError(f"migration(s) {failed} failed earlier; repair the database before migrating again")
        count = 0
        for version, vtext, description, path in pending:
            content = path.read_text(encoding="utf-8")
            cs = checksum(content)
            if vtext in applied:
                if applied[vtext].checksum != cs:
                    raise RuntimeError(f"{path.name} was changed after it was applied (checksum {applied[vtext].checksum} -> {cs}); "
                                       "never edit an applied migration, add a new one")
                continue
            start = time.monotonic()
            raw = eng.raw_connection()      # plain driver cursor: scripts may contain literal '%'
            try:
                cur = raw.cursor()
                cur.execute(content)
                rank += 1
                cur.execute("""INSERT INTO flyway_schema_history (installed_rank, version, description, type, script, checksum,
                                                                    installed_by, execution_time, success)
                               VALUES (%s, %s, %s, 'SQL', %s, %s, %s, %s, true)""",
                            (rank, vtext, description, path.name, cs, _user(), int((time.monotonic() - start) * 1000)))
                raw.commit()
            except Exception:
                raw.rollback()
                raise
            finally:
                raw.close()
            log.info("applied %s", path.name)
            count += 1
        return count
    finally:
        with eng.begin() as c:
            c.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_ID})


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "civalpha"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    n = migrate()
    log.info("database schema is up to date (%d migration(s) applied)", n)
    sys.exit(0)
