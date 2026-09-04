ALTER TABLE relationships
  ADD COLUMN IF NOT EXISTS stream_seq BIGSERIAL;
ALTER TABLE results
  ADD COLUMN IF NOT EXISTS stream_seq BIGSERIAL;

CREATE INDEX IF NOT EXISTS relationships_run_stream_idx
  ON relationships(run_id, stream_seq);
CREATE INDEX IF NOT EXISTS results_run_stream_idx
  ON results(run_id, stream_seq);
