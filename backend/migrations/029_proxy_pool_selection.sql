ALTER TABLE serverless_proxy_settings
  ADD COLUMN IF NOT EXISTS proxy_pool TEXT NOT NULL DEFAULT 'cloud';

ALTER TABLE serverless_proxy_settings
  DROP CONSTRAINT IF EXISTS serverless_proxy_settings_proxy_pool_check;

ALTER TABLE serverless_proxy_settings
  ADD CONSTRAINT serverless_proxy_settings_proxy_pool_check
  CHECK (proxy_pool IN ('cloud', 'manual'));

UPDATE serverless_proxy_settings
   SET proxy_pool = 'cloud'
 WHERE proxy_pool IS NULL OR proxy_pool NOT IN ('cloud', 'manual');
