"""Security hardening: redirect schemes, body caps, limiter bounds, proxy IP, token reuse, config."""

import json
import os
import stat
from urllib.parse import urlencode

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from bosch_ebike_connect_classic.app import build_app, redirect_label, valid_redirect_uri
from bosch_ebike_connect_classic.client import API
from bosch_ebike_connect_classic.config import Config
from bosch_ebike_connect_classic.logutil import clean
from bosch_ebike_connect_classic.ratelimit import RateLimiter
from bosch_ebike_connect_classic.tools import is_id, is_numeric_id
from test_flow import REDIRECT, bosch_login_ok, call_tool, connect, pkce, register, tool_text

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def make(tmp_path, **kw):
    cfg = Config(secret="x" * 40, public_url="http://testserver", data_dir=tmp_path, **kw)
    return TestClient(build_app(cfg), base_url="http://testserver")


def test_redirect_scheme_rules():
    ok = ["http://localhost:3000/cb", "https://claude.ai/api/mcp/auth_callback",
          "cursor://anysphere.cursor-retrieval/oauth/callback", "vscode://x/y", "com.example.app:/cb"]
    bad = ["javascript:alert(1)", "http://evil.example/cb", "search-ms:query=x", "ms-msdt:/id",
           "ms-officecmd:x", "file:///etc/passwd", "mailto:a@b.ch", "foo://x/y", "https://a.example/cb#frag",
           "https://" + "a" * 3000 + ".example/cb", "https://a.example/\ncb"]
    assert all(valid_redirect_uri(u) for u in ok)
    assert not any(valid_redirect_uri(u) for u in bad)
    assert valid_redirect_uri("foo://x/y", schemes=("foo",))
    assert valid_redirect_uri("https://claude.ai/cb", hosts=("claude.ai",))
    assert not valid_redirect_uri("https://other.example/cb", hosts=("claude.ai",))
    assert redirect_label("https://claude.ai/cb") == "claude.ai"
    assert redirect_label("cursor://anysphere/cb") == "cursor://anysphere"


def test_sign_in_page_shows_redirect_target_and_unverified_notice(tmp_path):
    with make(tmp_path) as c:
        cid = register(c)
        _, challenge = pkce()
        r = c.get("/oauth/authorize", params=dict(client_id=cid, redirect_uri=REDIRECT, response_type="code",
                                                  code_challenge=challenge, code_challenge_method="S256"))
        assert "sent to <b>localhost:3000</b>" in r.text and "not verified" in r.text


def test_client_name_is_sanitised_and_registration_capped(tmp_path):
    with make(tmp_path, max_clients=2) as c:
        r = c.post("/oauth/register", json={"redirect_uris": [REDIRECT], "client_name": "A\nINFO fake\r line"})
        assert r.status_code == 201 and "\n" not in r.json()["client_name"] and "\r" not in r.json()["client_name"]
        assert c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code == 201
        assert c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code == 503


def test_oversized_bodies_rejected_before_parsing(tmp_path):
    with make(tmp_path, max_body_bytes=2048, max_mcp_body_bytes=4096) as c:
        big = {"redirect_uris": [REDIRECT], "client_name": "x" * 5000}
        assert c.post("/oauth/register", json=big).status_code == 413
        assert c.post("/oauth/token", content="a=" + "b" * 5000, headers=FORM).status_code == 413
        assert c.post("/mcp", content=b"x" * 5000, headers={"Content-Type": "application/json"}).status_code == 413
        # chunked upload without Content-Length is capped as well
        def gen():
            for _ in range(10):
                yield b"x" * 1000
        assert c.post("/oauth/register", content=gen()).status_code == 413
        assert c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code == 201


def test_rate_limiter_is_bounded_and_allowed_does_not_count():
    rl = RateLimiter(2, 60, max_keys=3)
    assert rl.allowed("a") and rl.allowed("a")  # asking never consumes
    assert rl.hit("a") and rl.hit("a") and not rl.hit("a") and not rl.allowed("a")
    assert rl.hit("b") and rl.hit("c")
    assert not rl.hit("d")  # table full: unknown keys are refused instead of growing memory
    assert len(rl._hits) == 3
    assert not rl.hit("a")  # known keys keep working with their own budget


