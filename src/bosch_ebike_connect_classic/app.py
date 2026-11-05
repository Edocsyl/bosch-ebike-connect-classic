"""Starlette app: OAuth 2.1 authorization server (DCR + PKCE S256) + protected MCP."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from . import pages
from .client import AuthRequired, EBikeError, login
from .config import Config
from .logutil import clean, log, mask_email
from .ratelimit import RateLimiter
from .store import Store, normalize_account_key
from .tools import create_mcp, current_account

SEC_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action *; frame-ancestors 'none'",
}
BLOCKED_SCHEMES = {
    "javascript", "data", "file", "vbscript", "about", "blob", "ftp", "ftps", "sftp", "ssh", "telnet",
    "smb", "nfs", "ldap", "gopher", "mailto", "tel", "sms", "intent", "jar", "view-source", "chrome",
    "chrome-extension", "search", "search-ms", "ws", "wss",
}
# Desktop / IDE clients that finish OAuth through their own URL scheme. Other custom schemes are
# accepted only in reverse-DNS form (contains a dot, e.g. com.example.app) or when the operator
# lists them in ALLOWED_REDIRECT_SCHEMES. OS protocol handlers (ms-*, search-ms, ...) never are.
KNOWN_APP_SCHEMES = {"cursor", "vscode", "vscode-insiders", "vscodium", "windsurf", "claude", "zed",
                     "raycast", "chatgpt"}
LOOPBACK = {"localhost", "127.0.0.1", "[::1]", "::1"}
MAX_URI_LEN = 2048


def valid_redirect_uri(uri: str, hosts: tuple[str, ...] = (), schemes: tuple[str, ...] = ()) -> bool:
    if len(uri) > MAX_URI_LEN or any(not c.isprintable() for c in uri):
        return False
    try:
        u = urlsplit(uri)
    except ValueError:
        return False
    scheme = u.scheme.lower()
    if not scheme or scheme in BLOCKED_SCHEMES or scheme.startswith("ms-") or u.fragment:
        return False
    if scheme == "http":
        return (u.hostname or "") in LOOPBACK
    if scheme == "https":
        return bool(u.hostname) and (not hosts or u.hostname.lower() in hosts)
    return scheme in KNOWN_APP_SCHEMES or scheme in schemes or "." in scheme


def redirect_label(uri: str) -> str:
    """Where the browser is sent after sign-in, shown on the sign-in page."""
    u = urlsplit(uri)
    return u.netloc if u.scheme in ("http", "https") else f"{u.scheme}://{u.netloc}".rstrip("/")


def pkce_ok(verifier: str, challenge: str) -> bool:
    if not (43 <= len(verifier) <= 128):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest.encode(), challenge.encode("utf-8", "replace"))


def add_query(uri: str, **params: str) -> str:
    u = urlsplit(uri)
    q = parse_qsl(u.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ""))


def _json(data: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


def _html(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(body, status_code=status, headers=SEC_HEADERS)


class BodyLimit:
    """Reject oversized request bodies before any handler reads them (memory DoS). The body is
    buffered up to the limit and replayed, so chunked uploads are covered as well."""

    def __init__(self, app, oauth_limit: int, mcp_limit: int) -> None:
        self.app, self.oauth_limit, self.mcp_limit = app, oauth_limit, mcp_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        limit = self.mcp_limit if path == "/mcp" or path.startswith("/mcp/") else self.oauth_limit
        declared = dict(scope["headers"]).get(b"content-length", b"")
        chunks, size = [], 0
        too_big = declared.isdigit() and int(declared) > limit
        while not too_big:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body = msg.get("body", b"")
            size += len(body)
            if size > limit:
                too_big = True
                break
            chunks.append(body)
            if not msg.get("more_body"):
                break
        if too_big:
            log.warning("request body over %d bytes rejected (%s)", limit, clean(path))
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json")]})
            return await send({"type": "http.response.body", "body": b'{"error":"payload_too_large"}'})
        replay = iter([{"type": "http.request", "body": b"".join(chunks), "more_body": False}])

        async def replay_receive():
            try:
                return next(replay)
            except StopIteration:
                return await receive()  # waits for disconnect

        await self.app(scope, replay_receive, send)


class Cors:
    """Minimal CORS for browser-based MCP clients. Discovery, registration and token endpoints
    carry no cookies and are public, so they answer any origin. /mcp only answers origins on
    the allow-list (the Origin check in McpGuard enforces the same list)."""

    PUBLIC = ("/.well-known/", "/oauth/register", "/oauth/token")
    EXPOSE = "WWW-Authenticate, Mcp-Session-Id, MCP-Protocol-Version"

    def __init__(self, app, allowed: frozenset[str], allow_loopback: bool) -> None:
        self.app, self.allowed, self.allow_loopback = app, allowed, allow_loopback

    def _allow(self, path: str, origin: str) -> str | None:
        if path.startswith(self.PUBLIC):
            return "*"
        if path == "/mcp" or path.startswith("/mcp/"):
            if origin in self.allowed:
                return origin
            if self.allow_loopback and (urlsplit(origin).hostname or "") in LOOPBACK:
                return origin
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hdrs = dict(scope["headers"])
        origin = hdrs.get(b"origin", b"").decode("latin-1")
        allow = self._allow(scope["path"], origin) if origin else None
        if not allow:
            return await self.app(scope, receive, send)
        cors = [(b"access-control-allow-origin", allow.encode()),
                (b"access-control-expose-headers", self.EXPOSE.encode()), (b"vary", b"Origin")]
        if scope["method"] == "OPTIONS":
            cors += [(b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
                     (b"access-control-allow-headers",
                      b"Authorization, Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version, Mcp-Method, Mcp-Name, Last-Event-ID"),
                     (b"access-control-max-age", b"600")]
            await send({"type": "http.response.start", "status": 204, "headers": cors})
            return await send({"type": "http.response.body", "body": b""})

        async def send_cors(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *cors]}
            await send(message)

        await self.app(scope, receive, send_cors)


class McpGuard:
    """ASGI guard: require a valid Bearer token, bind the user for the tool call."""

    def __init__(self, app, store: Store, limiter: RateLimiter, metadata_url: str,
                 allowed_origins: frozenset[str] = frozenset(), allow_loopback: bool = False) -> None:
        self.app, self.store, self.limiter, self.metadata_url = app, store, limiter, metadata_url
        self.allowed_origins, self.allow_loopback = allowed_origins, allow_loopback

    def _origin_ok(self, origin: str) -> bool:
        """DNS-rebinding protection (MCP transport spec): a browser Origin must be ours."""
        if origin in self.allowed_origins:
            return True
        if self.allow_loopback:
            try:
                return (urlsplit(origin).hostname or "") in LOOPBACK
            except ValueError:
                return False
        return False

    async def _deny(self, send, status: int, www: str | None, body: bytes) -> None:
        headers = [(b"content-type", b"application/json")]
        if www:
            headers.append((b"www-authenticate", www.encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hdrs = dict(scope["headers"])
        origin = hdrs.get(b"origin", b"").decode("latin-1")
        if origin and not self._origin_ok(origin):
            log.warning("mcp: rejected foreign origin %s", clean(origin))
            return await self._deny(send, 403, None, b'{"error":"forbidden_origin"}')
        auth = hdrs.get(b"authorization", b"").decode("latin-1")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        account = self.store.lookup_access(token) if token else None
        if not account:
            log.debug("mcp: no or invalid bearer token -> 401 challenge")
            www = f'Bearer resource_metadata="{self.metadata_url}"'
            return await self._deny(send, 401, www, b'{"error":"invalid_token"}')
        if not self.limiter.hit(account):
            log.warning("mcp: rate limit exceeded for account %s", mask_email(account))
            return await self._deny(send, 429, None, b'{"error":"rate_limited"}')
        ctx = current_account.set(account)
        try:
            await self.app(scope, receive, send)
        finally:
            current_account.reset(ctx)


def build_app(config: Config) -> Starlette:
    store = Store(config.db_path, config.secret)
    mcp = create_mcp(store, config.host, config.port)
    mcp_app = mcp.streamable_http_app()

    login_ip = RateLimiter(config.rate_login_ip, 600)         # sign-in attempts per IP
    login_acct = RateLimiter(config.rate_login_account, 600)  # FAILED sign-ins per e-mail (protects the Bosch account)
    register_ip = RateLimiter(config.rate_register_ip, 3600)
    token_ip = RateLimiter(config.rate_token_ip, 60)
    mcp_limit = RateLimiter(config.rate_mcp_user, 60)         # MCP calls per user

    issuer = config.public_url
    metadata_url = f"{issuer}/.well-known/oauth-protected-resource"
    resource = f"{issuer}/mcp"
    iparts = urlsplit(issuer)
    allowed_origins = frozenset({f"{iparts.scheme}://{iparts.netloc}", *config.allowed_origins})
    allow_loopback = (iparts.hostname or "") in LOOPBACK

    def client_ip(request: Request) -> str:
        h = request.headers
        ip = ""
        if config.proxy_mode == "cloudflare":
            ip = h.get("cf-connecting-ip", "").strip()
        elif config.proxy_mode == "xff":
            # each trusted proxy appends the address it saw; the entry PROXY_HOPS from the right is the
            # real client. Anything further left is client-supplied and can be forged.
            hops = [x.strip() for x in h.get("x-forwarded-for", "").split(",") if x.strip()]
            if len(hops) >= config.proxy_hops:
                ip = hops[-config.proxy_hops]
        return ip[:64] or (request.client.host if request.client else "unknown")

    def lang_of(request: Request) -> str:
        return pages.pick_lang(request.headers.get("accept-language", ""))

    # --- discovery -----------------------------------------------------------------
    async def as_metadata(request: Request) -> Response:
        return _json({
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "authorization_response_iss_parameter_supported": True,
        })

    async def resource_metadata(request: Request) -> Response:
        return _json({"resource": resource, "authorization_servers": [issuer],
                      "bearer_methods_supported": ["header"]})

    # --- dynamic client registration (RFC 7591) -----------------------------------------
    async def register(request: Request) -> Response:
        if not register_ip.hit(client_ip(request)):
            log.warning("oauth: registration rate limit exceeded (ip %s)", client_ip(request))
            return _json({"error": "rate_limited"}, 429)
        try:
            body = await request.json()
            uris = body["redirect_uris"]
            assert isinstance(uris, list) and 1 <= len(uris) <= 10
            assert all(isinstance(u, str) and valid_redirect_uri(
                u, config.allowed_redirect_hosts, config.allowed_redirect_schemes) for u in uris)
        except Exception:
            log.warning("oauth: registration rejected (invalid redirect uris)")
            return _json({"error": "invalid_redirect_uri"}, 400)
        if store.count_clients() >= config.max_clients:
            log.warning("oauth: client registration refused (MAX_CLIENTS reached)")
            return _json({"error": "temporarily_unavailable"}, 503)
        name = clean(body.get("client_name") or "")
        cid = store.create_client(name, uris)
        log.info("oauth: registered client '%s' (%d redirect uri)", name or "-", len(uris))
        return _json({"client_id": cid, "client_id_issued_at": int(time.time()), "client_name": name, "redirect_uris": uris,
                      "token_endpoint_auth_method": "none",
                      "grant_types": ["authorization_code", "refresh_token"],
                      "response_types": ["code"]}, 201)

    # --- authorization endpoint ---------------------------------------------------------
    def check_authorize_params(p: dict[str, str]):
        """Return (client, error). Errors here must NOT redirect (RFC 6749 4.1.2.1)."""
        client = store.get_client(p.get("client_id", ""))
        if not client:
            return None, "Unknown client."
        if p.get("redirect_uri") not in client["redirect_uris"]:
            return None, "redirect_uri does not match the registration."
        if p.get("response_type") != "code" or p.get("code_challenge_method") != "S256" \
                or not p.get("code_challenge"):
            return None, "Only the authorization code flow with PKCE (S256) is supported."
        if p.get("resource") and p["resource"].rstrip("/") != resource:  # RFC 8707
            return None, "Unknown resource: this server only issues tokens for its own /mcp endpoint."
        return client, ""

    AUTH_KEYS = ("client_id", "redirect_uri", "response_type", "code_challenge",
                 "code_challenge_method", "state", "resource")

    async def authorize(request: Request) -> Response:
        if request.method == "GET":
            params = {k: request.query_params.get(k, "") for k in AUTH_KEYS}
            client, err = check_authorize_params(params)
            if not client:
                return _html(pages.message_page(pages.T[lang_of(request)]["msg_invalid"], err, lang_of(request)), 400)
            log.debug("oauth: sign-in page for client '%s'", client["name"])
            csrf = secrets.token_urlsafe(24)
            resp = _html(pages.authorize_page(client_name=client["name"], params=params, csrf=csrf, lang=lang_of(request),
                                              redirect_to=redirect_label(params["redirect_uri"])))
            resp.set_cookie("csrf", csrf, httponly=True, samesite="strict",
                            secure=config.secure_cookies, max_age=900, path="/oauth/authorize")
            return resp

        raw = (await request.body())[:8192].decode("utf-8", "replace")
        form = dict(parse_qsl(raw, keep_blank_values=True))
        params = {k: form.get(k, "") for k in AUTH_KEYS}
        client, err = check_authorize_params(params)
        if not client:
            return _html(pages.message_page(pages.T[lang_of(request)]["msg_invalid"], err, lang_of(request)), 400)

        csrf_cookie = request.cookies.get("csrf", "")
        if not csrf_cookie or not hmac.compare_digest(csrf_cookie.encode(), form.get("csrf", "").encode("utf-8", "replace")):
            log.warning("oauth: sign-in rejected (csrf mismatch)")
            tl = pages.T[lang_of(request)]
            return _html(pages.message_page(tl["msg_expired"], tl["msg_expired_t"], lang_of(request)), 400)

        email = form.get("email", "").strip()
        password = form.get("password", "")

        tt = pages.T[lang_of(request)]

        def again(msg: str, status: int = 200) -> Response:
            resp = _html(pages.authorize_page(client_name=client["name"], params=params,
                                              csrf=csrf_cookie, error=msg, email=email,
                                              lang=lang_of(request),
                                              redirect_to=redirect_label(params["redirect_uri"])), status)
            return resp

        if "@" not in email or not password or len(email) > 254 or len(password) > 256:
            return again(tt["e_fields"])
        key = normalize_account_key(email)
        if not login_ip.hit(client_ip(request)) or not login_acct.allowed(key):
            log.warning("oauth: sign-in rate limit exceeded (ip %s, account %s)", client_ip(request), mask_email(key))
            return again(tt["e_many"], 429)

        try:
            remember = await asyncio.wait_for(login(email, password), timeout=30)
        except AuthRequired:
            login_acct.hit(key)  # only failures count, so strangers cannot lock a user out by just trying
            log.warning("oauth: sign-in rejected by eBike Connect (account %s)", mask_email(key))
            return again(tt["e_bad"])
        except (EBikeError, asyncio.TimeoutError, OSError) as e:
            log.error("oauth: eBike Connect not reachable during sign-in: %s", type(e).__name__)
            return again(tt["e_down"], 502)
        finally:
            del password

        try:
            prev = store.get_account(key)
        except Exception:
            prev = None
        if prev and prev.get("username") != email:
            # same normalised key, different spelling: could be another Bosch account. Do not let
            # tokens issued for the previous session read this one.
            log.warning("oauth: sign-in spelling differs from stored one, revoking tokens (%s)", mask_email(key))
            store.revoke_tokens(key)
        store.upsert_account(key, {"username": email, "remember": remember})
        log.info("oauth: signed in %s via '%s'", mask_email(key), client["name"] or "-")
        code = store.put_code(client["client_id"], key, params["redirect_uri"],
                              params["code_challenge"])
        target = add_query(params["redirect_uri"], code=code, state=params["state"], iss=issuer)
        resp = _html(pages.success_page(client_name=client["name"], email=email, target=target,
                                        lang=lang_of(request)))
        resp.delete_cookie("csrf", path="/oauth/authorize")
        return resp

    # --- token endpoint --------------------------------------------------------------------
    async def token(request: Request) -> Response:
        if not token_ip.hit(client_ip(request)):
            log.warning("oauth: token rate limit exceeded (ip %s)", client_ip(request))
            return _json({"error": "rate_limited"}, 429)
        raw = (await request.body())[:8192].decode("utf-8", "replace")
        if "json" in request.headers.get("content-type", ""):
            try:
                form = {k: str(v) for k, v in json.loads(raw).items()}
            except Exception:
                return _json({"error": "invalid_request"}, 400)
        else:
            form = dict(parse_qsl(raw))
        basic = request.headers.get("authorization", "")
        if basic[:6].lower() == "basic " and not form.get("client_id"):
            try:  # client_secret_basic: we have no secrets (public clients) but take the id
                form["client_id"] = base64.b64decode(basic[6:]).decode().split(":", 1)[0]
            except Exception:
                pass
        grant, client_id = form.get("grant_type"), form.get("client_id", "")
        if not store.get_client(client_id):
            return _json({"error": "invalid_client"}, 401)
        if form.get("resource") and form["resource"].rstrip("/") != resource:
            return _json({"error": "invalid_target"}, 400)

        if grant == "authorization_code":
            row = store.pop_code(form.get("code", ""))
            if (not row or row["client_id"] != client_id
                    or row["redirect_uri"] != form.get("redirect_uri")
                    or not pkce_ok(form.get("code_verifier", ""), row["challenge"])):
                log.warning("oauth: invalid authorization_code grant")
                return _json({"error": "invalid_grant"}, 400)
            log.debug("oauth: tokens issued (authorization_code) for %s", mask_email(row["account_key"]))
            return _json(store.issue_tokens(client_id, row["account_key"]))

        if grant == "refresh_token":
            tokens = store.rotate_refresh(form.get("refresh_token", ""), client_id)
            if not tokens:
                log.warning("oauth: invalid or reused refresh token")
                return _json({"error": "invalid_grant"}, 400)
            log.debug("oauth: tokens refreshed")
            return _json(tokens)

        return _json({"error": "unsupported_grant_type"}, 400)

    async def landing(request: Request) -> Response:
        return _html(pages.landing_page(issuer, lang_of(request)))

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async def reaper():
            while True:
                store.purge_expired()
                await asyncio.sleep(3600)

        task = asyncio.create_task(reaper())
        try:
            from importlib.metadata import version
            ver = version("bosch-ebike-connect-classic")
        except Exception:
            ver = "?"
        log.info("starting bosch-ebike-connect-classic %s: public_url=%s data_dir=%s log_level=%s proxy=%s",
                 ver, config.public_url, config.data_dir, config.log_level, config.proxy_mode or "off")
        if config.public_url.startswith("http://") and (iparts.hostname or "") not in LOOPBACK:
            log.warning("PUBLIC_URL is plain http on a non-local address: passwords travel in cleartext")
        try:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            task.cancel()

    routes = [
        Route("/", landing),
        Route("/healthz", healthz),
        Route("/.well-known/oauth-authorization-server", as_metadata),
        Route("/.well-known/oauth-authorization-server/mcp", as_metadata),
        Route("/.well-known/openid-configuration", as_metadata),
        Route("/.well-known/oauth-protected-resource", resource_metadata),
        Route("/.well-known/oauth-protected-resource/mcp", resource_metadata),
        Route("/oauth/register", register, methods=["POST"]),
        Route("/oauth/authorize", authorize, methods=["GET", "POST"]),
        Route("/oauth/token", token, methods=["POST"]),
        Mount("/", app=McpGuard(mcp_app, store, mcp_limit, metadata_url, allowed_origins, allow_loopback)),
    ]
    inner = Starlette(routes=routes, lifespan=lifespan)
    inner.state.store = store
    app = BodyLimit(Cors(inner, allowed_origins, allow_loopback), config.max_body_bytes, config.max_mcp_body_bytes)
    app.state = inner.state  # tests reach the store through app.state
    app.router = inner.router
    return app
