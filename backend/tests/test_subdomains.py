from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

import app.subdomains as subdomains
from app.subdomains import HttpProbe, ResolvedHost


def test_normalize_domains_accepts_urls_idn_and_deduplicates():
    assert subdomains.normalize_domains([
        "https://www.Example.com/path",
        "example.com",
        "https://www.例子.公司.cn/",
    ]) == ["example.com", "xn--fsqu00a.xn--55qx5d.cn"]


def test_normalize_domains_rejects_invalid_input():
    with pytest.raises(ValueError, match="无效域名"):
        subdomains.normalize_domains(["not a domain"])
    with pytest.raises(ValueError, match="无效域名"):
        subdomains.normalize_domains(["85.196"])


@pytest.mark.asyncio
async def test_passive_sources_parse_only_children_of_root():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "crt.sh":
            return httpx.Response(200, json=[{
                "name_value": "*.api.example.com\nwww.example.com\nother.test",
                "common_name": "mail.example.com",
            }])
        if request.url.host == "api.certspotter.com":
            return httpx.Response(200, json=[{"dns_names": ["cdn.example.com", "*.dev.example.com"]}])
        return httpx.Response(200, text="vpn.example.com,203.0.113.4\ninvalid.test,203.0.113.5")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await subdomains.collect_crtsh(client, "example.com") == {
            "api.example.com", "www.example.com", "mail.example.com"
        }
        assert await subdomains.collect_certspotter(client, "example.com") == {
            "cdn.example.com", "dev.example.com"
        }
        assert await subdomains.collect_hackertarget(client, "example.com") == {"vpn.example.com"}


