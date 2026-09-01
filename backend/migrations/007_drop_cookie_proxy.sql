UPDATE workbench_settings
   SET proxy_mode = 'none', proxy_cookie = '', updated_at = now()
 WHERE proxy_mode = 'cookie';

ALTER TABLE workbench_settings DROP CONSTRAINT IF EXISTS workbench_settings_proxy_mode_check;
ALTER TABLE workbench_settings
  ADD CONSTRAINT workbench_settings_proxy_mode_check
  CHECK (proxy_mode IN ('none', 'http', 'socks'));
