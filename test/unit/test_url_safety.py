"""Unit tests for src/url_safety.py — SSRF guard for client-supplied URLs."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.url_safety import UnsafeUrlError, validate_outbound_url


def test_public_https_url_is_allowed():
    validate_outbound_url("https://example.com/callback")  # must not raise


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


def test_allow_private_bypasses_range_checks():
    validate_outbound_url(
        "http://127.0.0.1:8080/hook", allow_private=True
    )  # must not raise
    validate_outbound_url("http://10.0.0.5/hook", allow_private=True)  # must not raise


def test_allow_private_still_enforces_scheme():
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url("ftp://10.0.0.5/hook", allow_private=True)


def test_unresolvable_host_is_blocked():
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url("http://this-host-should-not-resolve.invalid/hook")
