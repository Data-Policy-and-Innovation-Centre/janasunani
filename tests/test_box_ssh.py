"""The SSH-opener must ask for its public address over IPv4.

`ssh` to the box can only use IPv4 (the box is a bare A record), so the address
the security group is opened for has to come from an IPv4 lookup. On a
carrier-NAT network the two families egress as different public addresses, and
asking over the wrong one opens a rule the SSH connection never matches.
"""

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "box_ssh", Path(__file__).resolve().parents[1] / "scripts" / "box_ssh.py"
)
box_ssh = importlib.util.module_from_spec(SPEC)
sys.modules["box_ssh"] = box_ssh
SPEC.loader.exec_module(box_ssh)


V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 443))
V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))


class _Response:
    """The two-line stand-in for what checkip.amazonaws.com returns."""

    def __init__(self, families):
        self._families = families

    def read(self):
        # The service reports the address it saw, which on this kind of network
        # differs by family. Encode that: v6-first lookup -> the wrong address.
        wrong, right = b"152.57.34.241\n", b"152.57.1.35\n"
        return right if self._families and self._families[0] == socket.AF_INET else wrong

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def dual_stack(monkeypatch):
    """A host that resolves v6 first, and a urlopen that records what was asked."""
    seen: list[int] = []

    def getaddrinfo(*args, **kwargs):
        return [V6, V4]

    def urlopen(url, timeout=None):
        infos = socket.getaddrinfo("checkip.amazonaws.com", 443)
        seen.clear()
        seen.extend(info[0] for info in infos)
        return _Response(seen)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(box_ssh.urllib.request, "urlopen", urlopen)
    return seen


def test_current_ip_asks_over_ipv4(dual_stack):
    """The address opened is the one an IPv4 connection egresses as."""
    assert box_ssh.current_ip() == "152.57.1.35"
    assert dual_stack == [socket.AF_INET], "the lookup still offered IPv6 first"


def test_ipv4_only_restores_getaddrinfo():
    """A context manager that leaks its monkeypatch would poison every later call."""
    before = socket.getaddrinfo
    with box_ssh._ipv4_only():
        assert socket.getaddrinfo is not before
    assert socket.getaddrinfo is before


def test_ipv4_only_falls_back_when_a_host_is_v6_only(monkeypatch):
    """Filtering must not turn a v6-only host into an empty, unusable answer."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V6])
    with box_ssh._ipv4_only():
        assert socket.getaddrinfo("example.invalid", 443) == [V6]
