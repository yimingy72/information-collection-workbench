CREATE TABLE IF NOT EXISTS subdomain_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  domains JSONB NOT NULL DEFAULT '[]'::jsonb,
  source_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  options JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','running','succeeded','partial','failed','cancelled')),
  phase TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  progress INTEGER NOT NULL DEFAULT 0,
  total INTEGER,
  discovered INTEGER NOT NULL DEFAULT 0,
  warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
  error TEXT,
  lease_id UUID,
  heartbeat_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS subdomain_runs_created_idx ON subdomain_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS subdomain_runs_queue_idx ON subdomain_runs(status, created_at);

CREATE TABLE IF NOT EXISTS subdomain_results (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES subdomain_runs(id) ON DELETE CASCADE,
  root_domain TEXT NOT NULL,
  hostname TEXT NOT NULL,
  ips JSONB NOT NULL DEFAULT '[]'::jsonb,
  canonical_name TEXT NOT NULL DEFAULT '',
  dns_status TEXT NOT NULL DEFAULT 'resolved',
  wildcard BOOLEAN NOT NULL DEFAULT FALSE,
  http_url TEXT NOT NULL DEFAULT '',
  http_status INTEGER,
  title TEXT NOT NULL DEFAULT '',
  sources JSONB NOT NULL DEFAULT '[]'::jsonb,
  discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_id, root_domain, hostname)
);
CREATE INDEX IF NOT EXISTS subdomain_results_run_idx ON subdomain_results(run_id, id);
CREATE INDEX IF NOT EXISTS subdomain_results_hostname_idx ON subdomain_results(hostname);
