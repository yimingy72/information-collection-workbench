-- Monotonic change cursor for live subdomain result delivery.
-- A DNS row is written before HTTP enrichment; updates to that same row must
-- still be visible to an already-open SSE connection after a page refresh.
CREATE SEQUENCE IF NOT EXISTS subdomain_results_stream_seq_seq;

ALTER TABLE subdomain_results
  ADD COLUMN IF NOT EXISTS stream_seq BIGINT;

UPDATE subdomain_results
   SET stream_seq = nextval('subdomain_results_stream_seq_seq')
 WHERE stream_seq IS NULL;

ALTER TABLE subdomain_results
  ALTER COLUMN stream_seq SET DEFAULT nextval('subdomain_results_stream_seq_seq'),
  ALTER COLUMN stream_seq SET NOT NULL;

SELECT setval(
  'subdomain_results_stream_seq_seq',
  COALESCE((SELECT max(stream_seq) FROM subdomain_results), 1),
  TRUE
);

ALTER SEQUENCE subdomain_results_stream_seq_seq
  OWNED BY subdomain_results.stream_seq;

CREATE INDEX IF NOT EXISTS subdomain_results_run_stream_idx
  ON subdomain_results(run_id, stream_seq);
