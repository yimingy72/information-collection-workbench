from __future__ import annotations

import asyncio
import html
import json
import ipaddress
import random
import re
import socket
import string
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit
from uuid import UUID

import httpx
import tldextract

from app.repository import Repository

PASSIVE_TIMEOUT = 20.0
PASSIVE_ATTEMPTS = 2
SOURCE_COOLDOWN_SECONDS = 60.0
PROGRESS_INTERVAL_SECONDS = 0.4
PROGRESS_BATCH_SIZE = 25
INSPECTION_BATCH_SIZE = 500
DNS_CONCURRENCY = 40
HTTP_CONCURRENCY = 16
# Run a few root domains concurrently. DNS/HTTP semaphores below still cap
# total outbound work, while preventing one large root from blocking all other
# ICP-derived domains for the full duration of its scan.
ROOT_CONCURRENCY = 3
HTTP_PROBE_TIMEOUT = 8.0
MAX_CANDIDATES_PER_DOMAIN = 10_000
ALTDNS_CANDIDATE_LIMIT = 2_000
COMMON_CRAWL_RESULT_LIMIT = 1_000
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

_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


class SubdomainError(RuntimeError):
    pass


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
        if page < 2:
            await asyncio.sleep(0.35)
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
    client: httpx.AsyncClient, domain: str, record_type: str
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
            host = _candidate(value, domain)
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
        _collect_dns_record_type(client, f"{prefix}.{domain}", "SRV")
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
            except httpx.HTTPError:
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
    return found


PASSIVE_SOURCES = (
    ("crt.sh", "collect_crtsh"),
    ("CertSpotter", "collect_certspotter"),
    ("HackerTarget", "collect_hackertarget"),
    ("urlscan.io", "collect_urlscan"),
    ("RapidDNS", "collect_rapiddns"),
    ("DNS记录", "collect_dns_records"),
    ("SRV记录", "collect_srv_records"),
    ("站点元数据", "collect_web_metadata"),
    ("Common Crawl", "collect_commoncrawl"),
)

SOURCE_MIN_INTERVALS = {
    "crt.sh": 0.5,
    "CertSpotter": 2.0,
    "HackerTarget": 1.0,
    "urlscan.io": 1.0,
    "RapidDNS": 0.5,
    "DNS记录": 0.15,
    "SRV记录": 0.15,
    "站点元数据": 0.5,
    "Common Crawl": 1.0,
}


class _SourceThrottle:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next_at: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}

    async def reserve(self, name: str) -> str | None:
        async with self._lock:
            now = time.monotonic()
            cooldown_until = self._cooldown_until.get(name, 0.0)
            if cooldown_until > now:
                remaining = max(1, int(cooldown_until - now))
                return f"请求频率受限，已暂停约 {remaining} 秒"
            wait = max(0.0, self._next_at.get(name, 0.0) - now)
            self._next_at[name] = max(now, self._next_at.get(name, 0.0)) + SOURCE_MIN_INTERVALS.get(name, 0.25)
        if wait:
            await asyncio.sleep(wait)
        return None

    async def cool_down(self, name: str, seconds: float) -> None:
        async with self._lock:
            self._cooldown_until[name] = max(
                self._cooldown_until.get(name, 0.0), time.monotonic() + seconds
            )


SOURCE_THROTTLE = _SourceThrottle()


async def resolve_hostname(hostname: str) -> ResolvedHost | None:
    def lookup() -> ResolvedHost | None:
        try:
            answers = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_CANONNAME
            )
        except (socket.gaierror, OSError):
            return None
        ips = sorted({item[4][0] for item in answers if item[4]})
        if not ips:
            return None
        canonical = next((str(item[3]).rstrip(".") for item in answers if item[3]), "")
        return ResolvedHost(hostname=hostname, ips=ips, canonical_name=canonical)

    return await asyncio.to_thread(lookup)


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
        except (httpx.HTTPError, asyncio.TimeoutError):
            continue
    return HttpProbe()


