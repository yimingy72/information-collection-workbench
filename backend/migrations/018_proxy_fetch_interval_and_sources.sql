ALTER TABLE workbench_settings
  ALTER COLUMN proxy_fetch_interval_minutes SET DEFAULT 120;

UPDATE workbench_settings
   SET proxy_fetch_interval_minutes = 120,
       updated_at = now()
 WHERE id = 1;

INSERT INTO proxy_pool_sources(id, label, url)
VALUES
  ('proxycompass', 'ProxyCompass', 'https://proxycompass.com/free-proxies/asia/china/'),
  ('proxmint', 'Proxmint', 'https://proxmint.com/free-proxies/china'),
  ('proxyhub', 'ProxyHub', 'https://proxyhub.me/en/cn-http-proxy-list.html')
ON CONFLICT (id) DO UPDATE
   SET label = EXCLUDED.label,
       url = EXCLUDED.url,
       enabled = TRUE,
       updated_at = now();
