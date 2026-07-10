"""Optional egress proxy configuration."""

from __future__ import annotations

from nimmakai.balancer import KeyPool
from nimmakai.config import Settings
from nimmakai.upstream import UpstreamClient


def test_egress_proxy_from_list() -> None:
    s = Settings(
        nim_api_keys=["k"],
        nim_egress_proxies=["http://proxy.example:8080"],
    )
    assert s.egress_proxy_url() == "http://proxy.example:8080"


def test_upstream_stores_proxy() -> None:
    pool = KeyPool(api_keys=["k"], rpm_limit=10)
    client = UpstreamClient(
        "https://example.com/v1",
        pool,
        proxy_url="http://proxy.example:8080",
    )
    assert client.proxy_url == "http://proxy.example:8080"
