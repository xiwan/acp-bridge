"""Shared SSRF guard for user-supplied outbound URLs.

Used wherever the Bridge fetches or posts to a URL that came from a client
request rather than trusted server config: `callback_url` (POST /jobs) and the
mesh L3 workspace relay (`ws_in`/`ws_out`, reachable via POST /a2a). Blocks
loopback, link-local, private, reserved, and cloud-metadata targets by
default. Deployments that intentionally point callbacks at a private-network
service (e.g. a self-hosted n8n instance) can opt in per-target via
`allow_private=True`, wired from `security.allow_private_callback_urls`.

Known limitations (see docs/security.md "SSRF Protection" for detail): this
validates the hostname's DNS resolution at check time, but the HTTP client
that later fetches the URL resolves independently — a DNS-rebinding attacker
could still redirect the actual connection after validation passes. The
resolution below is also a blocking call, not offloaded to a thread executor,
so a slow-to-resolve host adds latency to whichever request path calls this.
"""

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
# DNS names that resolve to metadata endpoints (not literal IPs, so they can't
# be caught by the range checks in _is_unsafe_ip below).
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


def _is_unsafe_ip(ip) -> bool:
    return (
        ip in _METADATA_IPS
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_outbound_url(url: str, *, allow_private: bool = False) -> None:
    """Raise UnsafeUrlError if `url` is unsafe for the server to fetch/POST to.

    Checks the scheme against an allowlist, then resolves the host and rejects
    loopback/link-local/private/reserved ranges plus known cloud metadata
    hostnames — unless `allow_private` opts a trusted deployment out of the
    range checks (scheme validation still applies).
    """
    if not url:
        raise UnsafeUrlError("empty url")
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"unsupported scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("missing host")
    if allow_private:
        return
    if host.lower() in _METADATA_HOSTNAMES:
        raise UnsafeUrlError(f"blocked metadata host: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_unsafe_ip(ip):
            raise UnsafeUrlError(f"blocked address: {host}")
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UnsafeUrlError(f"cannot resolve host: {host} ({e})") from e
    for info in infos:
        resolved = ipaddress.ip_address(info[4][0])
        if _is_unsafe_ip(resolved):
            raise UnsafeUrlError(f"host {host} resolves to blocked address: {resolved}")
