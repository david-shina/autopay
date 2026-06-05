"""HTTP-related helpers used by multiple routers."""
from __future__ import annotations

import ipaddress

from fastapi import Request


def client_ip(request: Request) -> str | None:
    """Return the client's IP, or None if it isn't a valid IPv4/IPv6.

    The `audit_logs.ip_address` column is `INET` and rejects strings
    that don't parse. TestClient sends `"testclient"` (not a valid
    IP) — we drop that. Forwarded headers (`X-Forwarded-For`) are
    NOT trusted here; put a real reverse proxy in front of the app
    if you need them.
    """
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host