@pytest.mark.asyncio
async def test_additional_passive_sources_parse_and_filter_results():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "urlscan.io":
            return httpx.Response(200, json={"results": [
                {"page": {"domain": "portal.example.com", "url": "https://api.example.com/x"}},
                {"task": {"domain": "unrelated.test"}},
            ]})
        if request.url.host == "rapiddns.io":
            return httpx.Response(200, text="<td>vpn.example.com</td><td>other.test</td>")
        return httpx.Response(200, json={"Answer": [
            {"data": "10 mail.example.com."}, {"data": "outside.test."}
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await subdomains.collect_urlscan(client, "example.com") == {
            "portal.example.com", "api.example.com"
        }
        assert await subdomains.collect_rapiddns(client, "example.com") == {"vpn.example.com"}
        assert await subdomains.collect_dns_records(client, "example.com") == {"mail.example.com"}


class FakeRepo:
    def __init__(self):
        self.progress = []
        self.results = []
        self.result_event = __import__("asyncio").Event()
        self.cache = {}

    async def update_subdomain_progress(self, *args, **kwargs):
        self.progress.append((args, kwargs))

    async def get_subdomain_source_cache(self, root, source):
        return self.cache.get((root, source))

    async def set_subdomain_source_cache(self, root, source, hosts):
        self.cache[(root, source)] = list(hosts)

    async def add_subdomain_result(self, run_id, **values):
        existing = next((item for item in self.results if item[1]["hostname"] == values["hostname"]), None)
        if existing:
            existing[1].update({key: value for key, value in values.items() if value not in ("", None, [])})
            existing[1]["sources"] = sorted(set(existing[1]["sources"]) | set(values["sources"]))
            return False
        self.results.append((run_id, values))
        self.result_event.set()
        return True


@pytest.mark.asyncio
async def test_collection_streams_resolved_results_and_filters_dictionary_wildcard(monkeypatch):
    async def crt(_client, _domain):
        return {"api.example.com", "passive.example.com"}

    async def cert(_client, _domain):
        return set()

    async def hacker(_client, _domain):
        return set()

    async def wildcard(_domain):
        return {"192.0.2.10"}

    async def resolve(hostname):
        if hostname in {"api.example.com", "passive.example.com"}:
            return ResolvedHost(hostname, ["93.184.216.34"])
        return ResolvedHost(hostname, ["192.0.2.10"])

    async def probe(_client, resolved, _root=None):
        return HttpProbe(f"https://{resolved.hostname}/", 200, "Example")

    monkeypatch.setattr(subdomains, "COMMON_PREFIXES", ("www", "api"))
    monkeypatch.setattr(subdomains, "PASSIVE_SOURCES", (
        ("crt.sh", "collect_crtsh"),
        ("CertSpotter", "collect_certspotter"),
        ("HackerTarget", "collect_hackertarget"),
    ))
    monkeypatch.setattr(subdomains, "collect_crtsh", crt)
    monkeypatch.setattr(subdomains, "collect_certspotter", cert)
    monkeypatch.setattr(subdomains, "collect_hackertarget", hacker)
    monkeypatch.setattr(subdomains, "_wildcard_ips", wildcard)
    monkeypatch.setattr(subdomains, "resolve_hostname", resolve)
    monkeypatch.setattr(subdomains, "probe_http", probe)
    monkeypatch.setattr(subdomains, "generate_altdns_candidates", lambda *_args: set())

    repo = FakeRepo()
    warnings = await subdomains.collect_subdomains(
        repo,
        uuid4(),
        ["example.com"],
        {"passive": True, "brute_force": True, "http_probe": True},
        lease_id=uuid4(),
    )

    assert warnings == []
    names = {item[1]["hostname"] for item in repo.results}
    assert names == {"api.example.com", "passive.example.com"}
    assert all(item[1]["http_status"] == 200 for item in repo.results)
    assert repo.results[0][1]["sources"] == ["DNS字典", "crt.sh"]
    assert repo.progress[-1][0][4] == "completed"


@pytest.mark.asyncio
async def test_passive_source_failure_is_warning_not_fatal(monkeypatch):
    async def broken(_client, _domain):
        raise httpx.ConnectError("offline")

    async def empty(_client, _domain):
        return set()

    async def unresolved(_hostname):
        return None

    monkeypatch.setattr(subdomains, "PASSIVE_SOURCES", (
        ("crt.sh", "collect_crtsh"),
        ("CertSpotter", "collect_certspotter"),
        ("HackerTarget", "collect_hackertarget"),
    ))
    monkeypatch.setattr(subdomains, "collect_crtsh", broken)
    monkeypatch.setattr(subdomains, "collect_certspotter", empty)
    monkeypatch.setattr(subdomains, "collect_hackertarget", empty)
    monkeypatch.setattr(subdomains, "resolve_hostname", unresolved)

    repo = FakeRepo()
    warnings = await subdomains.collect_subdomains(
        repo,
        uuid4(),
        ["example.com"],
        {"passive": True, "brute_force": False, "http_probe": False},
        lease_id=uuid4(),
    )

    assert len(warnings) == 1
    assert "crt.sh" in warnings[0]
    assert repo.results == []


@pytest.mark.asyncio
async def test_passive_source_cache_avoids_duplicate_external_request():
    repo = FakeRepo()
    repo.cache[("example.com", "cached")] = ["api.example.com"]
    called = False

    async def source(_client, _domain):
        nonlocal called
        called = True
        return set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
        name, hosts, error = await subdomains._call_source(
            repo, "cached", source, client, "example.com"
        )

    assert (name, hosts, error) == ("cached", {"api.example.com"}, "")
    assert called is False


@pytest.mark.asyncio
async def test_dictionary_results_arrive_before_slow_passive_source(monkeypatch):
    release = __import__("asyncio").Event()

    async def slow(_client, _domain):
        await release.wait()
        return {"api.example.com"}

    async def resolve(hostname):
        return ResolvedHost(hostname, ["93.184.216.34"])

    async def no_wildcard(_domain):
        return set()

    monkeypatch.setattr(subdomains, "COMMON_PREFIXES", ("www",))
    monkeypatch.setattr(subdomains, "PASSIVE_SOURCES", (("slow", "collect_crtsh"),))
    monkeypatch.setattr(subdomains, "collect_crtsh", slow)
    monkeypatch.setattr(subdomains, "resolve_hostname", resolve)
    monkeypatch.setattr(subdomains, "_wildcard_ips", no_wildcard)
    monkeypatch.setattr(subdomains, "generate_altdns_candidates", lambda *_args: set())

    repo = FakeRepo()
    task = __import__("asyncio").create_task(subdomains.collect_subdomains(
        repo, uuid4(), ["example.com"],
        {"passive": True, "brute_force": True, "http_probe": False},
        lease_id=uuid4(),
    ))
    await __import__("asyncio").wait_for(repo.result_event.wait(), 1)
    assert task.done() is False
    assert repo.results[0][1]["hostname"] == "www.example.com"
    release.set()
    assert await task == []
    assert {item[1]["hostname"] for item in repo.results} == {
        "www.example.com", "api.example.com"
    }


@pytest.mark.asyncio
async def test_http_probe_does_not_follow_redirect_to_private_address():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    resolved = ResolvedHost("safe.example.com", ["93.184.216.34"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        result = await subdomains.probe_http(client, resolved)

    assert result == HttpProbe()
    assert requests == ["https://safe.example.com/", "http://safe.example.com/"]


def test_subdomain_options_require_a_discovery_method():
    from pydantic import ValidationError
    from app.models import SubdomainOptions

    with pytest.raises(ValidationError, match="至少启用一项"):
        SubdomainOptions(passive=False, brute_force=False, http_probe=True)


@pytest.mark.asyncio
async def test_passive_source_retries_once_before_warning(monkeypatch):
    repo = FakeRepo()
    attempts = 0

    async def flaky(_client, _domain):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("slow")
        return {"api.example.com"}

    monkeypatch.setattr(subdomains, "PASSIVE_ATTEMPTS", 2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        name, hosts, error = await subdomains._call_source(
            repo, "flaky", flaky, client, "example.com"
        )

    assert (name, hosts, error) == ("flaky", {"api.example.com"}, "")
    assert attempts == 2
    assert repo.cache[("example.com", "flaky")] == ["api.example.com"]


@pytest.mark.asyncio
async def test_passive_only_result_is_kept_and_marked_as_wildcard(monkeypatch):
    async def passive(_client, _domain):
        return {"api.example.com"}

    async def wildcard(_domain):
        return {"203.0.113.10"}

    async def resolve(hostname):
        return ResolvedHost(hostname, ["203.0.113.10"])

    monkeypatch.setattr(subdomains, "PASSIVE_SOURCES", (("passive", "collect_crtsh"),))
    monkeypatch.setattr(subdomains, "collect_crtsh", passive)
    monkeypatch.setattr(subdomains, "_wildcard_ips", wildcard)
    monkeypatch.setattr(subdomains, "resolve_hostname", resolve)

    repo = FakeRepo()
    warnings = await subdomains.collect_subdomains(
        repo, uuid4(), ["example.com"],
        {"passive": True, "brute_force": False, "http_probe": False},
        lease_id=uuid4(),
    )

    assert warnings == []
    assert len(repo.results) == 1
    assert repo.results[0][1]["wildcard"] is True
    assert repo.results[0][1]["sources"] == ["passive"]


@pytest.mark.asyncio
async def test_certspotter_follows_bounded_next_pages():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        page = len(calls)
        headers = {}
        if page < 3:
            headers["link"] = f'<https://api.certspotter.com/v1/issuances?after={page}>; rel="next"'
        return httpx.Response(
            200,
            headers=headers,
            json=[{"dns_names": [f"page{page}.example.com"]}],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await subdomains.collect_certspotter(client, "example.com")

    assert result == {"page1.example.com", "page2.example.com", "page3.example.com"}
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_commoncrawl_parses_json_lines_and_filters_hosts():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "index.commoncrawl.org" and request.url.path.endswith("collinfo.json"):
            return httpx.Response(200, json=[{"cdx-api": "https://index.commoncrawl.org/CC-index"}])
        return httpx.Response(
            200,
            text='{"url":"https://api.example.com/path"}\n{"url":"https://outside.test/"}\nhttps://mail.example.com/\n',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await subdomains.collect_commoncrawl(client, "example.com")

    assert result == {"api.example.com", "mail.example.com"}


@pytest.mark.asyncio
async def test_commoncrawl_no_captures_is_not_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("collinfo.json"):
            return httpx.Response(200, json=[{"cdx-api": "https://index.commoncrawl.org/CC-index"}])
        return httpx.Response(404, json={"message": "No Captures found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await subdomains.collect_commoncrawl(client, "no-captures.example") == set()


def test_altdns_generates_bounded_observed_name_variants():
    old_limit = subdomains.ALTDNS_CANDIDATE_LIMIT
    subdomains.ALTDNS_CANDIDATE_LIMIT = 20
    try:
        result = subdomains.generate_altdns_candidates(
            {"api-01.example.com", "portal.example.com"}, "example.com"
        )
    finally:
        subdomains.ALTDNS_CANDIDATE_LIMIT = old_limit

    assert len(result) <= 20
    assert "api-02.example.com" in result or "api-00.example.com" in result
    assert all(host.endswith(".example.com") for host in result)


@pytest.mark.asyncio
async def test_http_probe_returns_subdomains_found_in_response_body():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text='<html><title>Home</title><script src="https://static.example.com/app.js"></script></html>',
        )

    resolved = ResolvedHost("www.example.com", ["93.184.216.34"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        result = await subdomains.probe_http(client, resolved, "example.com")

    assert result.title == "Home"
    assert result.discovered == ("static.example.com",)


@pytest.mark.asyncio
async def test_http_discovered_hosts_are_enqueued_for_dns_validation(monkeypatch):
    async def resolve(hostname):
        return ResolvedHost(hostname, ["93.184.216.34"])

    async def probe(_client, resolved, _root=None):
        if resolved.hostname == "www.example.com":
            return HttpProbe(
                f"https://{resolved.hostname}/", 200, "Home",
                discovered=("api.example.com",),
            )
        return HttpProbe(f"https://{resolved.hostname}/", 200, "API")

    monkeypatch.setattr(subdomains, "COMMON_PREFIXES", ("www",))
    monkeypatch.setattr(subdomains, "PASSIVE_SOURCES", ())
    monkeypatch.setattr(subdomains, "resolve_hostname", resolve)
    monkeypatch.setattr(subdomains, "probe_http", probe)
    monkeypatch.setattr(subdomains, "_wildcard_ips", lambda _domain: __import__("asyncio").sleep(0, result=set()))

    repo = FakeRepo()
    await subdomains.collect_subdomains(
        repo, uuid4(), ["example.com"],
        {"passive": False, "brute_force": True, "deep_scan": False, "http_probe": True},
        lease_id=uuid4(),
    )

    assert {item[1]["hostname"] for item in repo.results} == {
        "www.example.com", "api.example.com"
    }


@pytest.mark.asyncio
async def test_rate_limited_source_is_not_retried_immediately(monkeypatch):
    calls = 0

    async def limited(_client, _domain):
        nonlocal calls
        calls += 1
        response = httpx.Response(429, headers={"retry-after": "1"})
        request = httpx.Request("GET", "https://source.invalid")
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    monkeypatch.setattr(subdomains, "SOURCE_COOLDOWN_SECONDS", 60.0)
    # Use a fresh throttle so this test is independent from other tests.
    throttle = subdomains._SourceThrottle()
    monkeypatch.setattr(subdomains, "SOURCE_THROTTLE", throttle)
    async with httpx.AsyncClient() as client:
        first = await subdomains._call_source(FakeRepo(), "rate-source", limited, client, "example.com")
        second = await subdomains._call_source(FakeRepo(), "rate-source", limited, client, "other.example.com")

    assert calls == 1
    assert first[1] == set()
    assert "429" in first[2]
    assert "暂停" in second[2]
