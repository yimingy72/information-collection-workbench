UPDATE workbench_settings
   SET probe_interval_minutes = 5, updated_at = now()
 WHERE id = 1 AND probe_interval_minutes = 1;

ALTER TABLE workbench_settings
  ALTER COLUMN probe_interval_minutes SET DEFAULT 5;
