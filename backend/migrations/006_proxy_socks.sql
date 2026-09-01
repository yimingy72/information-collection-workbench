ALTER TABLE workbench_settings DROP CONSTRAINT IF EXISTS workbench_settings_proxy_mode_check;
ALTER TABLE workbench_settings
  ADD CONSTRAINT workbench_settings_proxy_mode_check
  CHECK (proxy_mode IN ('none', 'http', 'socks', 'cookie'));
