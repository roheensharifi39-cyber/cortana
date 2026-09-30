"""One shared HTTP client per process.

Creating an httpx client loads the TLS certificate bundle, which costs ~300 ms on some
machines. Reusing a single client keeps every call fast and reuses connections.
"""
import asyncio
import httpx

_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def client() -> httpx.AsyncClient:
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _client is None or _client.is_closed or _client_loop != loop:
        _client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
        _client_loop = loop
    return _client
