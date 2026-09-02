from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg


class LeaseLost(RuntimeError):
    pass


def _json_encode(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


async def _init_connection(connection: asyncpg.Connection) -> None:
    await connection.set_type_codec("jsonb", encoder=_json_encode, decoder=json.loads, schema="pg_catalog")
    await connection.set_type_codec("json", encoder=_json_encode, decoder=json.loads, schema="pg_catalog")


async def create_pool(url: str, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(url, min_size=min_size, max_size=max_size, init=_init_connection)


class Repository:
    def __init__(self, pool: asyncpg.Pool, migration_dir: Path) -> None:
        self.pool = pool
        self.migration_dir = migration_dir

    async def migrate(self) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                      filename TEXT PRIMARY KEY,
                      applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                applied = {
                    row["filename"]
                    for row in await connection.fetch("SELECT filename FROM schema_migrations")
                }
                for path in sorted(self.migration_dir.glob("*.sql")):
                    if path.name in applied:
                        continue
                    await connection.execute(path.read_text())
                    await connection.execute(
                        "INSERT INTO schema_migrations(filename) VALUES($1)", path.name
                    )

    async def recover_stale(self, lease_seconds: int) -> None:
        await self.pool.execute(
            """
            UPDATE collection_runs
               SET status='queued', error='worker lease expired; requeued',
                   heartbeat_at=NULL, lease_id=NULL
             WHERE status='running'
               AND (heartbeat_at IS NULL OR heartbeat_at < now() - ($1 * interval '1 second'))
            """,
            lease_seconds,
        )

    async def create_run(self, request: dict[str, Any]) -> UUID:
        from app.providers.names import normalize_providers

        providers = normalize_providers(request.get("providers") or [request.get("provider") or "tianyancha"])
        row = await self.pool.fetchrow(
            """
            INSERT INTO collection_runs
              (kind, keyword, provider, providers, depth, holding_percent, include_branches, fields, request)
            VALUES ('enterprise', $1, $2, $3::jsonb, $4, $5, $6, $7::jsonb, $8::jsonb)
            RETURNING id
            """,
            request["keyword"], providers[0], json.dumps(providers), request["depth"], request["holding_percent"],
            request["include_branches"], json.dumps(request.get("fields") or ["invest"]), json.dumps(request),
        )
        return row["id"]

    async def start_sync(self, run_id: UUID) -> None:
        await self.pool.execute(
            """
            UPDATE collection_runs
               SET status='running', attempts=attempts+1, started_at=COALESCE(started_at, now()),
                   heartbeat_at=now(), error=NULL
             WHERE id=$1 AND status='queued'
            """,
            run_id,
        )

    async def list_runs(
        self, limit: int, offset: int, keyword: str = "", status: str = ""
    ) -> tuple[list[asyncpg.Record], int]:
        keyword = " ".join(str(keyword or "").split())
        status = str(status or "").strip()
        rows = await self.pool.fetch(
            """
            SELECT * FROM collection_runs
             WHERE ($3 = '' OR keyword ILIKE '%' || $3 || '%')
               AND ($4 = '' OR status = $4)
             ORDER BY created_at DESC
             LIMIT $1 OFFSET $2
            """,
            limit, offset, keyword, status,
        )
        total = await self.pool.fetchval(
            """
            SELECT count(*) FROM collection_runs
             WHERE ($1 = '' OR keyword ILIKE '%' || $1 || '%')
               AND ($2 = '' OR status = $2)
            """,
            keyword, status,
        )
        return rows, int(total or 0)

    async def get_run(self, run_id: UUID) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM collection_runs WHERE id=$1", run_id)

    async def claim_run(self, lease_seconds: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            """
            WITH candidate AS (
              SELECT id FROM collection_runs
               WHERE status='queued'
               ORDER BY created_at
               FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE collection_runs AS r
               SET status='running', attempts=r.attempts+1, started_at=COALESCE(r.started_at, now()),
                   heartbeat_at=now(), error=NULL, lease_id=gen_random_uuid()
             FROM candidate WHERE r.id=candidate.id
             RETURNING r.*
            """
        )

    async def heartbeat(
        self, run_id: UUID, progress: int, total: int | None = None, lease_id: UUID | None = None
    ) -> None:
        result = await self.pool.execute(
            """
            UPDATE collection_runs
               SET progress=$2, total=COALESCE($3,total), heartbeat_at=now()
             WHERE id=$1 AND status='running' AND ($4::uuid IS NULL OR lease_id=$4)
            """,
            run_id, progress, total, lease_id,
        )
        if lease_id is not None and result == "UPDATE 0":
            raise LeaseLost(str(run_id))

    async def touch_run(self, run_id: UUID, lease_id: UUID | None = None) -> None:
        result = await self.pool.execute(
            """
            UPDATE collection_runs
               SET heartbeat_at=now()
             WHERE id=$1
               AND status='running'
               AND (($2::uuid IS NULL AND lease_id IS NULL) OR lease_id=$2)
            """,
            run_id, lease_id,
        )
        if result == "UPDATE 0":
            raise LeaseLost(str(run_id))

    async def finish(
        self, run_id: UUID, status: str, error: str | None = None, lease_id: UUID | None = None
    ) -> None:
        result = await self.pool.execute(
            """
            UPDATE collection_runs
               SET status=$2, error=$3, finished_at=now(), heartbeat_at=NULL
             WHERE id=$1 AND status='running' AND ($4::uuid IS NULL OR lease_id=$4)
            """,
            run_id, status, error, lease_id,
        )
        if lease_id is not None and result == "UPDATE 0":
            raise LeaseLost(str(run_id))

    async def has_results(self, run_id: UUID) -> bool:
        return bool(
            await self.pool.fetchval(
                """
                SELECT EXISTS(SELECT 1 FROM results WHERE run_id=$1)
                    OR EXISTS(SELECT 1 FROM relationships WHERE run_id=$1)
                """,
                run_id,
            )
        )

    async def upsert_entity(self, provider: str, external_id: str, name: str, payload: dict) -> UUID:
        row = await self.pool.fetchrow(
            """
            INSERT INTO entities(provider, external_id, name, payload)
            VALUES($1,$2,$3,$4::jsonb)
            ON CONFLICT(provider, external_id) DO UPDATE SET name = entities.name
            RETURNING id
            """,
            provider, external_id, name, json.dumps(payload),
        )
        return row["id"]

    async def add_relationship(
        self, run_id: UUID, parent_id: UUID, child_id: UUID, relation_type: str,
        holding_percent: float | None, depth: int, reference: str,
        source_url: str, raw_payload: dict,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO relationships(run_id,parent_entity_id,child_entity_id,relation_type,holding_percent,
                                      depth,reference,source_url,raw_payload)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
            ON CONFLICT DO NOTHING
            """,
            run_id, parent_id, child_id, relation_type, holding_percent, depth, reference,
            source_url, json.dumps(raw_payload),
        )

    async def add_result(
        self, run_id: UUID, entity_id: UUID, category: str, payload: dict,
        source_url: str, raw_payload: dict | None = None,
    ) -> None:
        raw = json.dumps(raw_payload if raw_payload is not None else payload)
        await self.pool.execute(
            """
            INSERT INTO results(run_id,entity_id,category,payload,source_url,raw_payload)
            VALUES($1,$2,$3,$4::jsonb,$5,$6::jsonb) ON CONFLICT DO NOTHING
            """,
            run_id, entity_id, category, json.dumps(payload), source_url, raw,
        )

    async def results(
        self, run_id: UUID, category: str | None, limit: int, offset: int,
        relationship_limit: int | None = 200, relationship_offset: int = 0,
    ) -> tuple[list, list, int, int]:
        if category:
            rows = await self.pool.fetch(
                """
                SELECT r.id,r.category,r.entity_id,e.name entity_name,r.payload,r.source_url,r.captured_at
                  FROM results r JOIN entities e ON e.id=r.entity_id
                 WHERE r.run_id=$1 AND r.category=$2
                 ORDER BY r.captured_at,r.id LIMIT $3 OFFSET $4
                """,
                run_id, category, limit, offset,
            )
            count_result = await self.pool.fetchval(
                "SELECT count(*) FROM results WHERE run_id=$1 AND category=$2", run_id, category
            )
        else:
            rows = await self.pool.fetch(
                """
                SELECT r.id,r.category,r.entity_id,e.name entity_name,r.payload,r.source_url,r.captured_at
                  FROM results r JOIN entities e ON e.id=r.entity_id
                 WHERE r.run_id=$1
                 ORDER BY r.captured_at,r.id LIMIT $2 OFFSET $3
                """,
                run_id, limit, offset,
            )
            count_result = await self.pool.fetchval("SELECT count(*) FROM results WHERE run_id=$1", run_id)
        relationship_query = """
            SELECT rel.id,rel.parent_entity_id,p.name parent_name,rel.child_entity_id,c.name child_name,
                   rel.relation_type,rel.holding_percent,rel.depth,rel.reference,rel.source_url,rel.captured_at,
                   rel.raw_payload
              FROM relationships rel
              JOIN entities p ON p.id=rel.parent_entity_id
              JOIN entities c ON c.id=rel.child_entity_id
             WHERE rel.run_id=$1
             ORDER BY rel.depth,rel.id
        """
        if relationship_limit is None:
            # The query view needs the complete relationship set. A fixed 1000-row
            # limit here made historical records appear truncated even though the
            # remaining relationships were already persisted in PostgreSQL.
            rels = await self.pool.fetch(relationship_query, run_id)
        else:
            rels = await self.pool.fetch(
                relationship_query + " LIMIT $2 OFFSET $3",
                run_id, relationship_limit, relationship_offset,
            )
        count_rel = await self.pool.fetchval("SELECT count(*) FROM relationships WHERE run_id=$1", run_id)
        return rows, rels, int(count_result or 0), int(count_rel or 0)

    async def entity_names_for_run(self, run_id: UUID) -> list[str]:
        rows = await self.pool.fetch(
            """
            SELECT DISTINCT name FROM (
                SELECT p.name FROM relationships rel
                  JOIN entities p ON p.id = rel.parent_entity_id
                 WHERE rel.run_id = $1
                UNION
                SELECT c.name FROM relationships rel
                  JOIN entities c ON c.id = rel.child_entity_id
                 WHERE rel.run_id = $1
                UNION
                SELECT e.name FROM results r
                  JOIN entities e ON e.id = r.entity_id
                 WHERE r.run_id = $1
                   AND r.category IN ('company_selection', 'invest', 'partner')
            ) names
            ORDER BY name
            """,
            run_id,
        )
        return [row["name"] for row in rows]

    async def delete_runs(self, run_ids: list[UUID]) -> int:
        if not run_ids:
            return 0
        result = await self.pool.execute(
            "DELETE FROM collection_runs WHERE id = ANY($1::uuid[])",
            run_ids,
        )
        return int(str(result).split()[-1])

    async def get_runtime_config(self) -> dict[str, Any]:
        sessions = await self.pool.fetch("SELECT provider, cookie, expires_at, updated_at FROM provider_sessions")
        serverless_proxy = await self.pool.fetchrow(
            "SELECT * FROM serverless_proxy_settings WHERE id=1"
        )
        manual_proxies = await self.pool.fetch("SELECT * FROM manual_proxy_nodes ORDER BY created_at, id")
        return {
            "sessions": {row["provider"]: dict(row) for row in sessions},
            "serverless_proxy": dict(serverless_proxy) if serverless_proxy else {},
            "manual_proxies": [dict(row) for row in manual_proxies],
        }

    async def update_serverless_proxy(self, config: dict[str, Any]) -> asyncpg.Record:
        return await self.pool.fetchrow(
            """
            UPDATE serverless_proxy_settings
               SET enabled=$1,
                   provider=$2,
                   endpoint=$3,
                   region=$4,
                   function_name=$5,
                   image_uri=$6,
                   access_key_id=$7,
                   access_key_secret=COALESCE($8, access_key_secret),
                   insecure_skip_verify=$9,
                   nodes=COALESCE($10::jsonb, nodes),
                   deployment_id=CASE
                     WHEN provider <> $2 OR region <> $4 OR function_name <> $5 THEN ''
                     ELSE deployment_id
                   END,
                   status=CASE
                     WHEN $1 AND $3 <> '' THEN 'configured'
                     WHEN $3 <> '' THEN 'configured'
                     ELSE 'not_configured'
                   END,
                   last_error='',
                   updated_at=now()
             WHERE id=1
            RETURNING *
            """,
            config["enabled"], config["provider"], config["endpoint"], config["region"],
            config["function_name"], config["image_uri"], config["access_key_id"],
            config.get("access_key_secret"), config["insecure_skip_verify"],
            json.dumps(config["nodes"]) if "nodes" in config else None,
        )

    async def set_serverless_proxy_status(
        self,
        status: str,
        error: str = "",
        *,
        endpoint: str | None = None,
        deployment_id: str | None = None,
        enabled: bool | None = None,
    ) -> asyncpg.Record:
        return await self.pool.fetchrow(
            """
            UPDATE serverless_proxy_settings
               SET status=$1,
                   last_error=$2,
                   endpoint=COALESCE($3, endpoint),
                   deployment_id=COALESCE($4, deployment_id),
                   enabled=COALESCE($5, enabled),
                   updated_at=now()
             WHERE id=1
            RETURNING *
            """,
            status, error, endpoint, deployment_id, enabled,
        )

    async def clear_serverless_proxy_deployment(self) -> asyncpg.Record:
        return await self.pool.fetchrow(
            """
            UPDATE serverless_proxy_settings
               SET enabled=FALSE,
                   endpoint='',
                   deployment_id='',
                   status='configured',
                   last_error='',
                   updated_at=now()
             WHERE id=1
            RETURNING *
            """
        )

    async def list_manual_proxies(self) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM manual_proxy_nodes ORDER BY created_at, id"
        )

    async def get_manual_proxy(self, proxy_id: UUID) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT * FROM manual_proxy_nodes WHERE id=$1",
            proxy_id,
        )

    async def create_manual_proxy(
        self,
        *,
        scheme: str,
        host: str,
        port: int,
        username: str,
        password: str,
        enabled: bool,
    ) -> asyncpg.Record:
        return await self.pool.fetchrow(
            """
            INSERT INTO manual_proxy_nodes
              (scheme, host, port, username, password, enabled, status, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, CASE WHEN $6 THEN 'configured' ELSE 'disabled' END, now())
            RETURNING *
            """,
            scheme, host, port, username, password, enabled,
        )

    async def update_manual_proxy(
        self,
        proxy_id: UUID,
        *,
        scheme: str,
        host: str,
        port: int,
        username: str,
        password: str | None,
        enabled: bool,
    ) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            """
            UPDATE manual_proxy_nodes
               SET scheme=$2,
                   host=$3,
                   port=$4,
                   username=$5,
                   password=COALESCE($6, password),
                   enabled=$7,
                   status=CASE WHEN $7 THEN CASE WHEN status='ready' THEN status ELSE 'configured' END ELSE 'disabled' END,
                   last_error=CASE WHEN $7 THEN '' ELSE last_error END,
                   updated_at=now()
             WHERE id=$1
            RETURNING *
            """,
            proxy_id, scheme, host, port, username, password, enabled,
        )

    async def delete_manual_proxy(self, proxy_id: UUID) -> int:
        result = await self.pool.execute("DELETE FROM manual_proxy_nodes WHERE id=$1", proxy_id)
        return int(str(result).split()[-1])

    async def set_manual_proxy_result(
        self,
        proxy_id: UUID,
        *,
        status: str,
        latency_ms: int | None = None,
        error: str = "",
        enabled: bool | None = None,
    ) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            """
            UPDATE manual_proxy_nodes
               SET status=$2,
                   latency_ms=$3,
                   last_error=$4,
                   enabled=COALESCE($5, enabled),
                   failure_count=CASE WHEN $2='ready' THEN 0 ELSE failure_count + 1 END,
                   last_tested_at=now(),
                   updated_at=now()
             WHERE id=$1
            RETURNING *
            """,
            proxy_id, status, latency_ms, error, enabled,
        )

    async def list_provider_sessions(self) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT provider, cookie, expires_at, updated_at FROM provider_sessions ORDER BY provider"
        )

    async def upsert_provider_session(self, provider: str, cookie: str, expires_at: Any) -> asyncpg.Record:
        return await self.pool.fetchrow(
            """
            INSERT INTO provider_sessions(provider, cookie, expires_at, updated_at)
            VALUES($1,$2,$3,now())
            ON CONFLICT (provider) DO UPDATE
               SET cookie=EXCLUDED.cookie, expires_at=EXCLUDED.expires_at, updated_at=now()
            RETURNING provider, cookie, expires_at, updated_at
            """,
            provider, cookie, expires_at,
        )

    async def clear_provider_session(self, provider: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            """
            UPDATE provider_sessions
               SET cookie='', expires_at=NULL, updated_at=now()
             WHERE provider=$1
            RETURNING provider, cookie, expires_at, updated_at
            """,
            provider,
        )
