ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS sources TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS country_code TEXT NOT NULL DEFAULT '';
ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS location TEXT NOT NULL DEFAULT '';
ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE query_proxies
  ADD COLUMN IF NOT EXISTS next_check_at TIMESTAMPTZ;

ALTER TABLE query_proxies DROP CONSTRAINT IF EXISTS query_proxies_origin_check;
ALTER TABLE query_proxies
  ADD CONSTRAINT query_proxies_origin_check CHECK (origin IN ('manual', 'pool'));

UPDATE query_proxies
   SET origin = COALESCE(NULLIF(origin, ''), 'manual'),
       next_check_at = COALESCE(
         next_check_at,
         now() + (mod((hashtextextended(url, 0) & 9223372036854775807), 600) * interval '1 second')
       );

ALTER TABLE query_proxies ALTER COLUMN next_check_at SET DEFAULT now();
ALTER TABLE query_proxies ALTER COLUMN next_check_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS query_proxies_next_check_idx
  ON query_proxies(next_check_at, created_at);
CREATE INDEX IF NOT EXISTS query_proxies_origin_idx
  ON query_proxies(origin);

ALTER TABLE workbench_settings
  ADD COLUMN IF NOT EXISTS proxy_pool_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE workbench_settings
  ADD COLUMN IF NOT EXISTS proxy_fetch_interval_minutes INTEGER NOT NULL DEFAULT 10;
ALTER TABLE workbench_settings
  ADD COLUMN IF NOT EXISTS proxy_max_latency_ms INTEGER NOT NULL DEFAULT 1000;

ALTER TABLE workbench_settings
  DROP CONSTRAINT IF EXISTS workbench_settings_proxy_fetch_interval_check;
ALTER TABLE workbench_settings
  ADD CONSTRAINT workbench_settings_proxy_fetch_interval_check
  CHECK (proxy_fetch_interval_minutes >= 1 AND proxy_fetch_interval_minutes <= 1440);

ALTER TABLE workbench_settings
  DROP CONSTRAINT IF EXISTS workbench_settings_proxy_max_latency_check;
ALTER TABLE workbench_settings
  ADD CONSTRAINT workbench_settings_proxy_max_latency_check
  CHECK (proxy_max_latency_ms >= 100 AND proxy_max_latency_ms <= 30000);

UPDATE workbench_settings
   SET probe_interval_minutes = 10,
       proxy_fetch_interval_minutes = 10,
       proxy_max_latency_ms = 1000,
       updated_at = now()
 WHERE id = 1;

ALTER TABLE workbench_settings
  ALTER COLUMN probe_interval_minutes SET DEFAULT 10;

CREATE TABLE IF NOT EXISTS proxy_pool_sources (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  url TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  last_fetch_at TIMESTAMPTZ,
  last_success_at TIMESTAMPTZ,
  last_count INTEGER NOT NULL DEFAULT 0,
  last_added INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO proxy_pool_sources(id, label, url)
VALUES
  ('zdaye', '站大爷', 'https://www.zdaye.com/free/?ip_adr=&checktime=&sleep=2&cunhuo=&dengji=&protocol=&yys=&px='),
  ('proxy5', 'Proxy5', 'https://proxy5.net/cn/free-proxy'),
  ('kuaidaili', '快代理', 'https://www.kuaidaili.com/free/'),
  ('proxy_tools', 'Proxy-Tools', 'https://cn.proxy-tools.com/proxy/cn'),
  ('qiyunip', '齐云代理', 'https://www.qiyunip.com/freeProxy/')
ON CONFLICT (id) DO UPDATE
   SET label = EXCLUDED.label,
       url = EXCLUDED.url,
       updated_at = now();
