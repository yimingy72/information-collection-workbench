ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS quarantined_at TIMESTAMPTZ;
ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ;

ALTER TABLE query_proxies
  DROP CONSTRAINT IF EXISTS query_proxies_consecutive_failures_check;
ALTER TABLE query_proxies
  ADD CONSTRAINT query_proxies_consecutive_failures_check
  CHECK (consecutive_failures >= 0);

UPDATE query_proxies
   SET consecutive_failures = CASE WHEN alive IS FALSE THEN 1 ELSE 0 END,
       quarantined_at = NULL,
       last_success_at = CASE WHEN alive IS TRUE THEN last_checked_at ELSE NULL END,
       next_check_at = now();

CREATE INDEX IF NOT EXISTS query_proxies_quarantined_at_idx
  ON query_proxies(quarantined_at)
  WHERE quarantined_at IS NOT NULL;
