UPDATE workbench_settings
   SET proxy_mode = 'none',
       proxy_url = '',
       proxy_cookie = '',
       proxy_pool_enabled = FALSE,
       updated_at = now()
 WHERE id = 1;

UPDATE proxy_pool_sources
   SET enabled = FALSE,
       updated_at = now()
 WHERE enabled IS TRUE;
