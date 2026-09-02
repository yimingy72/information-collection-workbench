CREATE TABLE IF NOT EXISTS manual_proxy_nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scheme TEXT NOT NULL DEFAULT 'http' CHECK (scheme IN ('http', 'https')),
  host TEXT NOT NULL,
  port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
  username TEXT NOT NULL DEFAULT '',
  password TEXT NOT NULL DEFAULT '',
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  status TEXT NOT NULL DEFAULT 'configured'
    CHECK (status IN ('configured', 'testing', 'ready', 'error', 'disabled')),
  latency_ms INTEGER,
  failure_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  last_tested_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (scheme, host, port, username)
);
