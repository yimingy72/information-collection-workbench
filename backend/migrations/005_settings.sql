CREATE TABLE IF NOT EXISTS workbench_settings (
  id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  proxy_mode TEXT NOT NULL DEFAULT 'none' CHECK (proxy_mode IN ('none', 'http', 'socks', 'cookie')),
  proxy_url TEXT NOT NULL DEFAULT '',
  proxy_cookie TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO workbench_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS provider_sessions (
  provider TEXT PRIMARY KEY CHECK (provider IN ('aiqicha', 'kuaicha', 'riskbird')),
  cookie TEXT NOT NULL DEFAULT '',
  expires_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO provider_sessions (provider) VALUES ('aiqicha'), ('kuaicha'), ('riskbird')
ON CONFLICT DO NOTHING;
