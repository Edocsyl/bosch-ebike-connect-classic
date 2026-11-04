from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    secret: str            # GATEWAY_SECRET: derives the AES-256-GCM key for stored sessions
    public_url: str        # PUBLIC_URL: externally visible base URL (OAuth issuer), no trailing slash
    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080
    trust_proxy: bool = False   # honour CF-Connecting-IP / X-Forwarded-For for rate limits
    allowed_origins: tuple[str, ...] = ()   # extra browser origins for /mcp (browser-based MCP clients)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gateway.sqlite3"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @classmethod
    def from_env(cls) -> "Config":
        secret = os.environ.get("GATEWAY_SECRET", "")
        if len(secret) < 32:
            raise SystemExit("GATEWAY_SECRET must be set (>= 32 chars): openssl rand -base64 48")
        public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
        if not public_url.startswith(("https://", "http://")):
            raise SystemExit("PUBLIC_URL must be set, e.g. https://ebike.example.com")
        return cls(
            secret=secret,
            public_url=public_url,
            data_dir=Path(os.environ.get("DATA_DIR", "/data")),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8080")),
            trust_proxy=os.environ.get("TRUST_PROXY", "") in ("1", "true", "yes"),
            allowed_origins=tuple(o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()),
        )
