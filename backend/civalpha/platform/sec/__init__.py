"""SEC EDGAR ingestion: clients (live HTTPS; a local-directory client for tests), parsers, passages, ticker lookup, filing ingestion."""
from .client import (COMPANY_FACTS, COMPANY_TICKERS, SUBMISSIONS, FixtureSecClient, LiveSecClient, RateLimiter, SecClient,
                     SecClientFactory, archive_url, company_facts_url, submissions_url)
from .ingestion import FilingIngestionService, Result
from .parsers import FactRow, FilePage, FilingMeta, Submissions
from .ticker_lookup import Match, SecTickerLookup

__all__ = ["SUBMISSIONS", "COMPANY_FACTS", "COMPANY_TICKERS", "SecClient", "LiveSecClient", "FixtureSecClient", "RateLimiter",
           "SecClientFactory", "archive_url", "company_facts_url", "submissions_url", "FilingIngestionService", "Result",
           "FactRow", "FilePage", "FilingMeta", "Submissions", "Match", "SecTickerLookup"]
