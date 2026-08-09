"""Shared SSRF guard for user-supplied outbound URLs.

Used wherever the Bridge fetches or posts to a URL that came from a client
request rather than trusted server config: `callback_url` (POST /jobs) and the
mesh L3 workspace relay (`ws_in`/`ws_out`, reachable via POST /a2a). Blocks
loopback, link-local, private, reserved, and cloud-metadata targets by
default. Deployments that intentionally point callbacks at a private-network
service (e.g. a self-hosted n8n instance) can opt specific hosts/CIDRs out of
the private-range checks via `allowed_targets` (wired from
`security.allowed_private_targets`) — cloud metadata hosts/IPs are always
blocked regardless, since no legitimate callback target is a metadata
endpoint.

`validate_outbound_url` returns a `SafeTarget` pinned to the exact IP it
validated, rather than just raising-or-not: the DNS resolution done here and
the one the HTTP client performs when it actually connects are two separate
lookups, and an attacker controlling DNS for the host can answer them
differently (rebind to a private/metadata address between the two). Callers
must issue the real request against `SafeTarget.pinned_url` (not the original
URL) with `extensions={"sni_hostname": target.host}` so TLS still validates
against the real hostname, and a `Host: <target.host_header>` header for
virtual-hosted targets — see webhook.py and mesh_a2a.py.

Callers must also keep redirects disabled (`follow_redirects=False`, httpx's
default). Pinning only covers the connection this module validated; a `30x`
response pointing at a private or metadata address would be followed against a
fresh, unvalidated resolution, reopening the hole from the other side.

Known limitation: the resolution below is a blocking call, not offloaded to a
thread executor, so a slow-to-resolve host adds latency to whichever request
path calls this.
"""

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

ALLOWED_SCHEMES = {"http", "https"}
# DNS names that resolve to metadata endpoints (not literal IPs, so they can't
# be caught by the range checks below).
_METADATA_HOSTNAMES = {"metadata.google.internal"}
# Metadata IPs outside the standard loopback/link-local/private/reserved
# ranges — must be checked explicitly, whether they appear as a literal URL
# host or as a DNS resolution result, since is_loopback/is_link_local/etc.
# alone would miss them. 169.254.169.254 (AWS/Azure/most clouds) is already
# covered by is_link_local (169.254.0.0/16); listed here anyway for clarity.
_METADATA_IPS = frozenset(
    ipaddress.ip_address(ip) for ip in ("169.254.169.254", "100.100.100.200")
)  # 100.100.100.200 = Alibaba Cloud


class UnsafeUrlError(ValueError):
    """Raised when a user-supplied URL fails outbound SSRF validation."""


@dataclass(frozen=True)
class SafeTarget:
    """A URL that passed SSRF validation, pinned to the IP that was checked.

    `host` is the bare hostname, for TLS SNI. `host_header` is what the
    original URL's authority would have produced as a `Host` header — i.e.
    including a non-default port — because the connection is made to a literal
    IP and the origin server still needs the real authority to route
    virtual-hosted requests correctly.
    """

    pinned_url: str
    host: str
    host_header: str
    ip: str


def _is_metadata(ip) -> bool:
    return ip in _METADATA_IPS


def _is_private_range(ip) -> bool:
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _target_allowed(host: str, ip, allowed_targets) -> bool:
    """True if `host`/`ip` opts out of the private-range check via `allowed_targets`.

    Entries containing "/" are matched as CIDR networks against the resolved
    IP; other entries are matched as exact hostnames. Never consulted for the
    metadata check — see validate_outbound_url.
    """
    host_l = host.lower()
    for entry in allowed_targets:
        if "/" in entry:
            try:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
        elif host_l == entry.lower():
            return True
    return False


def _pin_host(parsed, ip: str) -> str:
    """Rebuild `parsed`'s URL with its host replaced by the literal `ip`."""
    netloc_host = f"[{ip}]" if ":" in ip else ip
    if parsed.port:
        netloc_host = f"{netloc_host}:{parsed.port}"
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        netloc_host = f"{userinfo}@{netloc_host}"
    return urlunparse(parsed._replace(netloc=netloc_host))


def trusted_target(url: str) -> SafeTarget:
    """Build a SafeTarget for a URL that comes from trusted server config.

    No validation and no DNS resolution: the operator chose this destination
    (e.g. `webhook.url` pointing at an OpenClaw gateway on a private address),
    so the SSRF guard — which exists to contain *client-supplied* URLs — must
    not apply to it. `pinned_url` is the original URL, so the HTTP client
    resolves it normally; `host`/`host_header` match what it would have sent
    anyway, keeping callers uniform.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    host_header = f"[{host}]" if ":" in host else host
    if parsed.port:
        host_header = f"{host_header}:{parsed.port}"
    return SafeTarget(pinned_url=url, host=host, host_header=host_header, ip="")


def validate_outbound_url(url: str, *, allowed_targets: frozenset[str] = frozenset()) -> SafeTarget:
    """Raise UnsafeUrlError if `url` is unsafe, else return a pinned SafeTarget.

    Checks the scheme against an allowlist, resolves the host, and rejects
    loopback/link-local/private/reserved/multicast ranges plus cloud metadata
    hosts/IPs. `allowed_targets` (hostnames or CIDR strings) opts specific
    private-network targets out of the range checks — metadata targets are
    never exempt.
    """
    if not url:
        raise UnsafeUrlError("empty url")
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"unsupported scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("missing host")
    if host.lower() in _METADATA_HOSTNAMES:
        raise UnsafeUrlError(f"blocked metadata host: {host}")

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        candidates = [literal_ip]
    else:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise UnsafeUrlError(f"cannot resolve host: {host} ({e})") from e
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]

    for candidate in candidates:
        if _is_metadata(candidate):
            raise UnsafeUrlError(f"blocked metadata address: {candidate}")
        if _is_private_range(candidate) and not _target_allowed(host, candidate, allowed_targets):
            raise UnsafeUrlError(f"blocked address: {candidate}")

    resolved_ip = str(candidates[0])
    host_header = f"[{host}]" if ":" in host else host
    if parsed.port:
        host_header = f"{host_header}:{parsed.port}"
    return SafeTarget(pinned_url=_pin_host(parsed, resolved_ip), host=host,
                      host_header=host_header, ip=resolved_ip)
