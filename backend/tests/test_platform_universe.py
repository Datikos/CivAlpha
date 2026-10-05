"""Universe and ticker history (ported from the Java TickerResolver/Universe tests)."""
from datetime import date

import pytest

from civalpha.platform.errors import BadRequest, NotFound
from civalpha.platform.tickers import Span, TickerResolver, pad_cik, resolve


def test_pad_cik_and_in_memory_resolution():
    assert pad_cik("320193") == "0000320193"
    assert pad_cik("CIK 0000320193") == "0000320193"
    spans = [Span(7, "FB", date(2012, 5, 18), date(2022, 6, 9)), Span(7, "META", date(2022, 6, 9), None)]
    assert resolve(spans[:1], date(2021, 1, 4)) == 7
    assert resolve(spans[:1], date(2022, 6, 9)) is None


def test_ticker_change_resolves_to_one_company(universe, tdb):
    t = TickerResolver(tdb)
    meta = t.company_by_cik("0001326801")
    assert t.company_at("FB", date(2021, 1, 4)) == meta
    assert t.company_at("META", date(2023, 1, 3)) == meta
    assert t.company_at("FB", date(2023, 1, 3)) is None
    assert t.company_ever("fb") == meta
    assert t.current_symbol(meta) == "META"


def test_universe_management_rules_hold(universe, tdb):
    n = len(universe.companies())
    cid = universe.add("ZZZT", "Test Co", "1234567", "Technology", None, "XLK", None)
    assert len(universe.companies()) == n + 1
    with pytest.raises(BadRequest, match="already belongs"):
        universe.add("ZZZU", "Dup", "1234567", "Technology", None, "XLK", None)
    with pytest.raises(BadRequest, match="memberSince"):
        universe.add("ZZZV", "Future", "7654321", "Technology", None, "XLK", date(2999, 1, 1))
    universe.remove(cid, date.today())
    assert not universe.is_active(cid)
    universe.restore(cid, None)
    assert universe.is_active(cid)
    universe.change_ticker(cid, "ZZZX", date(2024, 1, 2))
    assert TickerResolver(tdb).company_at("ZZZX", date(2024, 6, 1)) == cid
    universe.delete(cid)
    assert TickerResolver(tdb).company_by_cik("1234567") is None


def test_migrations_are_recorded_like_flyway_and_rerunning_is_a_no_op(pg):
    from civalpha.platform.migrate import checksum, migrate
    from conftest import MIGRATIONS

    rows = pg.all("SELECT version, script, checksum, success FROM flyway_schema_history ORDER BY installed_rank")
    files = sorted(MIGRATIONS.glob("V*__*.sql"))
    assert [r["script"] for r in rows] == [f.name for f in sorted(files, key=lambda f: int(f.name[1:].split("__")[0]))]
    assert all(r["success"] and r["checksum"] == checksum(next(f for f in files if f.name == r["script"]).read_text()) for r in rows)
    assert migrate(pg.engine, MIGRATIONS) == 0
    # Flyway's checksum of a known text (CRC32 over lines without line breaks)
    assert checksum("a\nb\r\nc\n") == checksum("abc") == checksum("﻿a\nb\nc")


def test_universe_can_be_managed_and_seeding_never_undoes_it(universe, tdb):
    t = TickerResolver(tdb)
    cid = universe.add("bdsx", "Biodesix, Inc.", "1439725", "Health Care", "DIAGNOSTICS", "xlv", None)
    assert t.company_at("BDSX", date.today()) == cid
    assert cid in [c["id"] for c in universe.companies()]
    assert "XLV" in universe.benchmark_symbols()
    with pytest.raises(BadRequest, match="already belongs to BDSX"):
        universe.add("BDSY", "x", "1439725", "Health Care", None, "XLV", None)
    with pytest.raises(BadRequest, match="already the current ticker"):
        universe.add("BDSX", "x", "999", "Health Care", None, "XLV", None)
    # remove: membership closes today, history kept
    universe.remove(cid, None)
    assert cid not in [c["id"] for c in universe.companies()]
    assert not universe.is_active(cid)
    universe.restore(cid, None)
    assert universe.is_active(cid)
    universe.edit(cid, industry="MEDICAL_DIAGNOSTICS")
    assert tdb.scalar("SELECT industry FROM company WHERE id = :id", id=cid) == "MEDICAL_DIAGNOSTICS"
    tomorrow = date.fromordinal(date.today().toordinal() + 1)
    universe.change_ticker(cid, "BDSZ", tomorrow)
    assert t.company_at("BDSX", date.today()) == cid and t.company_at("BDSZ", tomorrow) == cid
    # a company with data cannot be deleted; one without can
    tdb.execute("INSERT INTO price_bar (company_id, symbol, trade_date, close, provider) VALUES (:c, 'BDSX', '2024-01-02', 1, 'test')", c=cid)
    with pytest.raises(BadRequest, match="remove it from the universe instead"):
        universe.delete(cid)
    tdb.execute("DELETE FROM price_bar WHERE company_id = :c", c=cid)
    universe.delete(cid)
    assert tdb.scalar("SELECT count(*) FROM company WHERE id = :id", id=cid) == 0


def test_tags_are_cleaned_replaced_and_listed(universe, tdb):
    from civalpha.platform.universe import MAX_TAGS, normalize_tags

    # cleaning: trimmed, inner whitespace collapsed, blanks dropped, case-insensitive duplicates keep the first spelling
    assert normalize_tags([" AI ", "ai", "China  exposed", "", None, "AI"]) == ["AI", "China exposed"]
    assert normalize_tags("core, watch only,core") == ["core", "watch only"]        # a comma-separated string works too
    assert normalize_tags(None) == []
    with pytest.raises(BadRequest, match="letters, digits"):
        normalize_tags(["bad<tag>"])
    with pytest.raises(BadRequest, match="longer than"):
        normalize_tags(["x" * 41])
    with pytest.raises(BadRequest, match="at most"):
        normalize_tags([f"t{i}" for i in range(MAX_TAGS + 1)])

    meta = TickerResolver(tdb).company_by_cik("0001326801")
    assert universe.set_tags(meta, ["Mega cap", "ai", " AI "]) == ["Mega cap", "ai"]
    assert universe.tags_of(meta) == ["ai", "Mega cap"]                               # listed alphabetically, case-insensitive
    cid = universe.add("ZZZT", "Tagged Co", "1234567", "Technology", None, "XLK", None, tags=["AI", "watch only"])
    assert universe.tags_of(cid) == ["ai", "watch only"]                              # "AI" takes the spelling already in use
    assert universe.tags_by_company() == {meta: ["ai", "Mega cap"], cid: ["ai", "watch only"]}
    assert [(t["tag"], t["count"]) for t in universe.all_tags()] == [("ai", 2), ("Mega cap", 1), ("watch only", 1)]
    assert universe.set_tags(meta, []) == [] and universe.tags_of(meta) == []        # an empty list clears
    with pytest.raises(NotFound):
        universe.set_tags(999999, ["x"])
    universe.delete(cid)                                                              # tags go with the company
    assert tdb.scalar("SELECT count(*) FROM company_tag WHERE company_id = :c", c=cid) == 0
