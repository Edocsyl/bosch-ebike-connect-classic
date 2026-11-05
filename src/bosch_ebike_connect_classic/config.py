from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .logutil import parse_level


@dataclass(frozen=True)
class Config:
    secret: str            # GATEWAY_SECRET: derives the AES-256-GCM key for stored sessions
    public_url: str        # PUBLIC_URL: externally visible base URL (OAuth issuer), no trailing slash
    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"      # LOG_LEVEL: DEBUG | INFO | WARNING | ERROR
    allowed_origins: tuple[str, ...] = ()   # extra browser origins for /mcp (browser-based MCP clients)
    proxy_mode: str = ""         # "" (off) | "xff" (X-Forwarded-For, PROXY_HOPS) | "cloudflare" (CF-Connecting-IP)
    proxy_hops: int = 1          # trusted proxies in front of the server (xff mode)
    allowed_redirect_hosts: tuple[str, ...] = ()    # if set, https redirect URIs must use one of these hosts
    allowed_redirect_schemes: tuple[str, ...] = ()  # extra custom schemes (besides the built-in client list)
    allow_insecure_http: bool = False   # permit a non-loopback http:// PUBLIC_URL (password over cleartext!)
    # rate limits (requests per window) and size caps; all overridable by environment
    rate_login_ip: int = 10          # sign-in attempts per IP / 10 min
    rate_login_account: int = 5      # FAILED sign-ins per e-mail / 10 min
    rate_register_ip: int = 20       # client registrations per IP / hour
    rate_token_ip: int = 60          # token requests per IP / minute
    rate_mcp_user: int = 120         # MCP calls per user / minute
    max_body_bytes: int = 16384      # request body cap for /oauth/*
    max_mcp_body_bytes: int = 1048576  # request body cap for /mcp
    max_clients: int = 10000         # registered OAuth clients (dynamic registration is open)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gateway.sqlite3"

    @property
    def trust_proxy(self) -> bool:
        return bool(self.proxy_mode)

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
        loopback = (urlsplit(public_url).hostname or "") in ("localhost", "127.0.0.1", "::1")
        insecure_ok = _flag("ALLOW_INSECURE_HTTP")
        if public_url.startswith("http://") and not loopback and not insecure_ok:
            raise SystemExit("PUBLIC_URL uses plain http:// on a non-local address: passwords would cross the "
                             "network in cleartext. Use https:// (TLS proxy) or set ALLOW_INSECURE_HTTP=1.")
        tp = os.environ.get("TRUST_PROXY", "").strip().lower()
        proxy_mode = "cloudflare" if tp == "cloudflare" else "xff" if tp in ("1", "true", "yes", "xff") else ""
        return cls(
            secret=secret,
            public_url=public_url,
            data_dir=data_dir,
            host=os.environ.get("HOST", "0.0.0.0"),
            port=port,
            log_level=parse_level(os.environ.get("LOG_LEVEL", "INFO")),
            proxy_mode=proxy_mode,
            proxy_hops=_int("PROXY_HOPS", 1, 1, 10),
            allowed_origins=_list("ALLOWED_ORIGINS", strip_slash=True),
            allowed_redirect_hosts=_list("ALLOWED_REDIRECT_HOSTS"),
            allowed_redirect_schemes=_list("ALLOWED_REDIRECT_SCHEMES"),
            allow_insecure_http=insecure_ok,
            rate_login_ip=_int("RATE_LOGIN_IP", 10),
            rate_login_account=_int("RATE_LOGIN_ACCOUNT", 5),
            rate_register_ip=_int("RATE_REGISTER_IP", 20),
            rate_token_ip=_int("RATE_TOKEN_IP", 60),
            rate_mcp_user=_int("RATE_MCP_USER", 120),
            max_body_bytes=_int("MAX_BODY_BYTES", 16384, 1024),
            max_mcp_body_bytes=_int("MAX_MCP_BODY_BYTES", 1048576, 1024),
            max_clients=_int("MAX_CLIENTS", 10000),
        )


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _int(name: str, default: int, minimum: int = 1, maximum: int = 10**9) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a whole number, got {raw!r}") from None
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _list(name: str, strip_slash: bool = False) -> tuple[str, ...]:
    items = (i.strip() for i in os.environ.get(name, "").split(","))
    return tuple((i.rstrip("/") if strip_slash else i.lower()) for i in items if i)


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