def test_limits_come_from_config(tmp_path):
    with make(tmp_path, rate_register_ip=1) as c:
        assert c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code == 201
        assert c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code == 429


def test_forged_forwarded_headers_do_not_bypass_ip_limit(tmp_path):
    body = {"redirect_uris": [REDIRECT]}
    # xff mode, one trusted proxy: the rightmost entry is what the proxy saw; leftmost is forged
    with make(tmp_path, proxy_mode="xff", proxy_hops=1, rate_register_ip=2) as c:
        for i in range(2):
            h = {"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.7"}
            assert c.post("/oauth/register", json=body, headers=h).status_code == 201
        h = {"X-Forwarded-For": "10.9.9.99, 203.0.113.7", "CF-Connecting-IP": "1.2.3.4"}
        assert c.post("/oauth/register", json=body, headers=h).status_code == 429
        h = {"X-Forwarded-For": "10.9.9.99, 198.51.100.1"}  # a different real client is not limited
        assert c.post("/oauth/register", json=body, headers=h).status_code == 201


def test_cloudflare_mode_ignores_xff_and_proxy_off_ignores_both(tmp_path):
    body = {"redirect_uris": [REDIRECT]}
    with make(tmp_path, proxy_mode="cloudflare", rate_register_ip=1) as c:
        assert c.post("/oauth/register", json=body, headers={"CF-Connecting-IP": "1.1.1.1"}).status_code == 201
        assert c.post("/oauth/register", json=body, headers={"CF-Connecting-IP": "1.1.1.1",
                                                              "X-Forwarded-For": "9.9.9.9"}).status_code == 429
        assert c.post("/oauth/register", json=body, headers={"CF-Connecting-IP": "2.2.2.2"}).status_code == 201
    with make(tmp_path / "off", rate_register_ip=1) as c:
        assert c.post("/oauth/register", json=body, headers={"CF-Connecting-IP": "1.1.1.1"}).status_code == 201
        assert c.post("/oauth/register", json=body, headers={"CF-Connecting-IP": "2.2.2.2"}).status_code == 429


@respx.mock
def test_only_failed_sign_ins_lock_an_account(tmp_path):
    with make(tmp_path, rate_login_account=2, rate_login_ip=50) as c:
        cid = register(c)
        respx.post(f"{API}/portal/login/public").mock(return_value=httpx.Response(401))
        for _ in range(2):
            _, r = connect(c, cid, "v@b.ch", "wrong", expect_ok=False)
            assert "Bosch" in r.text or r.status_code == 200
        _, r = connect(c, cid, "v@b.ch", "wrong", expect_ok=False)
        assert r.status_code == 429
        # successful attempts of another user are not affected
        bosch_login_ok("c")
        tokens, _ = connect(c, cid, "other@b.ch")
        assert tokens["access_token"]


@respx.mock
def test_successful_sign_ins_do_not_count_against_the_account(tmp_path):
    with make(tmp_path, rate_login_account=1, rate_login_ip=50) as c:
        cid = register(c)
        bosch_login_ok("c")
        for _ in range(3):
            connect(c, cid, "same@b.ch")


@respx.mock
def test_replayed_refresh_token_revokes_the_family(tmp_path):
    with make(tmp_path) as c:
        cid = register(c)
        bosch_login_ok("c")
        t, _ = connect(c, cid, "a@b.ch")
        mk = lambda rt: c.post("/oauth/token", content=urlencode({
            "grant_type": "refresh_token", "refresh_token": rt, "client_id": cid}), headers=FORM)
        new = mk(t["refresh_token"])
        assert new.status_code == 200
        assert mk(t["refresh_token"]).status_code == 400          # replay of the spent token...
        assert mk(new.json()["refresh_token"]).status_code == 400  # ...kills the rotated one too
        respx.get(f"{API}/user/profile").mock(return_value=httpx.Response(200, json={}))
        assert call_tool(c, new.json()["access_token"], "get_user_profile").status_code == 401


