ALTER TABLE serverless_proxy_settings
  ADD COLUMN IF NOT EXISTS nodes JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Keep an already configured single function available as the first pool node.
UPDATE serverless_proxy_settings
   SET nodes = jsonb_build_array(jsonb_build_object(
     'id', provider || ':' || region || ':' || function_name,
     'enabled', enabled,
     'provider', provider,
     'endpoint', endpoint,
     'region', region,
     'function_name', function_name,
     'image_uri', image_uri,
     'access_key_id', access_key_id,
     'access_key_secret', access_key_secret,
     'insecure_skip_verify', insecure_skip_verify,
     'deployment_id', deployment_id,
     'status', status,
     'last_error', last_error,
     'latency_ms', NULL,
     'failure_count', 0,
     'updated_at', updated_at
   ))
 WHERE endpoint <> ''
   AND jsonb_array_length(nodes) = 0;
