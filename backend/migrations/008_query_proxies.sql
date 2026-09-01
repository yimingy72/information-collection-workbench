CREATE TABLE IF NOT EXISTS query_proxies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scheme TEXT NOT NULL CHECK (scheme IN ('http', 'socks5')),
  host TEXT NOT NULL,
  port INTEGER NOT NULL CHECK (port > 0 AND port < 65536),
  username TEXT NOT NULL DEFAULT '',
  password TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL,
  alive BOOLEAN,
  latency_ms INTEGER,
  last_error TEXT NOT NULL DEFAULT '',
  last_checked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

UPDATE workbench_settings
   SET proxy_mode = 'none', proxy_url = '', proxy_cookie = '', updated_at = now()
 WHERE id = 1;
