from __future__ import annotations

import asyncio
import base64
import html
import json
import ipaddress
import random
import re
import socket
import ssl
import string
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx
import tldextract

from app.concurrency import gather_cancel_on_error, shared_semaphore, supervise
from app.repository import LeaseLost, Repository

PASSIVE_TIMEOUT = 20.0
PASSIVE_ATTEMPTS = 3
SOURCE_COOLDOWN_SECONDS = 60.0
SOURCE_COOLDOWN_WAIT_SECONDS = 8.0
PROGRESS_INTERVAL_SECONDS = 0.4
PROGRESS_BATCH_SIZE = 25
PERSIST_BATCH_SIZE = 80
DNS_CONCURRENCY = 64
HTTP_CONCURRENCY = 20
# Run a few root domains concurrently. DNS/HTTP semaphores below still cap
# total outbound work, while preventing one large root from blocking all other
# ICP-derived domains for the full duration of its scan.
ROOT_CONCURRENCY = 5
HTTP_PROBE_TIMEOUT = 5.0
DNS_TIMEOUT = 2.5
DNS_ATTEMPTS = 2
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=DNS_CONCURRENCY, thread_name_prefix="subdomain-dns")
ALTDNS_CANDIDATE_LIMIT = 400
COMMON_CRAWL_RESULT_LIMIT = 1_000
WAYBACK_RESULT_LIMIT = 2_000
FOFA_MAX_PAGES = 3
FOFA_PAGE_SIZE = 1000
HUNTER_MAX_PAGES = 5
HUNTER_PAGE_SIZE = 100
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
HOST_REFERENCE_RE = re.compile(
    r"(?<![a-z0-9-])(?:https?://|//)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})(?![a-z0-9-])",
    re.I,
)
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$",
    re.I,
)

# A compact, auditable default dictionary. The implementation borrows the
# OneForAll workflow (parallel passive discovery, DNS validation, wildcard
# filtering and HTTP enrichment) without copying its GPL-licensed source.
COMMON_PREFIXES = tuple(dict.fromkeys("""
www api m mail smtp pop imap webmail mx ns ns1 ns2 dns dns1 dns2 ftp sftp ssh
admin portal console dashboard manage manager account auth login oauth sso id
app apps mobile wap h5 static assets asset img images image cdn media file files
download downloads upload uploads video live stream status health monitor metrics
dev test testing qa uat stage staging pre prod production beta alpha demo sandbox
open docs doc help support service services gateway proxy vpn git gitlab github
jenkins ci cd build registry repo package packages npm pypi maven wiki blog news
shop store pay payment billing order orders user users member members customer
crm erp oa office work wechat wx mp mini data db database mysql redis elastic search
cloud intranet extranet internal external public private secure origin edge cache
www1 www2 api1 api2 v1 v2 old new backup bak temp tmp
""".split()))

# Use tldextract's bundled public-suffix snapshot without creating a cache file.
# The workbench does not need runtime PSL downloads, and avoiding a home-directory
# cache keeps tests and read-only container deployments deterministic.
_TLD_EXTRACT = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())


class SubdomainError(RuntimeError):
    pass


class IncompleteSource(SubdomainError):
    def __init__(self, hosts: set[str], reason: str) -> None:
        super().__init__(f"[结果不完整] {reason}")
        self.hosts = hosts


class DNSLookupError(SubdomainError):
    pass


SOURCE_CACHE_VERSION = "v2"


@dataclass(frozen=True)
class ResolvedHost:
    hostname: str
    ips: list[str]
    canonical_name: str = ""


@dataclass(frozen=True)
class HttpProbe:
    url: str = ""
    status: int | None = None
    title: str = ""
    discovered: tuple[str, ...] = ()


def normalize_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("域名不能为空")
    if "://" not in raw:
        raw = f"//{raw}"
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").strip(".*.")
    if not host:
        raise ValueError(f"无效域名：{value}")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"无效域名：{value}") from exc
    if not DOMAIN_RE.fullmatch(ascii_host) or ascii_host.rsplit(".", 1)[-1].isdigit():
        raise ValueError(f"无效域名：{value}")
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        pass
    else:
        raise ValueError(f"无效域名：{value}")
    return ascii_host


def registrable_domain(value: str) -> str:
    host = normalize_domain(value)
    extracted = _TLD_EXTRACT(host)
    root = extracted.top_domain_under_public_suffix
    if not root:
        raise ValueError(f"无效域名：{value}")
    return root


