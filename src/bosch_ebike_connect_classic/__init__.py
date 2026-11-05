"""Bosch eBike Connect (Classic) — multi-user remote MCP server."""

from __future__ import annotations


def main() -> None:
    import uvicorn

    from .app import build_app
    from .config import Config
    from .logutil import setup_logging

    config = Config.from_env()
    setup_logging(config.log_level)
    debug = config.log_level == "DEBUG"
    uvicorn.run(build_app(config), host=config.host, port=config.port,
                proxy_headers=config.trust_proxy, forwarded_allow_ips="*" if config.trust_proxy else None,
                log_config=None, log_level=config.log_level.lower(), access_log=debug)
