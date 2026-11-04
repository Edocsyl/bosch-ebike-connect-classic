"""Bosch eBike Connect (Classic) — multi-user remote MCP server."""

from __future__ import annotations


def main() -> None:
    import uvicorn

    from .app import build_app
    from .config import Config

    config = Config.from_env()
    uvicorn.run(build_app(config), host=config.host, port=config.port,
                proxy_headers=config.trust_proxy, forwarded_allow_ips="*" if config.trust_proxy else None,
                log_level="info")
