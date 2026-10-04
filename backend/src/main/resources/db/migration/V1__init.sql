-- CivAlpha schema v1.
-- Conventions:
--   * every row derived from an external source carries source_document_id (or accession_no),
--     published_at (when the world could know it), ingested_at (when we learned it) and a version.
--   * is_demo marks seeded synthetic demonstration data; the UI must label it.
--   * point-in-time ("PIT") reads always filter on the availability column documented per table.

-- ---------------------------------------------------------------- sources
CREATE TABLE source_document (
    id               BIGSERIAL PRIMARY KEY,
    source_type      TEXT        NOT NULL,  -- SEC_FILING | SEC_API | OFFICIAL_EVENT | NEWS | PRICE_FILE | MACRO | MANUAL
    publisher        TEXT        NOT NULL,  -- e.g. 'SEC EDGAR', 'Federal Reserve Board', 'Federal Register'
    url              TEXT,
    accession_no     TEXT,
    title            TEXT,
    published_at     TIMESTAMPTZ,           -- publication time stated by the source
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    version          INT         NOT NULL DEFAULT 1,
    content_sha256   TEXT,
    content_type     TEXT,
    storage_path     TEXT,                  -- relative path inside the documents volume
    supersedes_id    BIGINT REFERENCES source_document(id),
    is_demo          BOOLEAN     NOT NULL DEFAULT FALSE,
    CONSTRAINT source_document_has_locator CHECK (url IS NOT NULL OR accession_no IS NOT NULL)
);
CREATE INDEX source_document_url_idx ON source_document(url);
CREATE INDEX source_document_accession_idx ON source_document(accession_no);