async def _wildcard_ips(domain: str) -> set[str]:
    hostnames = [
        "of-" + "".join(random.choice(string.ascii_lowercase) for _ in range(18)) + f".{domain}"
        for _ in range(3)
    ]
    resolved_hosts = await asyncio.gather(*(resolve_hostname(host) for host in hostnames))
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
    if isinstance(exc, httpx.RequestError):
        return "网络连接失败", True, 0.0
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return "返回格式异常", False, 0.0
    detail = " ".join(str(exc).split())[:120] or type(exc).__name__
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
    cached = await repo.get_subdomain_source_cache(domain, name)
    if cached is not None:
        return name, set(cached), ""
    errors: list[str] = []
    for attempt in range(1, PASSIVE_ATTEMPTS + 1):
        throttled = await SOURCE_THROTTLE.reserve(name)
        if throttled:
            return name, set(), throttled
        try:
            async with asyncio.timeout(PASSIVE_TIMEOUT):
                hosts = await function(client, domain)
            await repo.set_subdomain_source_cache(domain, name, sorted(hosts))
            return name, hosts, ""
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
                    and exc.response.status_code in {403, 429, *range(500, 600)}
                )
            )
            if fallback_allowed:
                try:
                    async with asyncio.timeout(PASSIVE_TIMEOUT):
                        hosts = await function(fallback_client, domain)
                    await repo.set_subdomain_source_cache(domain, name, sorted(hosts))
                    return name, hosts, ""
                except Exception as fallback_exc:  # noqa: BLE001 - keep source isolated
                    fallback_detail, fallback_retryable, fallback_cooldown = _source_error(fallback_exc)
                    errors.append(f"代理兜底：{fallback_detail}")
                    retryable = retryable or fallback_retryable
                    cooldown = max(cooldown, fallback_cooldown)

            if cooldown:
                await SOURCE_THROTTLE.cool_down(name, cooldown)
            if not retryable or attempt >= PASSIVE_ATTEMPTS:
                break
            await asyncio.sleep(min(2.0, float(attempt)))
    return name, set(), f"{errors[-1]}（已重试 {len(errors)} 次）"


