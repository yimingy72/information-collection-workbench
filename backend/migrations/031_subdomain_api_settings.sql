CREATE TABLE IF NOT EXISTS subdomain_api_settings (
  id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  fofa_email TEXT NOT NULL DEFAULT '',
  fofa_key TEXT NOT NULL DEFAULT '',
  hunter_key TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO subdomain_api_settings (id) VALUES (1) ON CONFLICT DO NOTHING;
