"""Shared outbound HTTP settings (connect timeout 20 s, redirects followed)."""
from __future__ import annotations

import httpx


def client(timeout: float = 30.0, follow_redirects: bool = True) -> httpx.Client:
    return httpx.Client(follow_redirects=follow_redirects, timeout=httpx.Timeout(timeout, connect=20.0))
