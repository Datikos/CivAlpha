import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIGRATIONS = ROOT.parent / "db" / "migration"
TEST_UNIVERSE = Path(__file__).resolve().parent / "resources" / "test-universe.yml"


@pytest.fixture(scope="session")
def pg():
    """A PostgreSQL 16 database with every migration in db/migration applied (Testcontainers; skipped without Docker)."""
    # CI sets CIVALPHA_REQUIRE_DB=1 so the database tests can never be skipped silently
    skip = pytest.fail if os.environ.get("CIVALPHA_REQUIRE_DB") else pytest.skip
    if shutil.which("docker") is None:
        skip("Docker is not available for database tests")
    from sqlalchemy import create_engine
    from testcontainers.postgres import PostgresContainer

    from civalpha.platform.sql import Db

    try:
        container = PostgresContainer("postgres:16-alpine", driver="psycopg")
        container.start()
    except Exception as e:  # noqa: BLE001
        skip(f"cannot start PostgreSQL container: {e}")
    try:
        from civalpha.platform.migrate import migrate

        engine = create_engine(container.get_connection_url())
        migrate(engine, MIGRATIONS)
        yield Db(engine)
    finally:
        container.stop()


@pytest.fixture
def tdb(pg):
    """The test database inside a transaction that is rolled back after the test."""
    with pg.rollback_scope() as d:
        yield d


@pytest.fixture
def universe(tdb):
    """UniverseService on the test universe, already loaded."""
    from civalpha.platform.universe import UniverseService

    u = UniverseService(tdb, str(TEST_UNIVERSE))
    u.load(False)
    return u


@pytest.fixture(scope="session")
def api_db(pg, tmp_path_factory):
    """A separate, committed database for API tests (requests run in another thread, outside the test transaction)."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from civalpha.platform.migrate import migrate

    url = pg.engine.url
    with pg.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.exec_driver_sql("CREATE DATABASE api_test")
    api_url = make_url(url).set(database="api_test")
    migrate(create_engine(api_url), MIGRATIONS)
    return api_url.render_as_string(hide_password=False), tmp_path_factory.mktemp("api-data")


@pytest.fixture
def api(api_db, monkeypatch):
    """FastAPI TestClient bound to the API test database, with the test universe loaded."""
    from fastapi.testclient import TestClient

    from civalpha.platform import llm, settings, sql

    url, data = api_db
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CIVALPHA_UNIVERSE_FILE", str(TEST_UNIVERSE))
    monkeypatch.setenv("CIVALPHA_DOCUMENTS_DIR", str(data / "documents"))
    monkeypatch.setenv("CIVALPHA_ADMIN_TOKEN", "")
    for f in (settings.settings, sql.engine, sql.db, llm.provider):
        f.cache_clear()
    from civalpha.platform.app import app
    from civalpha.platform.universe import UniverseService

    UniverseService().load(False)
    yield TestClient(app)
    for f in (settings.settings, sql.engine, sql.db, llm.provider):
        f.cache_clear()
