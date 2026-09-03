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
        batches.append(names)
        if len(batches) == 1:
            __import__("asyncio").get_running_loop().call_soon(producers_done.set)
        return []

    monkeypatch.setattr(collector, "collect_icp", fake_collect)
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
    assert batches == [[root], [child]]
