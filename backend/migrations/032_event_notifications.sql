-- Wake API SSE streams after committed, user-visible changes. Batch result
-- writes use statement-level transition tables so one transaction emits at
-- most one notification per affected run instead of invoking a trigger per row.
CREATE OR REPLACE FUNCTION notify_asset_workbench_run_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  event_run_id TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    event_run_id := OLD.id::text;
  ELSE
    event_run_id := NEW.id::text;
  END IF;
  PERFORM pg_notify(TG_ARGV[0], event_run_id);
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION notify_asset_workbench_result_events()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  event_run_id TEXT;
  category_filter TEXT := NULLIF(TG_ARGV[1], '');
BEGIN
  FOR event_run_id IN
    SELECT DISTINCT event_row.run_id::text
      FROM event_rows AS event_row
     WHERE category_filter IS NULL
        OR to_jsonb(event_row) ->> 'category' = category_filter
  LOOP
    PERFORM pg_notify(TG_ARGV[0], event_run_id);
  END LOOP;
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS relationships_event_notify ON relationships;
CREATE TRIGGER relationships_event_notify
AFTER INSERT ON relationships
REFERENCING NEW TABLE AS event_rows
FOR EACH STATEMENT
EXECUTE FUNCTION notify_asset_workbench_result_events('collection_events', '');

DROP TRIGGER IF EXISTS icp_results_event_notify ON results;
CREATE TRIGGER icp_results_event_notify
AFTER INSERT ON results
REFERENCING NEW TABLE AS event_rows
FOR EACH STATEMENT
EXECUTE FUNCTION notify_asset_workbench_result_events('collection_events', 'icp');

DROP TRIGGER IF EXISTS collection_runs_update_event_notify ON collection_runs;
CREATE TRIGGER collection_runs_update_event_notify
AFTER UPDATE OF status, progress, total, error, icp_cache_hits, icp_live_queries
ON collection_runs
FOR EACH ROW
WHEN ((OLD.status, OLD.progress, OLD.total, OLD.error, OLD.icp_cache_hits, OLD.icp_live_queries)
      IS DISTINCT FROM
      (NEW.status, NEW.progress, NEW.total, NEW.error, NEW.icp_cache_hits, NEW.icp_live_queries))
EXECUTE FUNCTION notify_asset_workbench_run_event('collection_events');

DROP TRIGGER IF EXISTS collection_runs_delete_event_notify ON collection_runs;
CREATE TRIGGER collection_runs_delete_event_notify
AFTER DELETE ON collection_runs
FOR EACH ROW EXECUTE FUNCTION notify_asset_workbench_run_event('collection_events');

DROP TRIGGER IF EXISTS subdomain_results_event_notify ON subdomain_results;
CREATE TRIGGER subdomain_results_event_notify
AFTER INSERT ON subdomain_results
REFERENCING NEW TABLE AS event_rows
FOR EACH STATEMENT
EXECUTE FUNCTION notify_asset_workbench_result_events('subdomain_events', '');

DROP TRIGGER IF EXISTS subdomain_results_update_event_notify ON subdomain_results;
CREATE TRIGGER subdomain_results_update_event_notify
AFTER UPDATE ON subdomain_results
REFERENCING NEW TABLE AS event_rows
FOR EACH STATEMENT
EXECUTE FUNCTION notify_asset_workbench_result_events('subdomain_events', '');

DROP TRIGGER IF EXISTS subdomain_runs_update_event_notify ON subdomain_runs;
CREATE TRIGGER subdomain_runs_update_event_notify
AFTER UPDATE OF status, phase, progress, total, discovered, warnings, error
ON subdomain_runs
FOR EACH ROW
WHEN ((OLD.status, OLD.phase, OLD.progress, OLD.total, OLD.discovered, OLD.warnings, OLD.error)
      IS DISTINCT FROM
      (NEW.status, NEW.phase, NEW.progress, NEW.total, NEW.discovered, NEW.warnings, NEW.error))
EXECUTE FUNCTION notify_asset_workbench_run_event('subdomain_events');

DROP TRIGGER IF EXISTS subdomain_runs_delete_event_notify ON subdomain_runs;
CREATE TRIGGER subdomain_runs_delete_event_notify
AFTER DELETE ON subdomain_runs
FOR EACH ROW EXECUTE FUNCTION notify_asset_workbench_run_event('subdomain_events');