@respx.mock
def test_other_spelling_of_the_same_email_revokes_old_tokens(tmp_path):
    with make(tmp_path) as c:
        cid = register(c)
        bosch_login_ok("c")
        first, _ = connect(c, cid, "Anna@b.ch")
        connect(c, cid, "Anna@b.ch")  # same spelling: first tokens stay valid
        respx.get(f"{API}/user/profile").mock(return_value=httpx.Response(200, json={"ok": 1}))
        assert call_tool(c, first["access_token"], "get_user_profile").status_code == 200
        connect(c, cid, "anna@b.ch")  # different spelling: could be another Bosch account
        assert call_tool(c, first["access_token"], "get_user_profile").status_code == 401


def test_non_ascii_csrf_and_challenge_give_clean_errors(tmp_path):
    with make(tmp_path) as c:
        cid = register(c)
        _, challenge = pkce()
        q = dict(client_id=cid, redirect_uri=REDIRECT, response_type="code",
                 code_challenge=challenge, code_challenge_method="S256")
        c.get("/oauth/authorize", params=q)
        r = c.post("/oauth/authorize", content=urlencode({**q, "csrf": "é€", "email": "a@b.ch", "password": "x"}),
                   headers=FORM, follow_redirects=False)
        assert r.status_code == 400
        q["code_challenge"] = "é" * 43
        c.get("/oauth/authorize", params=q)
        r = c.post("/oauth/token", content=urlencode({"grant_type": "authorization_code", "code": "nope",
                   "client_id": cid, "redirect_uri": REDIRECT, "code_verifier": "é" * 50}), headers=FORM)
        assert r.status_code == 400


def test_database_files_are_owner_only(tmp_path):
    with make(tmp_path / "d") as c:
        register(c)
        for name in ("gateway.sqlite3", "gateway.sqlite3-wal", "gateway.sqlite3-shm"):
            p = tmp_path / "d" / name
            if p.exists():
                assert stat.S_IMODE(p.stat().st_mode) == 0o600, name


def test_input_validation_helpers():
    assert is_id("abc-12_X") and not is_id("abc\n") and not is_id("a/b") and not is_id("")
    assert is_numeric_id("123") and not is_numeric_id("١٢٣") and not is_numeric_id("1" * 21) and not is_numeric_id("")
    assert clean("a\nb\r\x1bc") == "a b  c" and len(clean("x" * 500)) == 100


@respx.mock
def test_search_rides_rejects_non_finite_filters(tmp_path):
    with make(tmp_path) as c:
        cid = register(c)
        bosch_login_ok("c")
        t, _ = connect(c, cid, "a@b.ch")
        raw = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_rides",'
               '"arguments":{"min_distance_km":Infinity}}}')
        r = c.post("/mcp", content=raw, headers={"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json", "Authorization": f"Bearer {t['access_token']}"})
        assert r.status_code == 200
        assert "finite" in tool_text(r) or r.json()["result"]["isError"]
        r = call_tool(c, t["access_token"], "get_activity_details", {"activity_id": "١٢٣"})
        assert "numeric" in tool_text(r)


def test_config_limits_from_env_and_validation(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("RATE_", "MAX_", "PROXY_", "ALLOW", "TRUST_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GATEWAY_SECRET", "s" * 40)
    monkeypatch.setenv("RATE_MCP_USER", "7")
    monkeypatch.setenv("MAX_BODY_BYTES", "4096")
    monkeypatch.setenv("TRUST_PROXY", "cloudflare")
    monkeypatch.setenv("ALLOWED_REDIRECT_HOSTS", "Claude.ai, chatgpt.com")
    cfg = Config.from_env()
    assert (cfg.rate_mcp_user, cfg.max_body_bytes, cfg.proxy_mode) == (7, 4096, "cloudflare")
    assert cfg.allowed_redirect_hosts == ("claude.ai", "chatgpt.com") and cfg.rate_login_ip == 10
    monkeypatch.setenv("RATE_MCP_USER", "lots")
    with pytest.raises(SystemExit):
        Config.from_env()
    monkeypatch.setenv("RATE_MCP_USER", "7")
    monkeypatch.setenv("PUBLIC_URL", "http://ebike.example.com")
    with pytest.raises(SystemExit):
        Config.from_env()  # plain http on a public name: refused
    monkeypatch.setenv("ALLOW_INSECURE_HTTP", "1")
    assert Config.from_env().public_url == "http://ebike.example.com"
