from uuid import uuid4
import asyncio
from collections import Counter

import pytest

from app import collector
from app.providers.tianyancha import Company, Investment
from app.providers.pagination import IncompletePagination


class TraversalRepo:
    def __init__(self):
        self.entities = {}
        self.relationships = []
        self.results = []

    async def upsert_entity(self, provider, external_id, *args):
        return self.entities.setdefault(external_id, uuid4())

    async def add_result(self, *args):
        self.results.append(args)

    async def add_relationship(self, *args):
        self.relationships.append(args)

    async def heartbeat(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_shared_company_uses_shortest_path_and_is_fetched_once():
    calls = Counter()

    class Provider:
        id = "test"
        label = "test"

        async def search(self, keyword):
            root = Company("root", "root", {})
            return root, [root]

        async def all_pages(self, fetch):
            return (await fetch(1))[0]

        async def investments(self, company, page):
            calls[company] += 1
            if company == "slow":
                await asyncio.sleep(0.02)
            graph = {"root": ["fast", "slow"], "fast": ["middle"],
                     "middle": ["shared"], "slow": ["shared"], "shared": ["leaf"]}
            children = graph.get(company, [])
            return [Investment(child, child, 100, {}) for child in children], len(children)

    repo = TraversalRepo()
    errors = await collector._collect_one(repo, Provider(), collector.RunSpec(uuid4(), "root", 3, 100, ["invest"]))
    assert errors == []
    assert "leaf" in repo.entities
    assert calls["shared"] == 1
    assert max(calls.values()) == 1
    leaf_rows = [row for row in repo.relationships if row[2] == repo.entities["leaf"]]
    assert leaf_rows[0][5] == 3


@pytest.mark.asyncio
async def test_partial_provider_pages_are_kept_and_their_children_are_traversed():
    class Provider:
        id = "test"
        label = "test"

        async def search(self, keyword):
            root = Company("root", "root", {})
            return root, [root]

        async def all_pages(self, fetch):
            return await fetch(1)

        async def investments(self, company, page):
            if company == "root":
                raise IncompletePagination("missing page", [Investment("child", "child", 100, {})])
            return [Investment("leaf", "leaf", 100, {})]

    repo = TraversalRepo()
    errors = await collector._collect_one(repo, Provider(), collector.RunSpec(uuid4(), "root", 2, 100, ["invest"]))
    assert "missing page" in errors[0]
    assert "leaf" in repo.entities


@pytest.mark.asyncio
async def test_icp_final_drain_rechecks_snapshot_after_last_producer_commit(monkeypatch):
    done = asyncio.Event()
    fed = []

    class Repo:
        calls = 0

        async def entity_names_since(self, *args):
            self.calls += 1
            if self.calls == 1:
                done.set()
                return ["root"], 1, 1
            return ["last-child"], 2, 2

        async def touch_run(self, *args, **kwargs):
            pass

    async def collect(repo, run_id, queue):
        while (name := await queue.get()) is not None:
            fed.append(name)
        return []

    monkeypatch.setattr(collector, "collect_icp_from_queue", collect)
    await collector._collect_icp_as_entities_are_discovered(
        Repo(), collector.RunSpec(uuid4(), "root", 2, 100, ["invest"]), done, asyncio.Event()
    )
    assert fed == ["root", "last-child"]


@pytest.mark.asyncio
async def test_icp_consumer_drains_names_after_producers_finish(monkeypatch):
    run_id = uuid4()
    root = "中国烟草总公司湖北省公司"
    child = "湖北省烟草公司天门市公司"
    batches = []

    class Repo:
        calls = 0

        async def entity_names_for_run(self, _run_id):
            self.calls += 1
            await __import__("asyncio").sleep(0)
            return [root] if self.calls == 1 else [root, child]

        async def touch_run(self, *_args, **_kwargs):
            return None

    repo = Repo()
    producers_done = __import__("asyncio").Event()
    entity_changed = __import__("asyncio").Event()

    async def fake_collect(_repo, _run_id, names):
        collected = []
        while True:
            item = await names.get()
            if item is None:
                break
            collected.append(item)
            if len(batches) == 0 and len(collected) == 1:
                __import__("asyncio").get_running_loop().call_soon(producers_done.set)
        batches.append(collected)
        return []

    monkeypatch.setattr(collector, "collect_icp_from_queue", fake_collect)
    monkeypatch.setattr(collector, "ICP_STREAM_MIN_START", 1)
    spec = collector.RunSpec(
        id=run_id,
        keyword=root,
        depth=3,
        holding_percent=51,
        fields=["invest"],
    )

    errors = await collector._collect_icp_as_entities_are_discovered(
        repo, spec, producers_done, entity_changed
    )

    assert errors == []
    assert batches == [[root, child]]


@pytest.mark.asyncio
async def test_icp_consumer_feeds_later_names_into_one_collector(monkeypatch):
    run_id = uuid4()
    names = ["根企业"] + [f"子企业{i}" for i in range(8)]
    seen_queues = []
    fed = []

    class Repo:
        calls = 0

        async def entity_names_for_run(self, _run_id):
            self.calls += 1
            await __import__("asyncio").sleep(0)
            if self.calls == 1:
                return names[:8]
            return names

        async def touch_run(self, *_args, **_kwargs):
            return None

    producers_done = __import__("asyncio").Event()
    entity_changed = __import__("asyncio").Event()

    async def fake_collect(_repo, _run_id, queue):
        seen_queues.append(queue)
        while True:
            item = await queue.get()
            if item is None:
                break
            fed.append(item)
            if item == names[7]:
                producers_done.set()
        return []

    monkeypatch.setattr(collector, "collect_icp_from_queue", fake_collect)
    monkeypatch.setattr(collector, "ICP_STREAM_MIN_START", 8)
    spec = collector.RunSpec(
        id=run_id,
        keyword=names[0],
        depth=3,
        holding_percent=51,
        fields=["invest"],
    )
    errors = await collector._collect_icp_as_entities_are_discovered(
        Repo(), spec, producers_done, entity_changed
    )
    assert errors == []
    assert len(seen_queues) == 1
    assert fed == names


@pytest.mark.asyncio
async def test_icp_consumer_uses_incremental_name_cursor(monkeypatch):
    run_id = uuid4()
    calls = []
    fed = []

    class Repo:
        async def entity_names_since(self, _run_id, rel_cursor, res_cursor):
            calls.append((rel_cursor, res_cursor))
            await __import__("asyncio").sleep(0)
            if rel_cursor == 0 and res_cursor == 0:
                return ["根企业", "子企业1"], 3, 1
            return ["子企业2"], 5, 2

        async def entity_names_for_run(self, _run_id):
            assert producers_done.is_set(), "running discovery must use incremental cursors"
            return ["根企业", "子企业1", "子企业2"]

        async def touch_run(self, *_args, **_kwargs):
            return None

    producers_done = __import__("asyncio").Event()
    entity_changed = __import__("asyncio").Event()

    async def fake_collect(_repo, _run_id, queue):
        while True:
            item = await queue.get()
            if item is None:
                break
            fed.append(item)
            if item == "子企业1":
                producers_done.set()
        return []

    monkeypatch.setattr(collector, "collect_icp_from_queue", fake_collect)
    monkeypatch.setattr(collector, "ICP_STREAM_MIN_START", 1)
    errors = await collector._collect_icp_as_entities_are_discovered(
        Repo(),
        collector.RunSpec(id=run_id, keyword="根企业", depth=3, holding_percent=51, fields=["invest"]),
        producers_done,
        entity_changed,
    )
    assert errors == []
    assert "根企业" in fed
    assert "子企业1" in fed
    assert "子企业2" in fed
    assert calls[0] == (0, 0)
