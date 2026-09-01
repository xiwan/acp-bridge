"""Unit tests for src/url_safety.py — SSRF guard for client-supplied URLs."""

import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.url_safety import SafeTarget, UnsafeUrlError, validate_outbound_url


def test_public_https_url_is_allowed():
    target = validate_outbound_url("https://example.com/callback")
    assert isinstance(target, SafeTarget)
    assert target.host == "example.com"
    # pinned_url swaps the host for the resolved literal IP; path/query survive.
    assert target.pinned_url.endswith("/callback")
    assert target.ip in target.pinned_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/hook",
        "http://localhost/hook",
        "http://[::1]/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254",
        "http://10.0.0.5/hook",
        "http://192.168.1.5/hook",
        "http://172.16.0.5/hook",
        "http://100.100.100.200/latest/meta-data/",  # Alibaba Cloud metadata
        "http://[fd00::1]/hook",  # IPv6 ULA (RFC 4193, is_private)
        "http://[fe80::1]/hook",  # IPv6 link-local
    ],
)
def test_private_and_metadata_targets_are_blocked(url):
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/hook",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_SET",
        "",
    ],
)
def test_bad_scheme_or_empty_is_blocked(url):
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url(url)


def test_missing_host_is_blocked():
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url("http:///no-host")


def test_allowed_targets_opt_specific_hosts_out_of_range_checks():
    target = validate_outbound_url(
        "http://127.0.0.1:8080/hook", allowed_targets=frozenset({"127.0.0.1"})
    )
    assert target.ip == "127.0.0.1"

    target = validate_outbound_url(
        "http://10.0.0.5/hook", allowed_targets=frozenset({"10.0.0.0/8"})
    )
    assert target.ip == "10.0.0.5"


def test_allowed_targets_do_not_cover_unlisted_private_hosts():
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url("http://192.168.1.5/hook", allowed_targets=frozenset({"10.0.0.0/8"}))


def test_allowed_targets_still_enforce_scheme():
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url("ftp://10.0.0.5/hook", allowed_targets=frozenset({"10.0.0.0/8"}))


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
    ],
)
def test_allowed_targets_never_bypass_metadata_block(url):
    """The most severe gap from the upstream review: allowlisting a private
    CIDR/host must never also unblock cloud metadata endpoints."""
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url(url, allowed_targets=frozenset({"0.0.0.0/0"}))


def test_unresolvable_host_is_blocked():
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url("http://this-host-should-not-resolve.invalid/hook")


def test_pinned_url_targets_the_validated_ip_not_a_rebound_one(monkeypatch):
    """Simulate DNS rebinding: the resolver used at validation time answers
    with a public IP, but a later lookup of the same host (as a real HTTP
    client would perform independently) answers with a private one. Because
    the request must be made against SafeTarget.pinned_url — the literal IP
    validation already checked — the rebind answer is never consulted again."""
    calls = {"n": 0}
    real_getaddrinfo = socket.getaddrinfo

    def flaky_getaddrinfo(host, *args, **kwargs):
        if host == "rebind.example":
            calls["n"] += 1
            ip = "93.184.216.34" if calls["n"] == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", flaky_getaddrinfo)

    target = validate_outbound_url("http://rebind.example/hook")
    assert target.ip == "93.184.216.34"
    assert target.ip in target.pinned_url

    # A second, independent resolution (what an unpinned HTTP client would do)
    # would have landed on the private address — proving the rebind is real
    # and that pinning, not re-resolving, is what closes it.
    second = socket.getaddrinfo("rebind.example", None)
    assert second[0][4][0] == "127.0.0.1"
