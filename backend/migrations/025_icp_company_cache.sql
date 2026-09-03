CREATE TABLE IF NOT EXISTS icp_company_cache (
  company_name TEXT PRIMARY KEY,
  rows JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(rows) = 'array'),
  reported_total INTEGER NOT NULL CHECK (reported_total >= 0),
  saved_total INTEGER NOT NULL CHECK (saved_total >= 0),
  complete BOOLEAN NOT NULL DEFAULT FALSE,
  source TEXT NOT NULL DEFAULT 'miit',
  query_version TEXT NOT NULL,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS icp_company_cache_expires_idx
  ON icp_company_cache(expires_at);

ALTER TABLE collection_runs
  ADD COLUMN IF NOT EXISTS icp_cache_hits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE collection_runs
  ADD COLUMN IF NOT EXISTS icp_live_queries INTEGER NOT NULL DEFAULT 0;
