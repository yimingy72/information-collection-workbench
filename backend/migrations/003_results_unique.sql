ALTER TABLE results DROP CONSTRAINT IF EXISTS results_run_id_entity_id_category_payload_key;
CREATE UNIQUE INDEX IF NOT EXISTS results_dedupe_idx
  ON results (run_id, entity_id, category, md5(payload::text));
