import asyncio
from uuid import uuid4

import pytest

from app import worker


@pytest.mark.asyncio
@pytest.mark.parametrize("warnings, expected", [
    (["source timeout"], "succeeded"),
    (["[结果不完整] more pages remain"], "partial"),
])
async def test_worker_distinguishes_optional_source_failure_from_confirmed_gaps(monkeypatch, warnings, expected):
    class Repo:
        claimed = False
        finished = []

        async def claim_subdomain_run(self):
            if self.claimed:
                raise asyncio.CancelledError()
            self.claimed = True
            return {"id": uuid4(), "lease_id": uuid4(), "domains": ["example.com"], "options": {}}

        async def subdomain_result_count(self, run_id):
            return 10

        async def finish_subdomain_run(self, run_id, status, *args, **kwargs):
            self.finished.append(status)

    async def collect(*args, **kwargs):
        return warnings

    monkeypatch.setattr(worker, "collect_subdomains", collect)
    repo = Repo()
    with pytest.raises(asyncio.CancelledError):
        await worker.subdomain_worker_loop(repo)
    assert repo.finished == [expected]
