-- User-defined tags on companies: free-form categories such as a theme ("AI", "China exposed"), a watchlist
-- ("core", "watch only") or anything else worth filtering by. Tags carry no meaning for the models; they only
-- group stocks on the Companies, Universe and company pages and in the MCP tools.
CREATE TABLE company_tag (
    company_id  BIGINT      NOT NULL REFERENCES company(id),
    tag         TEXT        NOT NULL,            -- as typed (trimmed); matched case-insensitively
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, tag)
);
CREATE INDEX company_tag_tag_idx ON company_tag(lower(tag));
