ALTER TABLE subdomain_runs
  ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '';

UPDATE subdomain_runs
   SET title = COALESCE(NULLIF(title, ''), domains->>0, '子域名查询')
 WHERE title = '';