-- ---------------------------------------------------------------- companies / identifiers
CREATE TABLE company (
    id                 BIGSERIAL PRIMARY KEY,
    name               TEXT    NOT NULL,
    sector             TEXT    NOT NULL,
    industry           TEXT,                -- product key used to match PRODUCT event targets
    benchmark_symbol   TEXT    NOT NULL,   -- sector benchmark (e.g. XLK) used by the forecast target
    exchange           TEXT    NOT NULL DEFAULT 'NASDAQ',
    is_demo            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ticker symbols change over time (e.g. FB -> META on 2022-06-09). valid_to is exclusive.
CREATE TABLE ticker_history (
    id          BIGSERIAL PRIMARY KEY,
    company_id  BIGINT NOT NULL REFERENCES company(id),
    symbol      TEXT   NOT NULL,
    valid_from  DATE   NOT NULL,
    valid_to    DATE,
    source      TEXT   NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ticker_history_range CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX ticker_history_symbol_idx ON ticker_history(symbol, valid_from);
CREATE UNIQUE INDEX ticker_history_company_from_idx ON ticker_history(company_id, valid_from);

CREATE TABLE cik_mapping (
    id          BIGSERIAL PRIMARY KEY,
    company_id  BIGINT NOT NULL REFERENCES company(id),
    cik         TEXT   NOT NULL,          -- zero-padded 10 digits
    valid_from  DATE   NOT NULL,
    valid_to    DATE,
    source      TEXT   NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX cik_mapping_cik_idx ON cik_mapping(cik);

-- Universe membership is point-in-time too: a company may join or leave the configured universe.
CREATE TABLE universe_membership (
    id          BIGSERIAL PRIMARY KEY,
    universe    TEXT   NOT NULL,
    company_id  BIGINT NOT NULL REFERENCES company(id),
    valid_from  DATE   NOT NULL,
    valid_to    DATE,
    UNIQUE (universe, company_id, valid_from)
);

-- ---------------------------------------------------------------- SEC filings and facts
CREATE TABLE filing (
    id                 BIGSERIAL PRIMARY KEY,
    company_id         BIGINT NOT NULL REFERENCES company(id),
    cik                TEXT   NOT NULL,
    accession_no       TEXT   NOT NULL UNIQUE,
    form_type          TEXT   NOT NULL,          -- 10-K, 10-Q, 8-K, 10-K/A ...
    period_of_report   DATE,
    filed_date         DATE   NOT NULL,
    accepted_at        TIMESTAMPTZ NOT NULL,     -- EDGAR acceptance time = PIT availability
    primary_document   TEXT,
    items              TEXT,                     -- 8-K item list e.g. '2.02,9.01'
    amends_accession   TEXT,                     -- for /A forms when known
    source_document_id BIGINT REFERENCES source_document(id),
    ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo            BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX filing_company_idx ON filing(company_id, accepted_at);

-- One row per reported value per filing. The same (concept, period, dimensions) can be reported
-- by several filings (comparatives, amendments). A PIT read picks the latest accepted_at <= as_of.
CREATE TABLE xbrl_fact (
    id             BIGSERIAL PRIMARY KEY,
    company_id     BIGINT  NOT NULL REFERENCES company(id),
    filing_id      BIGINT  REFERENCES filing(id),
    accession_no   TEXT    NOT NULL,
    taxonomy       TEXT    NOT NULL,         -- us-gaap, dei, srt ...
    concept        TEXT    NOT NULL,
    unit           TEXT    NOT NULL,
    value          NUMERIC NOT NULL,
    period_start   DATE,
    period_end     DATE    NOT NULL,
    fiscal_year    INT,
    fiscal_period  TEXT,
    form_type      TEXT    NOT NULL,
    filed_date     DATE    NOT NULL,
    accepted_at    TIMESTAMPTZ NOT NULL,
    dimensions     JSONB   NOT NULL DEFAULT '{}'::jsonb,  -- {"srt:StatementGeographicalAxis":"country:CN"}
    dims_key       TEXT    NOT NULL DEFAULT '',          -- canonical text of dimensions for uniqueness
    source_url     TEXT,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo        BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE NULLS NOT DISTINCT (accession_no, taxonomy, concept, unit, period_start, period_end, dims_key)
);
CREATE INDEX xbrl_fact_pit_idx ON xbrl_fact(company_id, concept, period_end, accepted_at);

CREATE TABLE filing_passage (
    id                BIGSERIAL PRIMARY KEY,
    filing_id         BIGINT NOT NULL REFERENCES filing(id),
    section           TEXT,                  -- e.g. 'Item 1A. Risk Factors'
    topic             TEXT NOT NULL,         -- GEOGRAPHIC_REVENUE | SEGMENT | COSTS | DEBT | RISK | TRADE | RATES
    text              TEXT NOT NULL,
    char_start        INT,
    char_end          INT,
    extraction_method TEXT NOT NULL,         -- RULE_KEYWORD | XBRL | LLM
    extractor_version TEXT NOT NULL,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo           BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX filing_passage_filing_idx ON filing_passage(filing_id);

-- ---------------------------------------------------------------- market data
CREATE TABLE price_bar (
    company_id      BIGINT REFERENCES company(id),      -- null for benchmarks
    symbol          TEXT    NOT NULL,                   -- symbol as reported by the provider on that date
    trade_date      DATE    NOT NULL,
    open            NUMERIC,
    high            NUMERIC,
    low             NUMERIC,
    close           NUMERIC NOT NULL,                   -- raw (unadjusted) close
    volume          BIGINT,
    provider        TEXT    NOT NULL,
    source_document_id BIGINT REFERENCES source_document(id),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo         BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX price_bar_company_idx ON price_bar(company_id, trade_date);

CREATE TABLE corporate_action (
    id              BIGSERIAL PRIMARY KEY,
    company_id      BIGINT REFERENCES company(id),
    symbol          TEXT NOT NULL,
    ex_date         DATE NOT NULL,
    action_type     TEXT NOT NULL,          -- SPLIT | CASH_DIVIDEND
    value           NUMERIC NOT NULL,       -- split ratio (new/old, e.g. 4 for 4:1) or dividend per share
    announced_at    TIMESTAMPTZ,
    provider        TEXT NOT NULL,
    source_document_id BIGINT REFERENCES source_document(id),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo         BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (symbol, ex_date, action_type)
);

-- ---------------------------------------------------------------- macro (vintage-aware, ALFRED style)
CREATE TABLE macro_series (
    series_id   TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    units       TEXT,
    frequency   TEXT,
    source      TEXT NOT NULL
);
CREATE TABLE macro_observation (
    series_id      TEXT NOT NULL REFERENCES macro_series(series_id),
    obs_date       DATE NOT NULL,
    value          NUMERIC,
    realtime_start DATE NOT NULL,     -- first date this value was published
    realtime_end   DATE,              -- last date it was current (null = still current)
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo        BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (series_id, obs_date, realtime_start)
);

-- ---------------------------------------------------------------- political / policy events
CREATE TABLE policy_actor (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,     -- 'Federal Open Market Committee', 'USTR'
    actor_type    TEXT NOT NULL,            -- INSTITUTION | OFFICIAL
    authority     TEXT,                     -- documented legal authority, e.g. 'Section 301, Trade Act of 1974'
    affiliation   TEXT,
    profile_note  TEXT                      -- documented facts only; never inferred traits or motives
);

-- Documented statements, votes and prior actions that make up a public decision profile.
CREATE TABLE actor_record (
    id                 BIGSERIAL PRIMARY KEY,
    actor_id           BIGINT NOT NULL REFERENCES policy_actor(id),
    record_type        TEXT NOT NULL,       -- STATEMENT | VOTE | ACTION
    occurred_at        TIMESTAMPTZ NOT NULL,
    summary            TEXT NOT NULL,
    source_document_id BIGINT NOT NULL REFERENCES source_document(id),
    is_demo            BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE policy_event (
    id               BIGSERIAL PRIMARY KEY,
    category         TEXT NOT NULL,          -- MONETARY_POLICY | TRADE_TARIFF
    event_type       TEXT NOT NULL,          -- RATE_DECISION | TARIFF_IMPOSED | TARIFF_REMOVED | TARIFF_PROPOSED ...
    title            TEXT NOT NULL,
    summary          TEXT,
    actor_id         BIGINT REFERENCES policy_actor(id),
    jurisdiction     TEXT NOT NULL DEFAULT 'US',
    event_date       DATE NOT NULL,          -- effective/decision date
    published_at     TIMESTAMPTZ NOT NULL,   -- earliest publication of the official document (PIT availability)
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    evidence_status  TEXT NOT NULL,          -- OFFICIAL | NEWS_ONLY
    attributes       JSONB NOT NULL DEFAULT '{}'::jsonb, -- {"rate_change_bps":25,"tariff_rate_pct":25,"severity":0.8}
    dedup_key        TEXT NOT NULL,
    version          INT  NOT NULL DEFAULT 1,
    is_demo          BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX policy_event_published_idx ON policy_event(published_at);
CREATE INDEX policy_event_dedup_idx ON policy_event(category, event_date);

CREATE TABLE event_source (
    event_id           BIGINT NOT NULL REFERENCES policy_event(id),
    source_document_id BIGINT NOT NULL REFERENCES source_document(id),
    role               TEXT NOT NULL,       -- OFFICIAL_PRIMARY | NEWS_DISCOVERY | OFFICIAL_SUPPORTING
    linked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, source_document_id)
);

-- What an event touches: a country, sector, product or cost input.
CREATE TABLE event_target (
    id           BIGSERIAL PRIMARY KEY,
    event_id     BIGINT NOT NULL REFERENCES policy_event(id),
    target_type  TEXT NOT NULL,             -- COUNTRY | SECTOR | PRODUCT | COST_INPUT | INTEREST_RATE
    target_code  TEXT NOT NULL,             -- CN, SEMICONDUCTORS, STEEL, US_POLICY_RATE ...
    magnitude    NUMERIC,                   -- e.g. tariff rate pct or bps
    note         TEXT
);
CREATE INDEX event_target_event_idx ON event_target(event_id);

-- ---------------------------------------------------------------- company exposure
-- event target -> company link, supported by a filing passage or XBRL fact.
CREATE TABLE company_exposure (
    id                BIGSERIAL PRIMARY KEY,
    company_id        BIGINT NOT NULL REFERENCES company(id),
    target_type       TEXT NOT NULL,
    target_code       TEXT NOT NULL,
    exposure_channel  TEXT NOT NULL,        -- REVENUE | SUPPLY_CHAIN | COST_INPUT | FINANCING | SECTOR
    share             NUMERIC,              -- fraction 0..1 where meaningful (e.g. revenue share)
    basis             TEXT NOT NULL,        -- DIRECTLY_REPORTED | ESTIMATED
    confidence        TEXT NOT NULL,        -- HIGH | MEDIUM | LOW
    method            TEXT NOT NULL,        -- XBRL_DIMENSION | RULE_KEYWORD | LLM | SECTOR_MAP
    filing_id         BIGINT REFERENCES filing(id),
    passage_id        BIGINT REFERENCES filing_passage(id),
    xbrl_fact_id      BIGINT REFERENCES xbrl_fact(id),
    available_at      TIMESTAMPTZ NOT NULL, -- = accepted_at of the supporting filing
    period_end        DATE,
    rationale         TEXT,
    version           INT NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo           BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX company_exposure_pit_idx ON company_exposure(company_id, target_type, target_code, available_at);

-- ---------------------------------------------------------------- models, forecasts, evaluation
CREATE TABLE model_version (
    id               BIGSERIAL PRIMARY KEY,
    model_kind       TEXT NOT NULL,          -- BASELINE | AUGMENTED
    algorithm        TEXT NOT NULL,          -- logistic_regression_l2
    feature_names    JSONB NOT NULL,
    trained_through  DATE NOT NULL,          -- last as_of date whose label was fully observed before training cutoff
    training_cutoff  TIMESTAMPTZ NOT NULL,
    n_samples        INT NOT NULL,
    params           JSONB NOT NULL,         -- coefficients, intercept, scaler, clip bounds
    code_version     TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_demo          BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE forecast (
    id                    BIGSERIAL PRIMARY KEY,
    company_id            BIGINT NOT NULL REFERENCES company(id),
    symbol                TEXT NOT NULL,
    benchmark_symbol      TEXT NOT NULL,
    model_kind            TEXT NOT NULL,
    model_version_id      BIGINT NOT NULL REFERENCES model_version(id),
    target                TEXT NOT NULL,      -- textual definition of the target
    horizon_trading_days  INT  NOT NULL,
    as_of                 TIMESTAMPTZ NOT NULL,  -- data cutoff; nothing published after this was used
    as_of_date            DATE NOT NULL,         -- trading date the window starts from (close)
    issued_at             TIMESTAMPTZ NOT NULL DEFAULT now(), -- publication time (wall clock)
    issue_mode            TEXT NOT NULL,      -- LIVE | REPLAY (replay = issued later than its cutoff)
    probability           NUMERIC NOT NULL CHECK (probability >= 0 AND probability <= 1),
    prob_low              NUMERIC NOT NULL,
    prob_high             NUMERIC NOT NULL,
    uncertainty_note      TEXT,
    features              JSONB NOT NULL,     -- feature values used
    explanation           JSONB NOT NULL,     -- factor contributions + provenance (event ids, filings, passages)
    sources               JSONB NOT NULL,     -- list of {label,url|accession,published_at}
    series_key            TEXT NOT NULL,      -- company|model_kind|as_of_date
    version               INT  NOT NULL,
    supersedes_id         BIGINT REFERENCES forecast(id),
    reason                TEXT NOT NULL,      -- why issued / re-issued
    content_sha256        TEXT NOT NULL,
    is_demo               BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (series_key, version)
);
CREATE INDEX forecast_company_idx ON forecast(company_id, as_of_date);

-- Forecasts are immutable once issued: corrections create a new version.
CREATE FUNCTION forbid_forecast_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'forecast % is immutable; issue a new version instead', OLD.id;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER forecast_immutable BEFORE UPDATE OR DELETE ON forecast
    FOR EACH ROW EXECUTE FUNCTION forbid_forecast_mutation();

-- Outcomes are recorded separately so the forecast row never changes.
CREATE TABLE forecast_outcome (
    forecast_id       BIGINT PRIMARY KEY REFERENCES forecast(id),
    window_end_date   DATE NOT NULL,
    stock_return      NUMERIC NOT NULL,
    benchmark_return  NUMERIC NOT NULL,
    excess_return     NUMERIC NOT NULL,
    outcome           BOOLEAN NOT NULL,
    brier             NUMERIC NOT NULL,
    resolved_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_evaluation (
    id             BIGSERIAL PRIMARY KEY,
    run_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_cutoff    DATE NOT NULL,
    config         JSONB NOT NULL,
    metrics        JSONB NOT NULL,      -- per model: brier, log_loss, auc, accuracy, base_rate, n
    comparison     JSONB NOT NULL,      -- augmented vs baseline with bootstrap CI
    calibration    JSONB NOT NULL,      -- per model reliability bins
    trading        JSONB NOT NULL,      -- cost-adjusted long/short simulation
    folds          JSONB NOT NULL,
    verdict        TEXT NOT NULL,
    is_demo        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE backtest_prediction (
    evaluation_id  BIGINT NOT NULL REFERENCES model_evaluation(id) ON DELETE CASCADE,
    company_id     BIGINT NOT NULL REFERENCES company(id),
    as_of_date     DATE NOT NULL,
    model_kind     TEXT NOT NULL,
    fold           INT  NOT NULL,
    probability    NUMERIC NOT NULL,
    outcome        BOOLEAN NOT NULL,
    excess_return  NUMERIC NOT NULL,
    PRIMARY KEY (evaluation_id, company_id, as_of_date, model_kind)
);

-- ---------------------------------------------------------------- jobs
CREATE TABLE pipeline_job (
    id           BIGSERIAL PRIMARY KEY,
    job_type     TEXT NOT NULL,
    status       TEXT NOT NULL,          -- RUNNING | SUCCEEDED | FAILED
    params       JSONB NOT NULL DEFAULT '{}'::jsonb,
    log          TEXT NOT NULL DEFAULT '',
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
