CREATE TABLE IF NOT EXISTS serverless_proxy_settings (
  id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  provider TEXT NOT NULL DEFAULT 'aliyun' CHECK (provider IN ('aliyun', 'tencent', 'custom')),
  endpoint TEXT NOT NULL DEFAULT '',
  region TEXT NOT NULL DEFAULT 'cn-hangzhou',
  function_name TEXT NOT NULL DEFAULT 'asset-workbench-seamoon',
  image_uri TEXT NOT NULL DEFAULT '',
  access_key_id TEXT NOT NULL DEFAULT '',
  access_key_secret TEXT NOT NULL DEFAULT '',
  insecure_skip_verify BOOLEAN NOT NULL DEFAULT FALSE,
  deployment_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'not_configured'
    CHECK (status IN ('not_configured', 'configured', 'deploying', 'deployed', 'testing', 'ready', 'error')),
  last_error TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO serverless_proxy_settings (id) VALUES (1)
ON CONFLICT DO NOTHING;
