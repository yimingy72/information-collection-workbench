ALTER TABLE workbench_settings
  ADD COLUMN IF NOT EXISTS probe_interval_minutes INTEGER NOT NULL DEFAULT 1;
ALTER TABLE workbench_settings
  ADD COLUMN IF NOT EXISTS proxy_cursor INTEGER NOT NULL DEFAULT 0;

ALTER TABLE workbench_settings
  DROP CONSTRAINT IF EXISTS workbench_settings_probe_interval_minutes_check;
ALTER TABLE workbench_settings
  ADD CONSTRAINT workbench_settings_probe_interval_minutes_check
  CHECK (probe_interval_minutes >= 1 AND probe_interval_minutes <= 1440);

CREATE UNIQUE INDEX IF NOT EXISTS query_proxies_url_uidx ON query_proxies(url);
