ALTER TABLE workbench_settings
  ALTER COLUMN proxy_max_latency_ms SET DEFAULT 2000;

UPDATE workbench_settings
   SET proxy_max_latency_ms = 2000,
       updated_at = now()
 WHERE id = 1
   AND proxy_max_latency_ms < 2000;
