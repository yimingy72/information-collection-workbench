CREATE TABLE IF NOT EXISTS subdomain_source_cache (
  root_domain TEXT NOT NULL,
  source TEXT NOT NULL,
  hosts JSONB NOT NULL DEFAULT '[]'::jsonb,
  refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(root_domain, source)
);
CREATE INDEX IF NOT EXISTS subdomain_source_cache_expiry_idx
  ON subdomain_source_cache(expires_at);
