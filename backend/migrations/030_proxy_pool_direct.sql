ALTER TABLE serverless_proxy_settings
  DROP CONSTRAINT IF EXISTS serverless_proxy_settings_proxy_pool_check;

ALTER TABLE serverless_proxy_settings
  ADD CONSTRAINT serverless_proxy_settings_proxy_pool_check
  CHECK (proxy_pool IN ('cloud', 'manual', 'direct'));
