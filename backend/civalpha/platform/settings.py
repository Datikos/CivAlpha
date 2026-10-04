"""Configuration from environment variables (same names as before the move from the Java backend)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]     # `docker run --env-file` keeps quotes that docker compose strips
    return v


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    return [s.strip() for s in _env(name, default).split(",") if s.strip()]


@dataclass(frozen=True)
class Sec:
    mode: str
    user_agent: str
    max_requests_per_second: float
    fixture_dir: str
    lookback_years: int

    @property
    def live(self) -> bool:
        return self.mode.lower() == "live"


@dataclass(frozen=True)
class Fred:
    api_key: str
    base_url: str
    series: list[str]

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class Events:
    federal_register_enabled: bool
    fed_rss_enabled: bool
    news_feeds: list[str]


@dataclass(frozen=True)
class Llm:
    provider: str
    model: str
    api_key: str

    @property
    def enabled(self) -> bool:
        return self.provider.lower() == "anthropic" and bool(self.api_key)


@dataclass(frozen=True)
class Prices:
    provider: str
    tiingo_api_key: str
    history_start: date

    @property
    def enabled(self) -> bool:
        return bool(self.provider) and self.provider.lower() != "none"


@dataclass(frozen=True)
class Schedule:
    pipeline_cron: str      # cron expression; "-" or empty disables. 6 fields = seconds first (as before), 5 = standard
    outcomes_cron: str
    zone: str


@dataclass(frozen=True)
class Settings:
    database_url: str
    universe_file: str
    documents_dir: str
    imports_dir: str
    demo_dir: str
    replay_months: int
    admin_token: str
    sec: Sec
    fred: Fred
    events: Events
    llm: Llm
    prices: Prices
    schedule: Schedule
    extra: dict = field(default_factory=dict)

    @property
    def admin_token_required(self) -> bool:
        return bool(self.admin_token)


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load()


def load() -> Settings:
    start = _env("CIVALPHA_PRICE_HISTORY_START", "2019-01-02")
    return Settings(
        database_url=_env("DATABASE_URL", "postgresql+psycopg://civalpha:civalpha@localhost:5432/civalpha"),
        universe_file=_env("CIVALPHA_UNIVERSE_FILE", _env("UNIVERSE_FILE", "../config/universe.yml")),
        documents_dir=_env("CIVALPHA_DOCUMENTS_DIR", "../var/data/documents"),
        imports_dir=_env("CIVALPHA_IMPORTS_DIR", "../var/data/imports"),
        demo_dir=_env("CIVALPHA_DEMO_DIR", "../var/data/demo"),
        replay_months=int(_env("CIVALPHA_DEMO_REPLAY_MONTHS", "12")),
        admin_token=_env("CIVALPHA_ADMIN_TOKEN"),
        sec=Sec(mode=_env("CIVALPHA_SEC_MODE", "fixture"), user_agent=_env("SEC_USER_AGENT"),
                max_requests_per_second=float(_env("SEC_MAX_RPS", "5")),
                fixture_dir=_env("CIVALPHA_SEC_FIXTURE_DIR", "../var/data/demo/sec"),
                lookback_years=int(_env("SEC_LOOKBACK_YEARS", "6"))),
        fred=Fred(api_key=_env("FRED_API_KEY"), base_url=_env("FRED_BASE_URL", "https://api.stlouisfed.org/fred"),
                  series=_list("FRED_SERIES", "FEDFUNDS,CPIAUCSL")),
        events=Events(federal_register_enabled=_bool("EVENTS_FEDERAL_REGISTER_ENABLED"), fed_rss_enabled=_bool("EVENTS_FED_RSS_ENABLED"),
                      news_feeds=_list("EVENTS_NEWS_FEEDS")),
        llm=Llm(provider=_env("CIVALPHA_LLM_PROVIDER", "none"), model=_env("CIVALPHA_LLM_MODEL", "claude-opus-5-5"),
                api_key=_env("ANTHROPIC_API_KEY")),
        prices=Prices(provider=_env("CIVALPHA_PRICE_PROVIDER", "none"), tiingo_api_key=_env("TIINGO_API_KEY"),
                      history_start=date.fromisoformat(start) if start else date(2019, 1, 2)),
        schedule=Schedule(pipeline_cron=_env("CIVALPHA_PIPELINE_CRON", "-"), outcomes_cron=_env("CIVALPHA_OUTCOMES_CRON", "-"),
                          zone=_env("CIVALPHA_SCHEDULE_ZONE", "America/New_York")),
    )