def normalize_domains(values: list[str]) -> list[str]:
    domains: list[str] = []
    errors: list[str] = []
    for raw in values:
        try:
            domain = registrable_domain(raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if domain not in domains:
            domains.append(domain)
    if errors:
        raise ValueError("；".join(errors[:5]))
    if not domains:
        raise ValueError("请至少输入一个有效域名")
    return domains


def _candidate(value: str, root: str) -> str | None:
    raw = str(value or "").strip().lower().replace("\x00", "")
    if not raw:
        return None
    if "://" in raw:
        raw = urlsplit(raw).hostname or ""
    elif raw.count(":") == 1:
        host, port = raw.rsplit(":", 1)
        if port.isdigit():
            raw = host
    raw = raw.strip(".*.")
    try:
        host = raw.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if host == root or not host.endswith(f".{root}") or not DOMAIN_RE.fullmatch(host):
        return None
    return host


async def collect_crtsh(client: httpx.AsyncClient, domain: str) -> set[str]:
    response = await client.get("https://crt.sh/", params={"q": f"%.{domain}", "output": "json"})
    response.raise_for_status()
    data = response.json()
    found: set[str] = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        for field in (item.get("name_value"), item.get("common_name")):
            for value in str(field or "").splitlines():
                host = _candidate(value, domain)
                if host:
                    found.add(host)
    return found


def _next_link(response: httpx.Response) -> str | None:
    for value in response.headers.get_list("link"):
        for part in value.split(","):
            match = re.search(r"<([^>]+)>\s*;\s*rel=\"?next\"?", part, re.I)
            if match:
                return match.group(1)
    return None


async def collect_certspotter(client: httpx.AsyncClient, domain: str) -> set[str]:
    next_url = "https://api.certspotter.com/v1/issuances"
    params: dict[str, str] | None = {
        "domain": domain,
        "include_subdomains": "true",
        "expand": "dns_names",
    }
    found: set[str] = set()
    # CertSpotter paginates larger certificate sets through Link headers. Keep
    # the page bound finite so completeness improvements cannot turn into an
    # unbounded external request loop.
    for page in range(3):
        response = await client.get(next_url, params=params)
        response.raise_for_status()
        data = response.json()
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            for value in item.get("dns_names") or []:
                host = _candidate(str(value), domain)
                if host:
                    found.add(host)
        next_link = _next_link(response)
        # CertSpotter currently returns a relative Link header (for example
        # ``</v1/issuances?...>; rel=next``). httpx cannot request that path
        # without a base URL, so resolve it against the response URL before the
        # next page. This also supports absolute links from older responses.
        next_url = urljoin(str(response.url), next_link) if next_link else None
        params = None
        if not next_url:
            break
        delay = SOURCE_MIN_INTERVALS.get("CertSpotter", 0.35)
        if page < 2 and delay:
            await asyncio.sleep(delay)
    if next_url:
        raise IncompleteSource(found, "CertSpotter 仍有下一页，已达到本轮来源请求额度")
    return found


async def collect_hackertarget(client: httpx.AsyncClient, domain: str) -> set[str]:
    response = await client.get("https://api.hackertarget.com/hostsearch/", params={"q": domain})
    response.raise_for_status()
    text = response.text.strip()
    if text.lower().startswith("error"):
        raise SubdomainError(text[:300])
    found: set[str] = set()
    for line in text.splitlines():
        host = _candidate(line.split(",", 1)[0], domain)
        if host:
            found.add(host)
    return found


async def collect_urlscan(client: httpx.AsyncClient, domain: str) -> set[str]:
    response = await client.get(
        "https://urlscan.io/api/v1/search/",
        params={"q": f"domain:{domain}", "size": "100"},
    )
    response.raise_for_status()
    data = response.json()
    found: set[str] = set()
    for item in data.get("results", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        for section_name in ("page", "task"):
            section = item.get(section_name)
            if not isinstance(section, dict):
                continue
            for key in ("domain", "url"):
                host = _candidate(str(section.get(key) or ""), domain)
                if host:
                    found.add(host)
    if isinstance(data, dict) and (data.get("has_more") or int(data.get("total") or 0) > len(data.get("results") or [])):
        raise IncompleteSource(found, "urlscan.io 仍有未读取结果")
    return found


class _RapidDNSParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_cell = False
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() == "td":
            self.in_cell = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "td":
            self.in_cell = False

    def handle_data(self, data: str) -> None:
        if self.in_cell and data.strip():
            self.values.append(data.strip())


async def collect_rapiddns(client: httpx.AsyncClient, domain: str) -> set[str]:
    response = await client.get(
        f"https://rapiddns.io/subdomain/{domain}", params={"full": "1"}
    )
    response.raise_for_status()
    parser = _RapidDNSParser()
    parser.feed(response.text)
    found: set[str] = set()
    for value in parser.values:
        host = _candidate(value, domain)
        if host:
            found.add(host)
    return found


async def _collect_dns_record_type(
    client: httpx.AsyncClient, domain: str, record_type: str, root_domain: str | None = None,
) -> set[str]:
    response = await client.get(
        "https://dns.alidns.com/resolve",
        params={"name": domain, "type": record_type},
        headers={"Accept": "application/dns-json"},
    )
    response.raise_for_status()
    data = response.json()
    found: set[str] = set()
    for answer in data.get("Answer", []) if isinstance(data, dict) else []:
        if not isinstance(answer, dict):
            continue
        for value in re.findall(r"[a-z0-9_.-]+", str(answer.get("data") or ""), re.I):
            host = _candidate(value, root_domain or domain)
            if host:
                found.add(host)
    return found


async def _best_effort_union(
    awaitables: list[Awaitable[set[str]]],
) -> set[str]:
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    successful = [result for result in results if isinstance(result, set)]
    if successful:
        return set().union(*successful)
    errors = [result for result in results if isinstance(result, Exception)]
    if errors:
        raise errors[0]
    return set()


async def collect_dns_records(client: httpx.AsyncClient, domain: str) -> set[str]:
    return await _best_effort_union([
        _collect_dns_record_type(client, domain, record_type)
        for record_type in ("MX", "NS", "SOA", "TXT", "SRV")
    ])


SRV_PREFIXES = tuple("""
_sip._tcp _sip._udp _sips._tcp _xmpp-client._tcp _xmpp-server._tcp
_ldap._tcp _kerberos._tcp _kerberos._udp _kpasswd._tcp _kpasswd._udp
_gc._tcp _caldav._tcp _carddav._tcp _autodiscover._tcp _submission._tcp
_imap._tcp _imaps._tcp _pop3._tcp _pop3s._tcp _http._tcp _https._tcp
""".split())


async def collect_srv_records(client: httpx.AsyncClient, domain: str) -> set[str]:
    return await _best_effort_union([
        _collect_dns_record_type(client, f"{prefix}.{domain}", "SRV", domain)
        for prefix in SRV_PREFIXES
    ])


def _hosts_from_text(text: str, domain: str) -> set[str]:
    found: set[str] = set()
    for match in HOST_REFERENCE_RE.finditer(text):
        host = _candidate(match.group(1), domain)
        if host:
            found.add(host)
    return found


async def collect_web_metadata(client: httpx.AsyncClient, domain: str) -> set[str]:
    resolved = await resolve_hostname(domain)
    if not resolved or not _public_ips(resolved.ips):
        return set()

    async def fetch_path(path: str) -> set[str]:
        for scheme in ("https", "http"):
            try:
                response = await client.get(f"{scheme}://{domain}/{path}")
                if response.status_code < 500:
                    return _hosts_from_text(response.text, domain) | _hosts_from_text(
                        response.headers.get("location", ""), domain
                    )
            except (httpx.HTTPError, ssl.SSLError, OSError):
                continue
        return set()

    results = await asyncio.gather(*(fetch_path(path) for path in (
        "robots.txt", "sitemap.xml", "crossdomain.xml"
    )))
    return set().union(*results)


async def collect_commoncrawl(client: httpx.AsyncClient, domain: str) -> set[str]:
    indexes = await client.get("https://index.commoncrawl.org/collinfo.json")
    indexes.raise_for_status()
    data = indexes.json()
    latest = data[0].get("cdx-api") if isinstance(data, list) and data else None
    if not latest:
        return set()
    response = await client.get(
        latest,
        params={
            "url": f"{domain}/*",
            "matchType": "domain",
            "output": "json",
            "fl": "url",
            "filter": "status:200",
            "collapse": "urlkey",
            "limit": str(COMMON_CRAWL_RESULT_LIMIT),
        },
    )
    # A 404 from the index means that this domain has no captures in the
    # selected crawl, not that the whole subdomain run failed.
    if response.status_code == 404:
        return set()
    response.raise_for_status()
    found: set[str] = set()
    for line in response.text.splitlines():
        try:
            item = json.loads(line)
            value = item.get("url", "") if isinstance(item, dict) else line
        except json.JSONDecodeError:
            value = line
        found.update(_hosts_from_text(str(value), domain))
    if len(response.text.splitlines()) >= COMMON_CRAWL_RESULT_LIMIT:
        raise IncompleteSource(found, "Common Crawl 返回达到本轮来源请求额度")
    return found



def _json_items(data: object) -> list[object]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "passive_dns", "results", "subdomains", "records"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _hosts_from_payload(data: object, domain: str) -> set[str]:
    found: set[str] = set()
    if isinstance(data, str):
        found.update(_hosts_from_text(data, domain))
        return found
    if isinstance(data, list):
        for item in data:
            found.update(_hosts_from_payload(item, domain))
        return found
    if isinstance(data, dict):
        for key in ("hostname", "host", "domain", "rrname", "name", "value", "url"):
            found.update(_hosts_from_payload(data.get(key), domain))
        for value in data.values():
            if isinstance(value, (list, dict, str)):
                found.update(_hosts_from_payload(value, domain))
        return found
    return found


async def collect_anubis(client: httpx.AsyncClient, domain: str) -> set[str]:
    found: set[str] = set()
    errors: list[Exception] = []
    for url in (
        f"https://jonlu.ca/anubis/subdomains/{domain}",
        f"https://jldc.me/anubis/subdomains/{domain}",
    ):
        try:
            async with asyncio.timeout(8.0):
                response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                for value in data:
                    host = _candidate(str(value), domain)
                    if host:
                        found.add(host)
            else:
                found.update(_hosts_from_payload(data, domain))
            if found:
                return found
        except Exception as exc:  # noqa: BLE001 - try the next public endpoint
            errors.append(exc)
            continue
    if errors:
        raise errors[-1]
    return found


async def collect_alienvault(client: httpx.AsyncClient, domain: str) -> set[str]:
    found: set[str] = set()
    for page in range(1, 4):
        response = await client.get(
            f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns",
            params={"limit": "100", "page": str(page)},
        )
        response.raise_for_status()
        data = response.json()
        records = data.get("passive_dns") if isinstance(data, dict) else None
        if not isinstance(records, list) or not records:
            break
        page_found = 0
        for item in records:
            if not isinstance(item, dict):
                continue
            host = _candidate(str(item.get("hostname") or item.get("domain") or ""), domain)
            if host:
                found.add(host)
                page_found += 1
        if len(records) < 100:
            break
        if page < 3:
            await asyncio.sleep(0.2)
    else:
        raise IncompleteSource(found, "AlienVault 返回达到本轮来源请求额度")
    return found


async def collect_wayback(client: httpx.AsyncClient, domain: str) -> set[str]:
    response = await client.get(
        "https://web.archive.org/cdx/search/cdx",
        params={
            "url": f"*.{domain}/*",
            "output": "json",
            "fl": "original",
            "collapse": "urlkey",
            "limit": str(WAYBACK_RESULT_LIMIT),
        },
    )
    response.raise_for_status()
    data = response.json()
    found: set[str] = set()
    for item in data if isinstance(data, list) else []:
        value = item[0] if isinstance(item, list) and item else item
        found.update(_hosts_from_text(str(value), domain))
    if isinstance(data, list) and len(data) >= WAYBACK_RESULT_LIMIT:
        raise IncompleteSource(found, "Wayback 返回达到本轮来源请求额度")
    return found


async def collect_threatminer(client: httpx.AsyncClient, domain: str) -> set[str]:
    response = await client.get(
        "https://api.threatminer.org/v2/domain.php",
        params={"q": domain, "rt": "5"},
    )
    response.raise_for_status()
    data = response.json()
    found: set[str] = set()
    for value in _json_items(data):
        host = _candidate(str(value), domain)
        if host:
            found.add(host)
    return found


async def collect_fofa(
    client: httpx.AsyncClient,
    domain: str,
    *,
    email: str,
    key: str,
) -> set[str]:
    email = email.strip()
    key = key.strip()
    if not email or not key:
        raise SubdomainError("未配置 FOFA API")
    query = base64.b64encode(f'domain="{domain}"'.encode("utf-8")).decode("ascii")
    found: set[str] = set()
    fetched = 0
    for page in range(1, FOFA_MAX_PAGES + 1):
        response = await client.get(
            "https://fofa.info/api/v1/search/all",
            params={
                "email": email,
                "key": key,
                "qbase64": query,
                "page": str(page),
                "full": "true",
                "size": str(FOFA_PAGE_SIZE),
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise SubdomainError("FOFA 返回格式异常")
        if data.get("error"):
            raise SubdomainError(str(data.get("errmsg") or "FOFA 查询失败")[:300])
        results = data.get("results")
        page_hosts: set[str] = set()
        for item in results if isinstance(results, list) else []:
            page_hosts.update(_hosts_from_payload(item, domain))
        found.update(page_hosts)
        fetched += len(results) if isinstance(results, list) else 0
        size = int(data.get("size") or 0)
        if size and fetched >= size:
            break
        if not results or len(results) < FOFA_PAGE_SIZE:
            if size > fetched:
                raise IncompleteSource(found, f"FOFA 上游报告 {size} 条，仅返回 {fetched} 条")
            break
        if page < FOFA_MAX_PAGES:
            await asyncio.sleep(1.0)
    else:
        raise IncompleteSource(found, "FOFA 仍有未读取结果，已达到本轮来源请求额度")
    return found


async def collect_hunter(
    client: httpx.AsyncClient,
    domain: str,
    *,
    key: str,
) -> set[str]:
    key = key.strip()
    if not key:
        raise SubdomainError("未配置 Hunter API")
    query = base64.b64encode(f'domain_suffix="{domain}"'.encode("utf-8")).decode("ascii")
    found: set[str] = set()
    fetched = 0
    for page in range(1, HUNTER_MAX_PAGES + 1):
        response = await client.get(
            "https://hunter.qianxin.com/openApi/search",
            params={
                "api-key": key,
                "search": query,
                "page": str(page),
                "page_size": str(HUNTER_PAGE_SIZE),
                "is_web": "1",
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise SubdomainError("Hunter 返回格式异常")
        code = int(data.get("code") or 0)
        if code != 200:
            raise SubdomainError(str(data.get("message") or f"Hunter 查询失败（{code}）")[:300])
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        records = payload.get("arr") if isinstance(payload, dict) else None
        page_hosts: set[str] = set()
        for item in records if isinstance(records, list) else []:
            page_hosts.update(_hosts_from_payload(item, domain))
        found.update(page_hosts)
        fetched += len(records) if isinstance(records, list) else 0
        total = int((payload or {}).get("total") or 0)
        if total and fetched >= total:
            break
        if not records or len(records) < HUNTER_PAGE_SIZE:
            if total > fetched:
                raise IncompleteSource(found, f"Hunter 上游报告 {total} 条，仅返回 {fetched} 条")
            break
        if page < HUNTER_MAX_PAGES:
            await asyncio.sleep(1.0)
    else:
        raise IncompleteSource(found, "Hunter 仍有未读取结果，已达到本轮来源请求额度")
    return found


PASSIVE_SOURCES = (
    ("crt.sh", "collect_crtsh"),
    ("CertSpotter", "collect_certspotter"),
    ("HackerTarget", "collect_hackertarget"),
    ("urlscan.io", "collect_urlscan"),
    ("RapidDNS", "collect_rapiddns"),
    ("Anubis", "collect_anubis"),
    ("AlienVault", "collect_alienvault"),
    ("Wayback", "collect_wayback"),
    ("ThreatMiner", "collect_threatminer"),
    ("DNS记录", "collect_dns_records"),
    ("SRV记录", "collect_srv_records"),
    ("站点元数据", "collect_web_metadata"),
    ("Common Crawl", "collect_commoncrawl"),
)

SOURCE_MIN_INTERVALS = {
    "crt.sh": 0.8,
    "CertSpotter": 3.0,
    "HackerTarget": 1.5,
    "urlscan.io": 1.2,
    "RapidDNS": 0.8,
    "Anubis": 1.0,
    "AlienVault": 1.2,
    "Wayback": 1.2,
    "ThreatMiner": 1.2,
    "DNS记录": 0.15,
    "SRV记录": 0.15,
    "站点元数据": 0.5,
    "Common Crawl": 1.2,
    "FOFA": 1.0,
    "Hunter": 1.0,
}


class _SourceThrottle:
    """Serialize one public source across every root domain in the process.

    Five ICP-derived roots used to fire CertSpotter at the same instant and
    immediately 429. A per-source lock plus a shared cooldown keeps the
    public APIs paced without blocking unrelated sources.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next_at: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Lock] = {}

    def _source_lock(self, name: str) -> asyncio.Lock:
        lock = self._inflight.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._inflight[name] = lock
        return lock

    async def acquire(self, name: str) -> str | None:
        source_lock = self._source_lock(name)
        await source_lock.acquire()
        wait = 0.0
        try:
            async with self._lock:
                now = time.monotonic()
                cooldown_until = self._cooldown_until.get(name, 0.0)
                if cooldown_until > now:
                    remaining = cooldown_until - now
                    if remaining > SOURCE_COOLDOWN_WAIT_SECONDS:
                        return f"请求频率受限，已暂停约 {max(1, int(remaining))} 秒"
                    wait = remaining
                else:
                    wait = max(0.0, self._next_at.get(name, 0.0) - now)
                self._next_at[name] = (
                    max(now, self._next_at.get(name, 0.0), cooldown_until)
                    + SOURCE_MIN_INTERVALS.get(name, 0.25)
                )
        except BaseException:
            source_lock.release()
            raise
        if wait:
            try:
                await asyncio.sleep(wait)
            except BaseException:
                source_lock.release()
                raise
        return None

    async def release(self, name: str) -> None:
        lock = self._inflight.get(name)
        if lock is not None and lock.locked():
            try:
                lock.release()
            except RuntimeError:
                return

    async def reserve(self, name: str) -> str | None:
        skipped = await self.acquire(name)
        await self.release(name)
        return skipped

    async def cool_down(self, name: str, seconds: float) -> None:
        async with self._lock:
            self._cooldown_until[name] = max(
                self._cooldown_until.get(name, 0.0), time.monotonic() + seconds
            )


SOURCE_THROTTLE = _SourceThrottle()


async def resolve_hostname(hostname: str) -> ResolvedHost | None:
    loop = asyncio.get_running_loop()

    def lookup(started: asyncio.Event) -> ResolvedHost | None:
        loop.call_soon_threadsafe(started.set)
        try:
            answers = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_CANONNAME
            )
        except socket.gaierror as exc:
            if exc.errno in {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}:
                return None
            raise
        ips = sorted({item[4][0] for item in answers if item[4]})
        if not ips:
            return None
        canonical = next((str(item[3]).rstrip(".") for item in answers if item[3]), "")
        return ResolvedHost(hostname=hostname, ips=ips, canonical_name=canonical)

    for attempt in range(DNS_ATTEMPTS):
        started = asyncio.Event()
        future = loop.run_in_executor(_DNS_EXECUTOR, lookup, started)
        try:
            # Queueing in a saturated executor is not a DNS response timeout.
            # Start the network budget only once getaddrinfo actually runs.
            await started.wait()
            return await asyncio.wait_for(asyncio.shield(future), timeout=DNS_TIMEOUT)
        except (TimeoutError, OSError) as exc:
            if attempt + 1 == DNS_ATTEMPTS:
                raise DNSLookupError(f"{hostname} DNS 超时或暂时失败（已尝试 {DNS_ATTEMPTS} 次）") from exc
        finally:
            if not future.done():
                future.cancel()
    raise AssertionError("unreachable")


def _public_ips(ips: list[str]) -> bool:
    if not ips:
        return False
    try:
        return all(ipaddress.ip_address(value).is_global for value in ips)
    except ValueError:
        return False


async def probe_http(
    client: httpx.AsyncClient,
    resolved: ResolvedHost,
    root_domain: str | None = None,
) -> HttpProbe:
    # Do not turn the workbench into an SSRF path to local/link-local services.
    if not _public_ips(resolved.ips):
        return HttpProbe()
    for scheme in ("https", "http"):
        current_url = f"{scheme}://{resolved.hostname}/"
        try:
            for redirect_count in range(4):
                parsed = urlsplit(current_url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    break
                checked = (
                    resolved
                    if parsed.hostname.casefold() == resolved.hostname.casefold()
                    else await resolve_hostname(parsed.hostname)
                )
                if not checked or not _public_ips(checked.ips):
                    break
                async with client.stream("GET", current_url) as response:
                    if response.is_redirect and redirect_count < 3:
                        location = response.headers.get("location", "")
                        if not location:
                            break
                        current_url = urljoin(str(response.url), location)
                        continue
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) >= 131_072:
                            break
                    text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
                    title = ""
                    content_type = response.headers.get("content-type", "").lower()
                    if "html" in content_type or not content_type:
                        match = TITLE_RE.search(text)
                        if match:
                            title = " ".join(html.unescape(match.group(1)).split())[:300]
                    discovered = tuple(sorted(
                        host for host in _hosts_from_text(text, root_domain or resolved.hostname)
                        if host != root_domain
                    )) if root_domain else ()
                    return HttpProbe(
                        url=str(response.url),
                        status=response.status_code,
                        title=title,
                        discovered=discovered,
                    )
        except (httpx.HTTPError, asyncio.TimeoutError, DNSLookupError):
            continue
    return HttpProbe()


async def _wildcard_ips(domain: str) -> set[str]:
    hostnames = [
        "of-" + "".join(random.choice(string.ascii_lowercase) for _ in range(18)) + f".{domain}"
        for _ in range(3)
    ]
    resolved_hosts = await gather_cancel_on_error(*(resolve_hostname(host) for host in hostnames))
    if any(resolved is None for resolved in resolved_hosts):
        return set()
    # Round-robin wildcard DNS may return a different subset for each random
    # name. If every impossible name resolves, treat the union as its fingerprint.
    return set().union(*(set(resolved.ips) for resolved in resolved_hosts if resolved))


def _source_error(exc: Exception) -> tuple[str, bool, float]:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            retry_after = exc.response.headers.get("retry-after", "").strip()
            delay = float(retry_after) if retry_after.isdigit() else SOURCE_COOLDOWN_SECONDS
            return "请求频率受限（HTTP 429），本轮暂停该来源", False, min(900.0, max(SOURCE_COOLDOWN_SECONDS, delay))
        if code in {401, 403}:
            return f"访问被拒绝（HTTP {code}）", False, 0.0
        if 500 <= code <= 599:
            return f"上游服务暂时不可用（HTTP {code}）", True, 0.0
        return f"上游返回 HTTP {code}", False, 0.0
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "请求超时", True, 0.0
    if isinstance(exc, (httpx.RequestError, ssl.SSLError)):
        return "网络连接失败", True, 0.0
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return "返回格式异常", False, 0.0
    detail = " ".join(str(exc).split())[:120] or type(exc).__name__
    if "SSL" in type(exc).__name__ or "ssl" in detail.lower():
        return "TLS 连接失败", True, 0.0
    return detail, True, 0.0


async def _call_source(
    repo: Repository,
    name: str,
    function: Callable[[httpx.AsyncClient, str], Awaitable[set[str]]],
    client: httpx.AsyncClient,
    domain: str,
    *,
    fallback_client: httpx.AsyncClient | None = None,
) -> tuple[str, set[str], str]:
    cache_key = f"{SOURCE_CACHE_VERSION}:{name}"
    cached = await repo.get_subdomain_source_cache(domain, cache_key)
    if cached is not None:
        return name, set(cached), ""
    errors: list[str] = []
    for attempt in range(1, PASSIVE_ATTEMPTS + 1):
        throttled = await SOURCE_THROTTLE.acquire(name)
        if throttled:
            await SOURCE_THROTTLE.release(name)
            return name, set(), throttled
        try:
            async with asyncio.timeout(PASSIVE_TIMEOUT):
                hosts = await function(client, domain)
            await repo.set_subdomain_source_cache(domain, cache_key, sorted(hosts))
            return name, hosts, ""
        except IncompleteSource as exc:
            return name, exc.hosts, str(exc)
        except Exception as exc:  # noqa: BLE001 - a passive source must not stop the run
            detail, retryable, cooldown = _source_error(exc)
            errors.append(f"直连：{detail}")

            # The worker container may not have a usable direct IPv6/TLS path
            # to public passive APIs even while the host can reach them. If a
            # tested SeaMoon/manual route exists, try it immediately instead
            # of waiting for another direct retry. This is fallback-only: it
            # does not proxy DNS resolution or HTTP probing of discovered hosts.
            fallback_allowed = fallback_client is not None and (
                isinstance(exc, (httpx.RequestError, httpx.TimeoutException, asyncio.TimeoutError))
                or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in {403, *range(500, 600)}
                )
            )
            if fallback_allowed:
                try:
                    async with asyncio.timeout(PASSIVE_TIMEOUT):
                        hosts = await function(fallback_client, domain)
                    await repo.set_subdomain_source_cache(domain, cache_key, sorted(hosts))
                    return name, hosts, ""
                except IncompleteSource as exc:
                    return name, exc.hosts, str(exc)
                except Exception as fallback_exc:  # noqa: BLE001 - keep source isolated
                    fallback_detail, fallback_retryable, fallback_cooldown = _source_error(fallback_exc)
                    errors.append(f"代理兜底：{fallback_detail}")
                    retryable = retryable or fallback_retryable
                    cooldown = max(cooldown, fallback_cooldown)

            if cooldown:
                await SOURCE_THROTTLE.cool_down(name, cooldown)
            if not retryable or attempt >= PASSIVE_ATTEMPTS:
                break
            # Retry immediately on a fresh connection / proxy. Source-level
            # spacing is already enforced by acquire(); a second sleep here
            # stacked 1s+2s onto every timeout and made large batches crawl.
        finally:
            await SOURCE_THROTTLE.release(name)
    return name, set(), f"{errors[-1]}（已重试 {len(errors)} 次）"


def generate_altdns_candidates(source_hosts: set[str], root: str) -> set[str]:
    """Generate bounded word/number variants from observed names.

    This follows the useful, non-invasive part of OneForAll's Altdns stage:
    learn words from already discovered labels, then try adjacent numeric
    versions and a small set of word combinations. Combinatorial inserts from
    the entire dictionary are intentionally avoided so a 70-domain ICP batch
    cannot expand into hundreds of thousands of HTTP probes.
    """
    observed = sorted(host for host in source_hosts if host != root)
    seed_words = [word for word in COMMON_PREFIXES if 2 <= len(word) <= 12][:24]
    learned: set[str] = set()
    candidates: set[str] = set()
    for host in observed:
        labels = host[: -(len(root) + 1)].split(".")
        for label in labels:
            parts = [part for part in re.split(r"[-_]", label) if part]
            learned.update(part for part in parts if 2 <= len(part) <= 24)
            for match in re.finditer(r"\d+", label):
                value = match.group(0)
                number = int(value)
                for delta in (-2, -1, 1, 2):
                    if number + delta >= 0:
                        replacement = str(number + delta).zfill(len(value))
                        variant = label[:match.start()] + replacement + label[match.end():]
                        candidates.add(f"{'.'.join([variant if item == label else item for item in labels])}.{root}" if len(labels) > 1 else f"{variant}.{root}")
        if len(labels) == 1:
            label = labels[0]
            for word in seed_words:
                candidates.add(f"{word}-{label}.{root}")
                candidates.add(f"{label}-{word}.{root}")
                if len(candidates) >= ALTDNS_CANDIDATE_LIMIT:
                    break
        if len(candidates) >= ALTDNS_CANDIDATE_LIMIT:
            break
    for word in sorted(learned)[:80]:
        candidates.add(f"{word}.{root}")
        if len(candidates) >= ALTDNS_CANDIDATE_LIMIT:
            break
    return {
        candidate for candidate in sorted(candidates)[:ALTDNS_CANDIDATE_LIMIT]
        if _candidate(candidate, root)
    }


async def collect_subdomains(
    repo: Repository,
    run_id: UUID,
    domains: list[str],
    options: dict[str, Any],
    *,
    lease_id: UUID,
) -> list[str]:
    """Collect multiple roots concurrently with bounded shared I/O.

    A previous implementation processed every root serially. For a task with
    dozens of ICP domains that made one slow/large root hold the entire queue
    for hours. Roots now run in a small bounded pool while DNS/HTTP semaphores
    still cap total outbound work. Results remain idempotent in PostgreSQL, so
    a retry or page refresh cannot create duplicates.
    """
    warnings: list[str] = []
    processed = 0
    total = len(domains)
    result_count = getattr(repo, "subdomain_result_count", None)
    discovered = await result_count(run_id) if result_count is not None else 0
    last_reported = 0
    last_report_at = 0.0
    progress_lock = asyncio.Lock()
    root_semaphore = shared_semaphore("subdomain:root", ROOT_CONCURRENCY)
    dns_semaphore = shared_semaphore("subdomain:dns", DNS_CONCURRENCY)
    http_semaphore = shared_semaphore("subdomain:http", HTTP_CONCURRENCY)
    passive_enabled = bool(options.get("passive", True))
    brute_enabled = bool(options.get("brute_force", True))
    deep_scan_enabled = bool(options.get("deep_scan", True))
    http_enabled = bool(options.get("http_probe", True))
    if not passive_enabled and not brute_enabled:
        raise SubdomainError("被动数据源和 DNS 字典至少启用一项")

    async def report_progress(phase: str, *, force: bool = False) -> None:
        nonlocal last_reported, last_report_at
        now = time.monotonic()
        async with progress_lock:
            if not force:
                if (
                    processed - last_reported < PROGRESS_BATCH_SIZE
                    and now - last_report_at < PROGRESS_INTERVAL_SECONDS
                ):
                    return
            await repo.update_subdomain_progress(
                run_id, processed, total, discovered, phase, lease_id=lease_id
            )
            last_reported = processed
            last_report_at = now

    async def increment_processed() -> None:
        nonlocal processed
        async with progress_lock:
            processed += 1

    async def increment_discovered(count: int = 1) -> None:
        nonlocal discovered
        if count <= 0:
            return
        async with progress_lock:
            discovered += count

    passive_timeout = httpx.Timeout(
        PASSIVE_TIMEOUT, connect=8.0, read=PASSIVE_TIMEOUT, write=10.0, pool=5.0
    )
    probe_timeout = httpx.Timeout(
        HTTP_PROBE_TIMEOUT, connect=3.0, read=HTTP_PROBE_TIMEOUT, write=5.0, pool=3.0
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/html, text/plain, */*",
    }
    async with (
        httpx.AsyncClient(
            timeout=passive_timeout,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as passive_client,
        httpx.AsyncClient(
            timeout=probe_timeout,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as probe_client,
    ):
        runtime_config = (
            await repo.get_runtime_config()
            if getattr(repo, "get_runtime_config", None) is not None
            else {}
        )
        try:
            from app.serverless_proxy import miit_proxy_urls
            proxy_routes = miit_proxy_urls(runtime_config)
        except Exception:  # noqa: BLE001 - proxy fallback is optional
            proxy_routes = []
        fallback_proxy = next((value for value in proxy_routes if value), "")
        api_settings = runtime_config.get("subdomain_api") or {}
        fofa_email = str(api_settings.get("fofa_email") or "").strip()
        fofa_key = str(api_settings.get("fofa_key") or "").strip()
        hunter_key = str(api_settings.get("hunter_key") or "").strip()
        passive_proxy_client = (
            httpx.AsyncClient(
                proxy=fallback_proxy,
                timeout=passive_timeout,
                follow_redirects=False,
                trust_env=False,
                headers=headers,
            )
            if fallback_proxy
            else None
        )

        async def process_root(root: str) -> list[str]:
            root_warnings: list[str] = []
            async with root_semaphore:
                await report_progress("collecting", force=True)
                sources: dict[str, set[str]] = {}
                inspected: dict[str, tuple[ResolvedHost | None, HttpProbe]] = {}
                resolved_cache: dict[str, ResolvedHost | None] = {}

                async def resolve_cached(hostname: str) -> ResolvedHost | None:
                    if hostname not in resolved_cache:
                        async with dns_semaphore:
                            try:
                                resolved_cache[hostname] = await resolve_hostname(hostname)
                            except DNSLookupError as exc:
                                root_warnings.append(f"[结果不完整] {exc}")
                                return None
                    return resolved_cache[hostname]

                passive_tasks = []
                if passive_enabled:
                    for source_name, function_name in PASSIVE_SOURCES:
                        passive_tasks.append(
                            asyncio.create_task(
                                _call_source(
                                    repo,
                                    source_name,
                                    globals()[function_name],
                                    passive_client,
                                    root,
                                    fallback_client=passive_proxy_client,
                                )
                            )
                        )
                    if fofa_email and fofa_key:
                        passive_tasks.append(
                            asyncio.create_task(
                                _call_source(
                                    repo,
                                    "FOFA",
                                    partial(collect_fofa, email=fofa_email, key=fofa_key),
                                    passive_client,
                                    root,
                                    fallback_client=passive_proxy_client,
                                )
                            )
                        )
                    if hunter_key:
                        passive_tasks.append(
                            asyncio.create_task(
                                _call_source(
                                    repo,
                                    "Hunter",
                                    partial(collect_hunter, key=hunter_key),
                                    passive_client,
                                    root,
                                    fallback_client=passive_proxy_client,
                                )
                            )
                        )
                wildcard_task = (
                    asyncio.create_task(_wildcard_ips(root))
                    if passive_enabled or brute_enabled else None
                )

                pending_rows: list[dict[str, Any]] = []
                persist_lock = asyncio.Lock()
                write_lock = asyncio.Lock()

                def _merge_pending_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
                    merged: dict[str, dict[str, Any]] = {}
                    for item in rows:
                        current = merged.get(item["hostname"])
                        if current is None:
                            merged[item["hostname"]] = dict(item)
                            continue
                        if item["ips"]:
                            current["ips"] = item["ips"]
                        if item["canonical_name"]:
                            current["canonical_name"] = item["canonical_name"]
                        current["wildcard"] = item["wildcard"]
                        if item["http_url"]:
                            current["http_url"] = item["http_url"]
                        if item["http_status"] is not None:
                            current["http_status"] = item["http_status"]
                        if item["title"]:
                            current["title"] = item["title"]
                        current["sources"] = sorted(
                            set(current["sources"]) | set(item["sources"])
                        )
                    return list(merged.values())

                async def _flush_rows(rows: list[dict[str, Any]]) -> None:
                    rows = _merge_pending_rows(rows)
                    async with write_lock:
                        # Even an empty flush waits for earlier writes. The
                        # completion barrier must include a periodic flush
                        # that already removed its rows from pending_rows.
                        if not rows:
                            return
                        batch_fn = getattr(repo, "add_subdomain_results", None)
                        if batch_fn is not None:
                            inserted = await batch_fn(run_id, rows)
                        else:
                            inserted = 0
                            for item in rows:
                                if await repo.add_subdomain_result(run_id, **item):
                                    inserted += 1
                        await increment_discovered(inserted)

                async def flush_persist() -> None:
                    async with persist_lock:
                        rows = pending_rows[:]
                        pending_rows.clear()
                    await _flush_rows(rows)

                async def persist(
                    resolved: ResolvedHost, probe: HttpProbe, wildcard_ips: set[str]
                ) -> None:
                    host = resolved.hostname
                    is_wildcard = bool(
                        wildcard_ips and set(resolved.ips).issubset(wildcard_ips)
                    )
                    passive_sources = sources[host] - {"DNS字典", "AltDNS"}
                    if is_wildcard and not passive_sources:
                        return
                    row = {
                        "root_domain": root,
                        "hostname": host,
                        "ips": resolved.ips,
                        "canonical_name": resolved.canonical_name,
                        "wildcard": is_wildcard,
                        "http_url": probe.url,
                        "http_status": probe.status,
                        "title": probe.title,
                        "sources": sorted(sources[host]),
                    }
                    async with persist_lock:
                        pending_rows.append(row)
                        if len(pending_rows) < PERSIST_BATCH_SIZE:
                            return
                        rows = pending_rows[:]
                        pending_rows.clear()
                    await _flush_rows(rows)

                async def merge_inspected(
                    candidates: set[str], wildcard_ips: set[str]
                ) -> None:
                    for host in sorted(candidates & set(inspected)):
                        resolved, probe = inspected[host]
                        if resolved:
                            await persist(resolved, probe, wildcard_ips)
                    await flush_persist()

                async def inspect_many(
                    candidates: list[str], wildcard_ips: set[str]
                ) -> None:
                    # Separate DNS and HTTP queues replace the 500-host
                    # barriers. Slow HTTP requests cannot hold the next DNS
                    # batch, and every observed candidate is scheduled once.
                    dns_queue: asyncio.Queue[str] = asyncio.Queue()
                    probe_queue: asyncio.Queue[tuple[str, ResolvedHost]] = asyncio.Queue(
                        maxsize=HTTP_CONCURRENCY * 2
                    )
                    scheduled: set[str] = set(inspected)
                    outstanding = 0
                    finished = asyncio.Event()

                    def schedule(host: str) -> None:
                        nonlocal outstanding
                        if host in scheduled:
                            return
                        scheduled.add(host)
                        outstanding += 1
                        dns_queue.put_nowait(host)

                    def complete() -> None:
                        nonlocal outstanding
                        outstanding -= 1
                        if outstanding == 0:
                            finished.set()

                    for host in candidates:
                        schedule(host)
                    if not outstanding:
                        return

                    async def dns_worker() -> None:
                        while True:
                            host = await dns_queue.get()
                            resolved = await resolve_cached(host)
                            inspected[host] = (resolved, HttpProbe())
                            if resolved:
                                await persist(resolved, HttpProbe(), wildcard_ips)
                                is_wildcard = bool(
                                    wildcard_ips and set(resolved.ips).issubset(wildcard_ips)
                                )
                                can_probe = not is_wildcard or bool(
                                    sources[host] - {"DNS字典", "AltDNS"}
                                )
                                if http_enabled and can_probe:
                                    await probe_queue.put((host, resolved))
                                else:
                                    complete()
                            else:
                                complete()
                            await report_progress("resolving")

                    async def http_worker() -> None:
                        while True:
                            host, resolved = await probe_queue.get()
                            async with http_semaphore:
                                probe = await probe_http(probe_client, resolved, root)
                            inspected[host] = (resolved, probe)
                            if probe.url or probe.status is not None or probe.title:
                                await persist(resolved, probe, wildcard_ips)
                            for found in probe.discovered:
                                # Only canonical, in-scope candidates enter DNS,
                                # including references from mocked/custom probes.
                                found_host = _candidate(found, root)
                                if found_host:
                                    sources.setdefault(found_host, set()).add("页面内容")
                                    if found_host in inspected:
                                        prior, prior_probe = inspected[found_host]
                                        if prior:
                                            await persist(prior, prior_probe, wildcard_ips)
                                    schedule(found_host)
                            complete()
                            await report_progress("probing")

                    async def flush_worker() -> None:
                        while True:
                            await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)
                            await flush_persist()
                            await report_progress("probing" if http_enabled else "resolving")

                    await report_progress("resolving", force=True)
                    worker_coroutines = [
                        dns_worker() for _ in range(min(DNS_CONCURRENCY, outstanding))
                    ]
                    if http_enabled:
                        worker_coroutines.extend(http_worker() for _ in range(HTTP_CONCURRENCY))
                    worker_coroutines.append(flush_worker())

                    async def finish_writes() -> None:
                        await finished.wait()
                        await flush_persist()

                    await supervise(finish_writes(), worker_coroutines)
                    await flush_persist()
                    await report_progress("probing" if http_enabled else "resolving", force=True)

                try:
                    if brute_enabled:
                        for prefix in COMMON_PREFIXES:
                            sources.setdefault(f"{prefix}.{root}", set()).add("DNS字典")
                        wildcard_ips = await wildcard_task if wildcard_task else set()
                        # Dictionary DNS verification starts immediately; passive
                        # sources are merged when each one finishes.
                        await inspect_many(sorted(sources), wildcard_ips)
                    else:
                        wildcard_ips = await wildcard_task if wildcard_task else set()

                    if passive_tasks:
                        for task in asyncio.as_completed(passive_tasks):
                            source_name, hosts, error = await task
                            if error:
                                root_warnings.append(f"{root} · {source_name}：{error}")
                            for host in hosts:
                                sources.setdefault(host, set()).add(source_name)
                            if hosts:
                                await merge_inspected(set(hosts), wildcard_ips)
                                await inspect_many(sorted(hosts), wildcard_ips)

                        if brute_enabled and deep_scan_enabled:
                            altdns_hosts = generate_altdns_candidates(set(sources), root)
                            for host in altdns_hosts:
                                sources.setdefault(host, set()).add("AltDNS")
                            await inspect_many(sorted(altdns_hosts), wildcard_ips)
                finally:
                    for task in passive_tasks:
                        if not task.done():
                            task.cancel()
                    if wildcard_task and not wildcard_task.done():
                        wildcard_task.cancel()
                    await asyncio.gather(*passive_tasks, return_exceptions=True)
                    if wildcard_task:
                        await asyncio.gather(wildcard_task, return_exceptions=True)
                    await flush_persist()
            return root_warnings

        async def process_root_safely(root: str) -> list[str]:
            try:
                return await process_root(root)
            except (asyncio.CancelledError, LeaseLost):
                raise
            finally:
                await increment_processed()
                await report_progress("collecting", force=True)

        try:
            async def collect_roots() -> None:
                root_warnings = await gather_cancel_on_error(
                    *(process_root_safely(root) for root in domains)
                )
                for values in root_warnings:
                    warnings.extend(values)

            async def keepalive() -> None:
                while True:
                    await asyncio.sleep(5)
                    await report_progress("collecting", force=True)

            await supervise(collect_roots(), [keepalive()])
        finally:
            if passive_proxy_client is not None:
                await passive_proxy_client.aclose()

    await report_progress("completed", force=True)
    return list(dict.fromkeys(warnings))
