"""Offline comparison against a Git revision, with identical simulated I/O.

Run: python3 scripts/benchmark_query_pipeline.py --baseline HEAD --repeats 3
No real DNS, enterprise source or website requests are made.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import subdomains
from app.providers.aiqicha import AnonymousAiqicha
from app.providers.tianyancha import Investment


def baseline_module(revision: str, path: str, name: str):
    source = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT, text=True)
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, path, "exec"), module.__dict__)
    return module


async def pagination(provider_class):
    calls = 0

    async def fetch(page):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return [Investment(str(i), str(i), 100, {}) for i in range((page - 1) * 10, page * 10)], 120

    provider = provider_class()
    try:
        start = time.perf_counter()
        rows = await provider.all_pages(fetch)
        elapsed = time.perf_counter() - start
        assert {row.external_id for row in rows} == {str(i) for i in range(120)}
        return elapsed, len(rows), calls
    finally:
        await provider.close()


async def pipeline(module):
    class Repo:
        def __init__(self):
            self.rows = {}

        async def update_subdomain_progress(self, *args, **kwargs):
            pass

        async def add_subdomain_results(self, run_id, rows):
            before = len(self.rows)
            for row in rows:
                self.rows.setdefault(row["hostname"], {}).update({
                    key: value for key, value in row.items() if value not in (None, "", [])
                })
            return len(self.rows) - before

    counts = {"dns": 0, "http": 0}
    hosts = {f"p{i:04d}.example.com" for i in range(1000)}

    async def source(*args, **kwargs):
        return "fixture", hosts, ""

    async def wildcard(*args):
        return set()

    async def resolve(host):
        counts["dns"] += 1
        await asyncio.sleep(0)
        return module.ResolvedHost(host, ["93.184.216.34"])

    async def probe(client, resolved, root):
        counts["http"] += 1
        await asyncio.sleep(0.15 if resolved.hostname in {"p0000.example.com", "p0500.example.com"} else 0.0001)
        return module.HttpProbe(f"https://{resolved.hostname}/", 200)

    repo = Repo()
    with (
        patch.object(module, "PASSIVE_SOURCES", (("fixture", "collect_crtsh"),)),
        patch.object(module, "_call_source", source),
        patch.object(module, "_wildcard_ips", wildcard),
        patch.object(module, "resolve_hostname", resolve),
        patch.object(module, "probe_http", probe),
    ):
        start = time.perf_counter()
        warnings = await module.collect_subdomains(
            repo, uuid4(), ["example.com"],
            {"passive": True, "brute_force": False, "http_probe": True}, lease_id=uuid4(),
        )
        elapsed = time.perf_counter() - start
    assert not warnings
    assert set(repo.rows) == hosts
    assert all(row["http_status"] == 200 for row in repo.rows.values())
    return elapsed, len(repo.rows), counts


async def main(args):
    baseline_source = baseline_module(args.baseline, "backend/app/subdomains.py", "benchmark_old_subdomains")
    baseline_provider = baseline_module(args.baseline, "backend/app/providers/aiqicha.py", "benchmark_old_aiqicha")
    output = {"baseline": args.baseline, "repeats": args.repeats, "network": "simulated only", "cases": {}}
    for name, old, new in [
        ("investment_12_pages_20ms", lambda: pagination(baseline_provider.AnonymousAiqicha), lambda: pagination(AnonymousAiqicha)),
        ("subdomain_1000_hosts_two_slow_sites", lambda: pipeline(baseline_source), lambda: pipeline(subdomains)),
    ]:
        before, after = [], []
        for _ in range(args.repeats):
            before.append(await old())
            after.append(await new())
        old_time = statistics.median(item[0] for item in before)
        new_time = statistics.median(item[0] for item in after)
        output["cases"][name] = {
            "before_seconds": round(old_time, 4), "after_seconds": round(new_time, 4),
            "speedup": round(old_time / new_time, 2), "records": after[-1][1],
            "before_requests": before[-1][2], "after_requests": after[-1][2],
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="HEAD")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    asyncio.run(main(args))
