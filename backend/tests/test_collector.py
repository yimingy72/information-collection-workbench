from uuid import uuid4

import pytest

from app import collector


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
