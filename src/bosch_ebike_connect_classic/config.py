from __future__ import annotations

import os
import secrets
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
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        port = int(os.environ.get("PORT", "8080"))
        secret = os.environ.get("GATEWAY_SECRET", "") or load_or_create_secret(data_dir)
        if len(secret) < 32:
            raise SystemExit("GATEWAY_SECRET must be at least 32 characters: openssl rand -base64 48 "
                             "(or leave it unset to generate one in the data folder)")
        public_url = (os.environ.get("PUBLIC_URL") or f"http://localhost:{port}").rstrip("/")
        if not public_url.startswith(("https://", "http://")):
            raise SystemExit("PUBLIC_URL must start with http:// or https://, e.g. https://ebike.example.com")
        return cls(
            secret=secret,
            public_url=public_url,
            data_dir=data_dir,
            host=os.environ.get("HOST", "0.0.0.0"),
            port=port,
            trust_proxy=os.environ.get("TRUST_PROXY", "") in ("1", "true", "yes"),
            allowed_origins=tuple(o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()),
        )


def load_or_create_secret(data_dir: Path) -> str:
    """Secret kept next to the database so the data folder is self-contained. Setting
    GATEWAY_SECRET in the environment instead keeps the key away from the data it protects."""
    path = data_dir / "gateway.secret"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(48))
    return path.read_text().strip()