def generate_altdns_candidates(source_hosts: set[str], root: str) -> set[str]:
    """Generate bounded word/number variants from observed names.

    This follows the useful, non-invasive part of OneForAll's Altdns stage:
    learn words from already discovered labels, then try adjacent numeric
    versions and word combinations. It deliberately does not perform any
    mutation or takeover action and is capped per root domain.
    """
    observed = sorted(host for host in source_hosts if host != root)
    words = {word for word in COMMON_PREFIXES if 2 <= len(word) <= 24}
    candidates: set[str] = set()
    for host in observed:
        labels = host[: -(len(root) + 1)].split(".")
        for label in labels:
            parts = [part for part in re.split(r"[-_]", label) if part]
            words.update(part for part in parts if 2 <= len(part) <= 24)
            for match in re.finditer(r"\d+", label):
                value = match.group(0)
                number = int(value)
                for delta in (-2, -1, 1, 2):
                    replacement = str(number + delta).zfill(len(value))
                    if number + delta >= 0:
                        variant = label[:match.start()] + replacement + label[match.end():]
                        candidates.add(f"{variant}.{root}")
        for label_index, label in enumerate(labels):
            if len(words) * max(1, len(labels)) > ALTDNS_CANDIDATE_LIMIT * 2:
                word_iter = sorted(words)[:ALTDNS_CANDIDATE_LIMIT // max(1, len(labels))]
            else:
                word_iter = sorted(words)
            for word in word_iter:
                for variant in (f"{word}-{label}", f"{label}-{word}"):
                    parts_copy = list(labels)
                    parts_copy[label_index] = variant
                    candidates.add(".".join(parts_copy + [root]))
                if len(labels) < 3:
                    parts_copy = list(labels)
                    parts_copy.insert(label_index, word)
                    candidates.add(".".join(parts_copy + [root]))
                if len(candidates) >= ALTDNS_CANDIDATE_LIMIT:
                    return {
                        candidate for candidate in sorted(candidates)[:ALTDNS_CANDIDATE_LIMIT]
                        if _candidate(candidate, root)
                    }
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
    total = 0
    result_count = getattr(repo, "subdomain_result_count", None)
    discovered = await result_count(run_id) if result_count is not None else 0
    last_reported = 0
    last_report_at = 0.0
    progress_lock = asyncio.Lock()
    root_semaphore = asyncio.Semaphore(ROOT_CONCURRENCY)
    dns_semaphore = asyncio.Semaphore(DNS_CONCURRENCY)
    http_semaphore = asyncio.Semaphore(HTTP_CONCURRENCY)
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

    async def increment_total(value: int) -> None:
        nonlocal total
        async with progress_lock:
            total += value

    async def increment_processed() -> None:
        nonlocal processed
        async with progress_lock:
            processed += 1

    async def increment_discovered() -> None:
        nonlocal discovered
        async with progress_lock:
            discovered += 1

    passive_timeout = httpx.Timeout(
        PASSIVE_TIMEOUT, connect=8.0, read=PASSIVE_TIMEOUT, write=10.0, pool=5.0
    )
    probe_timeout = httpx.Timeout(
        HTTP_PROBE_TIMEOUT, connect=3.0, read=HTTP_PROBE_TIMEOUT, write=5.0, pool=3.0
    )
    headers = {
        "User-Agent": "information-collection-workbench/0.3 (+authorized-subdomain-enumeration)"
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
                            resolved_cache[hostname] = await resolve_hostname(hostname)
                    return resolved_cache[hostname]

                passive_tasks = [
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
                    for source_name, function_name in PASSIVE_SOURCES
                ] if passive_enabled else []
                wildcard_task = (
                    asyncio.create_task(_wildcard_ips(root))
                    if passive_enabled or brute_enabled else None
                )

                async def persist(
                    resolved: ResolvedHost, probe: HttpProbe, wildcard_ips: set[str]
                ) -> None:
                    host = resolved.hostname
                    is_wildcard = bool(
                        wildcard_ips and set(resolved.ips).issubset(wildcard_ips)
                    )
                    passive_sources = sources[host] - {"DNS字典"}
                    if is_wildcard and not passive_sources:
                        return
                    inserted = await repo.add_subdomain_result(
                        run_id,
                        root_domain=root,
                        hostname=host,
                        ips=resolved.ips,
                        canonical_name=resolved.canonical_name,
                        wildcard=is_wildcard,
                        http_url=probe.url,
                        http_status=probe.status,
                        title=probe.title,
                        sources=sorted(sources[host]),
                    )
                    if inserted:
                        await increment_discovered()

                async def merge_inspected(
                    candidates: set[str], wildcard_ips: set[str]
                ) -> None:
                    for host in sorted(candidates & set(inspected)):
                        resolved, probe = inspected[host]
                        if resolved:
                            await persist(resolved, probe, wildcard_ips)

                async def inspect_many(
                    candidates: list[str], wildcard_ips: set[str]
                ) -> None:
                    pending = [host for host in candidates if host not in inspected]
                    if not pending:
                        return
                    phase = "probing" if http_enabled else "resolving"
                    while pending:
                        remaining = MAX_CANDIDATES_PER_DOMAIN - len(inspected)
                        if remaining <= 0:
                            root_warnings.append(
                                f"{root}：候选数超过单域名上限 {MAX_CANDIDATES_PER_DOMAIN}，已截断"
                            )
                            return
                        if len(pending) > remaining:
                            root_warnings.append(
                                f"{root}：候选数超过单域名上限 {MAX_CANDIDATES_PER_DOMAIN}，已截断"
                            )
                            pending = pending[:remaining]
                        batch = pending[:INSPECTION_BATCH_SIZE]
                        pending = pending[INSPECTION_BATCH_SIZE:]
                        await increment_total(len(batch))
                        await report_progress("resolving", force=True)

                        async def inspect(
                            hostname: str,
                        ) -> tuple[str, ResolvedHost | None, HttpProbe]:
                            resolved = await resolve_cached(hostname)
                            if not resolved:
                                return hostname, None, HttpProbe()
                            # Persist DNS before HTTP enrichment. A slow or
                            # blocked web server must not hide a valid DNS hit.
                            await persist(resolved, HttpProbe(), wildcard_ips)
                            if not http_enabled:
                                return hostname, resolved, HttpProbe()
                            async with http_semaphore:
                                probe = await probe_http(probe_client, resolved, root)
                            return hostname, resolved, probe

                        tasks = [asyncio.create_task(inspect(host)) for host in batch]
                        newly_discovered: set[str] = set()
                        try:
                            for task in asyncio.as_completed(tasks):
                                host, resolved, probe = await task
                                inspected[host] = (resolved, probe)
                                await increment_processed()
                                if resolved:
                                    if probe.url or probe.status is not None or probe.title:
                                        await persist(resolved, probe, wildcard_ips)
                                    for found_host in probe.discovered:
                                        sources.setdefault(found_host, set()).add("页面内容")
                                        newly_discovered.add(found_host)
                                await report_progress(phase)
                        finally:
                            for task in tasks:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                        pending.extend(
                            sorted(host for host in newly_discovered if host not in inspected)
                        )
                    await report_progress(phase, force=True)

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
            return root_warnings

        async def process_root_safely(root: str) -> list[str]:
            try:
                return await process_root(root)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate one root
                detail = " ".join(str(exc).split())[:240] or type(exc).__name__
                return [f"{root}：查询失败：{detail}"]

        try:
            root_warnings = await asyncio.gather(
                *(process_root_safely(root) for root in domains)
            )
            for values in root_warnings:
                warnings.extend(values)
        finally:
            if passive_proxy_client is not None:
                await passive_proxy_client.aclose()

    await report_progress("completed", force=True)
    return list(dict.fromkeys(warnings))
