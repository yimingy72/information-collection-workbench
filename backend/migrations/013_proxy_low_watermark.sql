ALTER TABLE workbench_settings
  ADD COLUMN IF NOT EXISTS proxy_min_alive INTEGER NOT NULL DEFAULT 30;

ALTER TABLE workbench_settings
  DROP CONSTRAINT IF EXISTS workbench_settings_proxy_min_alive_check;
ALTER TABLE workbench_settings
  ADD CONSTRAINT workbench_settings_proxy_min_alive_check
  CHECK (proxy_min_alive >= 1 AND proxy_min_alive <= 10000);

UPDATE workbench_settings
   SET proxy_min_alive = 30,
       proxy_fetch_interval_minutes = 10,
       updated_at = now()
 WHERE id = 1;

UPDATE query_proxies
   SET origin = 'pool',
       sources = array_remove(array_remove(sources, 'zdaye'), 'proxy_tools');

ALTER TABLE query_proxies ALTER COLUMN origin SET DEFAULT 'pool';

DELETE FROM proxy_pool_sources WHERE id IN ('zdaye', 'proxy_tools');

INSERT INTO proxy_pool_sources(id, label, url)
VALUES
  ('66daili', '66代理', 'https://www.66daili.com/'),
  ('iplocate', 'IPLocate', 'https://github.com/iplocate/free-proxy-list')
ON CONFLICT (id) DO UPDATE
   SET label = EXCLUDED.label,
       url = EXCLUDED.url,
       enabled = TRUE,
       updated_at = now();
