"""End-to-end: DCR -> authorize (login) -> token -> MCP tool call, against a fake Bosch."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from bosch_ebike_connect_classic.app import build_app
from bosch_ebike_connect_classic.client import API
from bosch_ebike_connect_classic.config import Config

REDIRECT = "http://localhost:3000/cb"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def app(tmp_path):
    cfg = Config(secret="x" * 40, public_url="http://testserver", data_dir=tmp_path)
    return build_app(cfg)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://testserver") as c:
        yield c


def pkce():
    verifier = base64.urlsafe_b64encode(b"v" * 48).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client):
    r = client.post("/oauth/register", json={"redirect_uris": [REDIRECT], "client_name": "Test"})
    assert r.status_code == 201
    return r.json()["client_id"]


def redirect_target(r):
    """Sign-in ends on a success page that links back to the client (no direct 302)."""
    import html, re
    assert r.status_code == 200, r.text
    m = re.search(r'<a class="btn" href="([^"]+)"', r.text)
    assert m, r.text
    return html.unescape(m.group(1))


def connect(client, client_id, email, password="pw", expect_ok=True):
    """Run the whole sign-in; returns (tokens|None, response)."""
    verifier, challenge = pkce()
    q = dict(client_id=client_id, redirect_uri=REDIRECT, response_type="code",
             code_challenge=challenge, code_challenge_method="S256", state="st")
    page = client.get("/oauth/authorize", params=q)
    assert page.status_code == 200 and "Password" in page.text
    csrf = client.cookies.get("csrf")
    r = client.post("/oauth/authorize", content=urlencode({**q, "csrf": csrf, "email": email,
                    "password": password}), headers={"Content-Type": "application/x-www-form-urlencoded"},
                    follow_redirects=False)
    if not expect_ok:
        return None, r
    loc = urlsplit(redirect_target(r))
    qs = parse_qs(loc.query)
    assert qs["state"] == ["st"]
    t = client.post("/oauth/token", content=urlencode({
        "grant_type": "authorization_code", "code": qs["code"][0], "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": verifier}),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert t.status_code == 200, t.text
    return t.json(), r


def call_tool(client, access, name, args=None):
    r = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": args or {}}})
    return r


def tool_text(r):
    assert r.status_code == 200, r.text
    return r.json()["result"]["content"][0]["text"]


def bosch_login_ok(cookie):
    return respx.post(f"{API}/portal/login/public").mock(
        return_value=httpx.Response(200, headers={"set-cookie": f"REMEMBER={cookie}; Path=/"}))


@respx.mock
def test_full_flow_and_user_isolation(client):
    cid = register(client)
    bosch_login_ok("cookie-alice")
    alice, _ = connect(client, cid, "Alice@Example.com")
    respx.routes.clear()
    bosch_login_ok("cookie-bob")
    bob, _ = connect(client, cid, "bob@example.com")

    seen = []

    def bikes(request):
        seen.append(request.headers["cookie"])
        return httpx.Response(200, json={"my_ebikes": [{"cookie": request.headers["cookie"]}]})

    respx.get(f"{API}/portal/devices/my_ebikes").mock(side_effect=bikes)
    assert "cookie-alice" in tool_text(call_tool(client, alice["access_token"], "get_my_ebikes"))
    assert "cookie-bob" in tool_text(call_tool(client, bob["access_token"], "get_my_ebikes"))
    assert seen == ["REMEMBER=cookie-alice", "REMEMBER=cookie-bob"]


@respx.mock
def test_requires_bearer_with_challenge(client):
    r = client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


@respx.mock
def test_wrong_bosch_password_rerenders_form_and_stores_nothing(client, app):
    cid = register(client)
    respx.post(f"{API}/portal/login/public").mock(
        return_value=httpx.Response(401, json={"errors": [{"code": 1, "message": "bad"}]}))
    tokens, r = connect(client, cid, "eve@example.com", expect_ok=False)
    assert r.status_code == 200 and "not accepted" in r.text
    assert app.state.store.get_account("eve@example.com") is None


@respx.mock
def test_code_single_use_and_pkce_enforced(client):
    cid = register(client)
    bosch_login_ok("c")
    verifier, challenge = pkce()
    q = dict(client_id=cid, redirect_uri=REDIRECT, response_type="code",
             code_challenge=challenge, code_challenge_method="S256", state="")
    client.get("/oauth/authorize", params=q)
    r = client.post("/oauth/authorize", content=urlencode({**q, "csrf": client.cookies.get("csrf"),
                    "email": "a@b.ch", "password": "x"}),
                    headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    code = parse_qs(urlsplit(redirect_target(r)).query)["code"][0]
    base = {"grant_type": "authorization_code", "code": code, "client_id": cid, "redirect_uri": REDIRECT}
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    bad = client.post("/oauth/token", content=urlencode({**base, "code_verifier": "w" * 50}), headers=form)
    assert bad.status_code == 400
    # a failed attempt burns the code
    ok = client.post("/oauth/token", content=urlencode({**base, "code_verifier": verifier}), headers=form)
    assert ok.status_code == 400


def test_authorize_rejects_unregistered_redirect_and_csrf(client):
    cid = register(client)
    _, challenge = pkce()
    q = dict(client_id=cid, redirect_uri="https://evil.example/cb", response_type="code",
             code_challenge=challenge, code_challenge_method="S256")
    assert client.get("/oauth/authorize", params=q).status_code == 400
    q["redirect_uri"] = REDIRECT
    client.get("/oauth/authorize", params=q)
    r = client.post("/oauth/authorize", content=urlencode({**q, "csrf": "wrong", "email": "a@b.ch",
                    "password": "x"}), headers={"Content-Type": "application/x-www-form-urlencoded"},
                    follow_redirects=False)
    assert r.status_code == 400


def test_register_rejects_dangerous_redirect(client):
    r = client.post("/oauth/register", json={"redirect_uris": ["javascript:alert(1)"]})
    assert r.status_code == 400
    r = client.post("/oauth/register", json={"redirect_uris": ["http://evil.example/cb"]})
    assert r.status_code == 400


@respx.mock
def test_expired_bosch_session_forces_reauth(client):
    cid = register(client)
    bosch_login_ok("c")
    tokens, _ = connect(client, cid, "a@b.ch")
    respx.get(f"{API}/portal/devices/my_ebikes").mock(return_value=httpx.Response(401, json={"errors": []}))
    assert "expired" in tool_text(call_tool(client, tokens["access_token"], "get_my_ebikes"))
    assert call_tool(client, tokens["access_token"], "get_my_ebikes").status_code == 401


@respx.mock
def test_refresh_rotation_and_cookie_rotation_persisted(client, app):
    cid = register(client)
    bosch_login_ok("old")
    tokens, _ = connect(client, cid, "a@b.ch")
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    new = client.post("/oauth/token", content=urlencode({
        "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": cid}), headers=form)
    assert new.status_code == 200
    replay = client.post("/oauth/token", content=urlencode({
        "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": cid}), headers=form)
    assert replay.status_code == 400

    respx.get(f"{API}/user/profile").mock(return_value=httpx.Response(
        200, json={"ok": 1}, headers={"set-cookie": "REMEMBER=rotated; Path=/"}))
    assert json.loads(tool_text(call_tool(client, new.json()["access_token"], "get_user_profile"))) == {"ok": 1}
    assert app.state.store.get_account("a@b.ch")["remember"] == "rotated"


@respx.mock
def test_export_sends_required_timezone_offset(client):
    cid = register(client)
    bosch_login_ok("c")
    tokens, _ = connect(client, cid, "a@b.ch")
    route = respx.get(f"{API}/gpx/export/ride/42").mock(return_value=httpx.Response(200, text="<gpx/>"))
    out = tool_text(call_tool(client, tokens["access_token"], "export_activity",
                              {"activity_id": "42", "format": "gpx", "timezone": "Asia/Kolkata"}))
    assert out == "<gpx/>"
    assert route.calls[0].request.url.params["timezone"] == "330"   # minutes east of UTC
    bad = tool_text(call_tool(client, tokens["access_token"], "export_activity",
                              {"activity_id": "42", "timezone": "Nope/Zone"}))
    assert bad.startswith("Error: unknown time zone")


@respx.mock
def test_new_read_tools_hit_expected_endpoints(client):
    cid = register(client)
    bosch_login_ok("c")
    tokens, _ = connect(client, cid, "a@b.ch")
    acc = tokens["access_token"]

    rides = respx.get(f"{API}/portal/activities/ride/headers").mock(
        return_value=httpx.Response(200, json={"bike_ride_header_dtos": []}))
    tool_text(call_tool(client, acc, "search_rides",
                        {"title": "Rhein", "min_distance_km": 5, "max_duration_min": 1.5, "max_items": 7}))
    p = rides.calls[0].request.url.params
    # the portal rejects decimals ("For input string: 1480.0"): whole metres / milliseconds
    assert p["title"] == "Rhein" and p["minTotalDistance"] == "5000" and p["max"] == "7"
    assert p["maxDrivingTime"] == "90000"
    assert "maxTotalDistance" not in p and "minDrivingTime" not in p   # unset filters are omitted

    respx.get(f"{API}/portal/navigation/my_items/destinations").mock(
        return_value=httpx.Response(200, json={"destinations": []}))
    assert json.loads(tool_text(call_tool(client, acc, "get_saved_places"))) == {"destinations": []}

    w = respx.get(f"{API}/portal/weather/forecast/hourly/").mock(return_value=httpx.Response(200, json=[]))
    tool_text(call_tool(client, acc, "get_weather_forecast",
                        {"latitude": 47.376887, "longitude": 8.541694, "period": "hourly"}))
    assert dict(w.calls[0].request.url.params) == {"latitude": "47.38", "longitude": "8.54"}
    assert tool_text(call_tool(client, acc, "get_weather_forecast",
                               {"latitude": 99, "longitude": 0})).startswith("Error")

    s = respx.get(f"{API}/portal/dashboard/stream").mock(return_value=httpx.Response(200, json={"stream": []}))
    tool_text(call_tool(client, acc, "get_dashboard_feed", {"count": 3}))
    q = s.calls[0].request.url.params
    assert q["count"] == "3" and q.get_list("types") == ["news", "activities"]

    s2 = respx.get(f"{API}/settings").mock(return_value=httpx.Response(200, json={"settings": {}}))
    tool_text(call_tool(client, acc, "get_user_settings"))
    assert s2.calls[0].request.url.params["filter"] == "user"

    assert tool_text(call_tool(client, acc, "get_route_details", {"route_id": "../x"})).startswith("Error")
    respx.get(f"{API}/portal/navigation/my_items/route_details/r1").mock(
        return_value=httpx.Response(200, json={"id": "r1"}))
    assert json.loads(tool_text(call_tool(client, acc, "get_route_details", {"route_id": "r1"}))) == {"id": "r1"}


@respx.mock
def test_get_integrations_reports_partial_failures(client):
    cid = register(client)
    bosch_login_ok("c")
    tokens, _ = connect(client, cid, "a@b.ch")
    respx.get(f"{API}/portal/strava/connect_status").mock(return_value=httpx.Response(200, json={"value": 1}))
    respx.get(f"{API}/portal/strava/upload_option").mock(return_value=httpx.Response(200, json={"option": "AUTO"}))
    respx.get(f"{API}/portal/komoot/connect_status").mock(
        return_value=httpx.Response(200, json={"value": 0, "last_sync": 5}))
    respx.get(f"{API}/portal/komoot/upload_option").mock(return_value=httpx.Response(500, json={"errors": []}))
    out = json.loads(tool_text(call_tool(client, tokens["access_token"], "get_integrations")))
    assert out["strava_status"] == {"value": 1} and out["komoot_status"]["last_sync"] == 5
    assert out["komoot_upload_option"].startswith("error:")


@respx.mock
def test_disconnect_requires_confirm_and_only_removes_own_data(client, app):
    cid = register(client)
    bosch_login_ok("cookie-alice")
    alice, _ = connect(client, cid, "alice@example.com")
    respx.routes.clear()
    bosch_login_ok("cookie-bob")
    bob, _ = connect(client, cid, "bob@example.com")
    logout = respx.post(f"{API}/portal/logout/public").mock(return_value=httpx.Response(200, json={}))

    # without confirm: described, nothing changes
    msg = tool_text(call_tool(client, alice["access_token"], "disconnect_account"))
    assert msg.startswith("Nothing was changed") and not logout.called
    assert app.state.store.get_account("alice@example.com") is not None

    # with confirm: portal sign-out with alice's cookie, then local delete
    msg = tool_text(call_tool(client, alice["access_token"], "disconnect_account", {"confirm": True}))
    assert msg.startswith("Disconnected. Logout request sent to ebike-connect.com")
    assert logout.calls[0].request.headers["cookie"] == "REMEMBER=cookie-alice"
    assert app.state.store.get_account("alice@example.com") is None
    assert call_tool(client, alice["access_token"], "get_my_ebikes").status_code == 401   # token gone
    # bob untouched
    assert app.state.store.get_account("bob@example.com")["remember"] == "cookie-bob"
    respx.get(f"{API}/portal/devices/my_ebikes").mock(return_value=httpx.Response(200, json={"ok": 1}))
    assert json.loads(tool_text(call_tool(client, bob["access_token"], "get_my_ebikes"))) == {"ok": 1}


@respx.mock
def test_disconnect_deletes_locally_even_if_portal_fails(client, app):
    cid = register(client)
    bosch_login_ok("c")
    tokens, _ = connect(client, cid, "a@b.ch")
    respx.post(f"{API}/portal/logout/public").mock(return_value=httpx.Response(500, json={"errors": []}))
    msg = tool_text(call_tool(client, tokens["access_token"], "disconnect_account", {"confirm": True}))
    assert "sign-out failed" in msg and "may stay valid" in msg
    assert app.state.store.get_account("a@b.ch") is None


@respx.mock
def test_disconnect_reports_already_expired_session(client, app):
    cid = register(client)
    bosch_login_ok("c")
    tokens, _ = connect(client, cid, "a@b.ch")
    respx.post(f"{API}/portal/logout/public").mock(return_value=httpx.Response(401, json={"errors": []}))
    msg = tool_text(call_tool(client, tokens["access_token"], "disconnect_account", {"confirm": True}))
    assert "already expired" in msg and app.state.store.get_account("a@b.ch") is None


def test_blob_encrypted_at_rest(client, app, tmp_path):
    app.state.store.upsert_account("a@b.ch", {"username": "a@b.ch", "remember": "SECRET-COOKIE"})
    raw = (tmp_path / "gateway.sqlite3").read_bytes()
    assert b"SECRET-COOKIE" not in raw
    assert app.state.store.get_account("a@b.ch")["remember"] == "SECRET-COOKIE"


@respx.mock
def test_spec_hardening_origin_resource_iss_iserror(client):
    cid = register(client)
    bosch_login_ok("cookie-spec")
    tokens, resp = connect(client, cid, "spec@example.com")
    acc = tokens["access_token"]
    # RFC 9207: iss on the authorization response + advertised in metadata
    assert "iss=" in redirect_target(resp)
    assert client.get("/.well-known/oauth-authorization-server").json()[
        "authorization_response_iss_parameter_supported"] is True
    # Origin validation: foreign browser origin rejected, own/no origin accepted
    hdr = {**MCP_HEADERS, "Authorization": f"Bearer {acc}"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/mcp", headers={**hdr, "Origin": "https://evil.example"}, json=body).status_code == 403
    assert client.post("/mcp", headers=hdr, json=body).status_code == 200
    # RFC 8707: foreign resource rejected at authorize; own accepted
    q = dict(client_id=cid, redirect_uri=REDIRECT, response_type="code", code_challenge=pkce()[1],
             code_challenge_method="S256", state="s")
    assert client.get("/oauth/authorize", params={**q, "resource": "https://other.example/mcp"}).status_code == 400
    assert client.get("/oauth/authorize", params={**q, "resource": "http://testserver/mcp"}).status_code == 200
    # isError flag on tool errors
    r = call_tool(client, acc, "get_route_details", {"route_id": "../x"})
    assert r.json()["result"]["isError"] is True


@respx.mock
def test_success_page_explains_logout_and_generic_client_support(client):
    cid = register(client)
    bosch_login_ok("cookie-ui")
    _, resp = connect(client, cid, "ui@example.com")
    assert "disconnect_account" in resp.text and "ui@example.com" in resp.text
    assert "http-equiv=\"refresh\"" in resp.text and "change your password" in resp.text.lower()
    # sign-in page is English and lists what is read
    page = client.get("/oauth/authorize", params=dict(
        client_id=cid, redirect_uri=REDIRECT, response_type="code", code_challenge=pkce()[1],
        code_challenge_method="S256", state="x"))
    assert "Connect your eBike Connect account" in page.text and "Read-only" in page.text
    # OIDC-style discovery alias + CORS for browser clients on public endpoints
    assert client.get("/.well-known/openid-configuration").json()["issuer"] == "http://testserver"
    pre = client.options("/oauth/token", headers={"Origin": "https://app.example",
                         "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "*"
    # /mcp stays closed to foreign origins even for preflight
    pre = client.options("/mcp", headers={"Origin": "https://app.example",
                         "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers
    # token endpoint accepts JSON bodies and client_secret_basic-style client ids
    import base64 as b64
    r = client.post("/oauth/token", json={"grant_type": "refresh_token", "refresh_token": "x", "client_id": cid})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    r = client.post("/oauth/token", content=urlencode({"grant_type": "refresh_token", "refresh_token": "x"}),
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "Authorization": "Basic " + b64.b64encode(f"{cid}:".encode()).decode()})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_config_generates_and_reuses_secret_in_data_dir(tmp_path, monkeypatch):
    for k in ("GATEWAY_SECRET", "PUBLIC_URL", "PORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "cfg"))
    first = Config.from_env()
    assert len(first.secret) >= 32 and first.public_url == "http://localhost:8080"
    f = tmp_path / "cfg" / "gateway.secret"
    assert f.exists() and (f.stat().st_mode & 0o777) == 0o600
    assert Config.from_env().secret == first.secret            # stable across restarts
    monkeypatch.setenv("GATEWAY_SECRET", "e" * 40)
    monkeypatch.setenv("PUBLIC_URL", "https://x.example/")
    c = Config.from_env()
    assert c.secret == "e" * 40 and c.public_url == "https://x.example"   # env wins


def test_log_level_parsing_and_masking(monkeypatch):
    from bosch_ebike_connect_classic.logutil import mask_email, parse_level
    assert parse_level("debug") == "DEBUG" and parse_level("warn") == "WARNING" and parse_level("") == "INFO"
    with pytest.raises(SystemExit):
        parse_level("loud")
    assert mask_email("alice@example.com") == "a***@e***.com" and mask_email("junk") == "***"
    monkeypatch.setenv("LOG_LEVEL", "error")
    monkeypatch.setenv("GATEWAY_SECRET", "s" * 40)
    assert Config.from_env().log_level == "ERROR"


@respx.mock
def test_logging_levels_and_no_secrets_in_logs(client, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="ebike")
    cid = register(client)
    bosch_login_ok("cookie-SECRETVALUE")
    tokens, _ = connect(client, cid, "alice@example.com", password="hunter2-PASSWORD")
    respx.get(f"{API}/portal/devices/my_ebikes").mock(return_value=httpx.Response(200, json={"my_ebikes": []}))
    call_tool(client, tokens["access_token"], "get_my_ebikes")
    text = "\n".join(f"{r.levelname} {r.getMessage()}" for r in caplog.records)
    assert "INFO oauth: signed in a***@e***.com" in text
    assert "DEBUG tool get_my_ebikes ok" in text and "DEBUG portal GET portal/devices/my_ebikes -> 200" in text
    for secret in ("hunter2-PASSWORD", "cookie-SECRETVALUE", tokens["access_token"], tokens["refresh_token"],
                   "alice@example.com"):
        assert secret not in text
    # a rejected sign-in is a WARNING
    caplog.clear()
    respx.post(f"{API}/portal/login/public").mock(return_value=httpx.Response(401, json={}))
    connect(client, cid, "bob@example.com", password="x", expect_ok=False)
    assert any(r.levelname == "WARNING" and "rejected by eBike Connect" in r.getMessage() for r in caplog.records)
