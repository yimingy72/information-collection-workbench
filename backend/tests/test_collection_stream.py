from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app import main


def run_row(run_id, status="cancelled"):
    now = datetime.now(UTC)
    return {
        "id": run_id,
        "keyword": "实时测试企业",
        "provider": "tianyancha",
        "providers": ["tianyancha"],
        "depth": 1,
        "holding_percent": Decimal("51"),
        "include_branches": False,
        "fields": ["invest"],
        "status": status,
        "attempts": 1,
        "progress": 1,
        "total": 1,
        "icp_cache_hits": 0,
        "icp_live_queries": 1,
        "error": None,
        "created_at": now,
        "started_at": now,
        "finished_at": now if status == "cancelled" else None,
    }


@pytest.mark.asyncio
async def test_collection_stream_emits_incremental_rows_and_done(monkeypatch):
    run_id = uuid4()

    class Repo:
        async def get_run(self, _run_id):
            return run_row(run_id)

        async def collection_events_after(self, _run_id, rel_cursor, result_cursor, limit):
            assert (rel_cursor, result_cursor, limit) == (0, 0, 1000)
            return (
                [
                    {
                        "stream_seq": 12,
                        "parent_name": "母公司",
                        "child_name": "子公司",
                        "holding_percent": Decimal("51"),
                        "depth": 1,
                        "raw_payload": {"source": "天眼查"},
                    }
                ],
                [
                    {
                        "stream_seq": 34,
                        "entity_name": "子公司",
                        "payload": {
                            "unit_name": "子公司",
                            "main_licence": "京ICP备1号",
                            "service_licence": "京ICP备1号-1",
                            "domain": "example.com",
                            "nature_name": "企业",
                            "update_time": "2026-09-03",
                            "source": "ICP备案",
                        },
                    }
                ],
            )

    monkeypatch.setattr(main, "repo", Repo())
    response = await main.stream_collection_results(run_id, 0, 0)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    body = "".join(chunks)

    assert "event: delta" in body
    assert "event: progress" in body
    assert "event: done" in body
    delta_json = body.split("event: delta\ndata: ", 1)[1].split("\n\n", 1)[0]
    delta = json.loads(delta_json)
    assert delta["relationship_cursor"] == 12
    assert delta["result_cursor"] == 34
    assert delta["investments"][0]["child_name"] == "子公司"
    assert delta["icp_records"][0]["domain"] == "example.com"


@pytest.mark.asyncio
async def test_subdomain_stream_uses_update_cursor_and_emits_done(monkeypatch):
    run_id = uuid4()
    now = datetime.now(UTC)

    class Repo:
        async def get_subdomain_run(self, _run_id):
            return {
                "id": run_id,
                "domains": ["example.com"],
                "source_run_ids": [],
                "options": {},
                "status": "succeeded",
                "phase": "completed",
                "attempts": 1,
                "progress": 1,
                "total": 1,
                "discovered": 1,
                "warnings": [],
                "error": None,
                "created_at": now,
                "started_at": now,
                "finished_at": now,
            }

        async def subdomain_events_after(self, _run_id, after_seq, limit):
            assert (after_seq, limit) == (0, 500)
            return [{
                "id": 7,
                "run_id": run_id,
                "stream_seq": 22,
                "root_domain": "example.com",
                "hostname": "www.example.com",
                "ips": ["93.184.216.34"],
                "canonical_name": "",
                "dns_status": "resolved",
                "wildcard": False,
                "http_url": "https://www.example.com/",
                "http_status": 200,
                "title": "Example",
                "sources": ["DNS字典"],
                "discovered_at": now,
            }]

    monkeypatch.setattr(main, "repo", Repo())
    response = await main.stream_subdomain_results(run_id, 0)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    body = "".join(chunks)

    assert '"stream_seq":22' in body
    assert "event: progress" in body
    assert "event: done" in body
