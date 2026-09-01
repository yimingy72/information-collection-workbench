CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS collection_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind TEXT NOT NULL CHECK (kind = 'enterprise'),
  keyword TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider = 'tianyancha-anonymous'),
  depth INTEGER NOT NULL CHECK (depth BETWEEN 1 AND 5),
  holding_percent NUMERIC(5,2) NOT NULL CHECK (holding_percent BETWEEN 0 AND 100),
  include_branches BOOLEAN NOT NULL DEFAULT FALSE,
  fields JSONB NOT NULL,
  request JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','partial','failed','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0,
  progress INTEGER NOT NULL DEFAULT 0,
  total INTEGER,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS collection_runs_queue_idx ON collection_runs(status, created_at);

CREATE TABLE IF NOT EXISTS entities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  name TEXT NOT NULL,
  entity_type TEXT NOT NULL DEFAULT 'company',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(provider, external_id)
);

CREATE TABLE IF NOT EXISTS relationships (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
  parent_entity_id UUID NOT NULL REFERENCES entities(id),
  child_entity_id UUID NOT NULL REFERENCES entities(id),
  relation_type TEXT NOT NULL,
  holding_percent NUMERIC(8,4),
  depth INTEGER NOT NULL,
  reference TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id, parent_entity_id, child_entity_id, relation_type)
);
CREATE INDEX IF NOT EXISTS relationships_run_idx ON relationships(run_id, depth);

CREATE TABLE IF NOT EXISTS results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
  entity_id UUID NOT NULL REFERENCES entities(id),
  category TEXT NOT NULL,
  payload JSONB NOT NULL,
  source_url TEXT NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(run_id, entity_id, category, payload)
);
CREATE INDEX IF NOT EXISTS results_run_idx ON results(run_id, category);
