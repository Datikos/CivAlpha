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
    user_agent: str
    max_requests_per_second: float
    lookback_years: int

    @property
    def configured(self) -> bool:
        return bool(self.user_agent)


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
    review: str = "advisory"   # off | advisory | veto: how the model's review of ENTER decisions is used

    @property
    def enabled(self) -> bool:
        return self.provider.lower() == "anthropic" and bool(self.api_key)

    @property
    def review_mode(self) -> str:
        m = (self.review or "advisory").lower()
        return m if m in ("off", "advisory", "veto") else "advisory"


@dataclass(frozen=True)
class Jev:
    """TypeSafe AI's Jev decision model (ADR-0003): typed answers with probabilities, used to classify 8-K text."""
    api_key: str
    model: str = "jev-1.13.0"            # pinned: an alias like jev-latest moves when a release ships
    base_url: str = "https://api.typesafe.ai/v1/systemone"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


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
class Mcp:
    allowed_hosts: list[str]      # extra Host header values accepted by the /mcp endpoint (DNS-rebinding protection)
    allowed_origins: list[str]    # extra Origin header values


@dataclass(frozen=True)
class Settings:
    database_url: str
    documents_dir: str
    imports_dir: str
    admin_token: str
    sec: Sec
    fred: Fred
    events: Events
    llm: Llm
    prices: Prices
    schedule: Schedule
    mcp: Mcp
    jev: Jev = field(default_factory=lambda: Jev(api_key=""))
    auth_mode: str = "token"        # ADR-0005: "token" (one shared admin token, reads public) or "accounts" (sign-in)
    cookie_secure: bool = True      # session cookie Secure flag; false only for plain-http use away from localhost
    extra: dict = field(default_factory=dict)

    @property
    def admin_token_required(self) -> bool:
        return bool(self.admin_token)

    @property
    def accounts(self) -> bool:
        return self.auth_mode == "accounts"


def _auth_mode(v: str) -> str:
    """A misspelt mode must not fall back to the open one: refuse to start instead."""
    m = (v or "token").strip().lower()
    if m not in ("token", "accounts"):
        raise ValueError(f"CIVALPHA_AUTH must be 'token' or 'accounts', not {v!r}")
    return m


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load()


def load() -> Settings:
    start = _env("CIVALPHA_PRICE_HISTORY_START", "2019-01-02")
    return Settings(
        database_url=_env("DATABASE_URL", "postgresql+psycopg://civalpha:civalpha@localhost:5432/civalpha"),
        documents_dir=_env("CIVALPHA_DOCUMENTS_DIR", "../var/data/documents"),
        imports_dir=_env("CIVALPHA_IMPORTS_DIR", "../var/data/imports"),
        admin_token=_env("CIVALPHA_ADMIN_TOKEN"),
        sec=Sec(user_agent=_env("SEC_USER_AGENT"), max_requests_per_second=float(_env("SEC_MAX_RPS", "5")),
                lookback_years=int(_env("SEC_LOOKBACK_YEARS", "6"))),
        fred=Fred(api_key=_env("FRED_API_KEY"), base_url=_env("FRED_BASE_URL", "https://api.stlouisfed.org/fred"),
                  series=_list("FRED_SERIES", "FEDFUNDS,CPIAUCSL")),
        events=Events(federal_register_enabled=_bool("EVENTS_FEDERAL_REGISTER_ENABLED"), fed_rss_enabled=_bool("EVENTS_FED_RSS_ENABLED"),
                      news_feeds=_list("EVENTS_NEWS_FEEDS")),
        llm=Llm(provider=_env("CIVALPHA_LLM_PROVIDER", "none"), model=_env("CIVALPHA_LLM_MODEL", "claude-opus-5-5"),
                api_key=_env("ANTHROPIC_API_KEY"), review=_env("CIVALPHA_LLM_REVIEW", "advisory")),
        prices=Prices(provider=_env("CIVALPHA_PRICE_PROVIDER", "none"), tiingo_api_key=_env("TIINGO_API_KEY"),
                      history_start=date.fromisoformat(start) if start else date(2019, 1, 2)),
        schedule=Schedule(pipeline_cron=_env("CIVALPHA_PIPELINE_CRON", "-"), outcomes_cron=_env("CIVALPHA_OUTCOMES_CRON", "-"),
                          zone=_env("CIVALPHA_SCHEDULE_ZONE", "America/New_York")),
        mcp=Mcp(allowed_hosts=_list("CIVALPHA_MCP_ALLOWED_HOSTS"), allowed_origins=_list("CIVALPHA_MCP_ALLOWED_ORIGINS")),
        auth_mode=_auth_mode(_env("CIVALPHA_AUTH", "token")),
        cookie_secure=_bool("CIVALPHA_COOKIE_SECURE", True),
        jev=Jev(api_key=_env("TYPESAFE_API_KEY"), model=_env("CIVALPHA_JEV_MODEL", "jev-1.13.0"),
                base_url=_env("CIVALPHA_JEV_URL", "https://api.typesafe.ai/v1/systemone")),
    )
