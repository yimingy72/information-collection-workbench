INSERT INTO proxy_pool_sources(id, label, url)
VALUES
  ('databay', 'DataBay', 'https://databay.com/free-proxy-list'),
  ('hproxy', 'HProxy', 'https://github.com/hproxy-com/free-proxy-list'),
  ('xyzhealth', 'Proxy Health', 'https://github.com/xyzs996/free-proxy-health-list')
ON CONFLICT (id) DO UPDATE
   SET label = EXCLUDED.label,
       url = EXCLUDED.url,
       enabled = TRUE,
       updated_at = now();
