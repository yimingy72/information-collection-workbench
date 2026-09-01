ALTER TABLE collection_runs DROP CONSTRAINT IF EXISTS collection_runs_provider_check;
UPDATE collection_runs SET provider = 'tianyancha' WHERE provider IN ('tianyancha-anonymous', 'tianyancha');
ALTER TABLE collection_runs ADD COLUMN IF NOT EXISTS providers JSONB NOT NULL DEFAULT '["tianyancha"]'::jsonb;
