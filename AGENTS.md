# AGENTS.md

Developer guide for humans and coding agents (Claude Code reads it through `CLAUDE.md`). User documentation
is in [README.md](README.md); keep that for end users only and put everything developer-facing here.

Contents: [What this is](#what-this-is) · [Layout](#layout) · [Commands](#commands) ·
[Architecture](#architecture) · [How to](#how-to) · [Testing](#testing) · [Conventions](#conventions) ·
[Security invariants](#security-invariants-do-not-weaken) · [Configuration](#configuration-environment) ·
[Versioning and releases](#versioning-and-releases) · [CI](#ci-and-docker) · [Portal API notes](#portal-api-notes) ·
[Known gaps](#known-gaps) · [Troubleshooting](#troubleshooting)

## What this is

A multi-user remote MCP server (Streamable HTTP, OAuth 2.1) for the Bosch eBike Connect **Classic**
portal (`https://www.ebike-connect.com/ebikeconnect/api/`, undocumented). One container serves many users;
each signs in with their own portal account. Python 3.11+, `mcp` SDK 1.x (`FastMCP`), Starlette, httpx,
SQLite, `cryptography`.

Out of scope: the Smart System / eBike Flow platform (different backend).

## Layout

```
src/bosch_ebike_connect_classic/
  app.py        Starlette app: OAuth server (DCR, PKCE, tokens), McpGuard (Bearer, Origin, rate limit), Cors
  tools.py      MCP tools; current user comes from a contextvar set by McpGuard
  client.py     Async portal client (BoschClient), login(), error types
  store.py      SQLite: sealed accounts, clients, codes, tokens (hashes only)
  config.py     Environment -> Config; generates gateway.secret in the data dir if unset
  pages.py      HTML pages (sign-in, success, message, landing); English only, no JavaScript
  ratelimit.py  Sliding-window limiter
tests/test_flow.py   Whole OAuth flow + tools against a fake Bosch (respx)
Dockerfile, docker-compose.yml, .github/workflows/{ci,release}.yml, CHANGELOG.md
```

## Commands

```bash
pip install -e ".[dev]"      # install with test dependencies
pytest -q                    # 18 tests, no network, no real Bosch account
docker compose up -d --build # build and run locally (data in ./config, port 127.0.0.1:8080)
docker compose logs -f
```

Local live testing with a real account: sign in through `http://localhost:8080/oauth/authorize` (OAuth
client registered via `POST /oauth/register`), or `claude mcp add --transport http bosch-ebike-connect-classic
http://localhost:8080/mcp` and `/mcp` → Authenticate. Never commit real tokens, cookies, ride data or
`./config`.

## Architecture

```
Client ── GET /.well-known/oauth-protected-resource ─▶ discovery (RFC 9728 / 8414)
Client ── POST /oauth/register ───────────────────────▶ dynamic client registration (RFC 7591)
Client ── GET /oauth/authorize ───────────────────────▶ sign-in page (CSRF cookie)
User   ── POST /oauth/authorize (e-mail, password) ───▶ login() at Bosch -> REMEMBER cookie sealed in DB
                                                        -> auth code -> success page -> redirect to client
Client ── POST /oauth/token (code + PKCE verifier) ───▶ access (1 h) + refresh (30 d) token
Client ── POST /mcp  Authorization: Bearer ───────────▶ McpGuard -> FastMCP tool -> BoschClient -> portal
```

Request path for a tool call: `Cors` (outermost) → Starlette routes → `Mount("/")` → `McpGuard` (Origin check,
Bearer lookup, per-user rate limit, sets `current_account`) → FastMCP (stateless, JSON responses) → tool →
`run()` in `tools.py` loads the account's sealed cookie and creates a `BoschClient`. A `401` from the portal
raises `AuthRequired`: `run()` revokes the user's tokens and returns the expired-session error, so the next
MCP call gets `401` and the client re-authorises. A rotated `REMEMBER` cookie is persisted via `on_rotate`.

HTTP endpoints:

| Path | Purpose |
|---|---|
| `/` | landing page |
| `/healthz` | `ok` (Docker health check) |
| `/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration` (+ `/mcp` variant) | AS metadata |
| `/.well-known/oauth-protected-resource` (+ `/mcp` variant) | protected-resource metadata |
| `/oauth/register` | dynamic client registration (public clients, `token_endpoint_auth_method: none`) |
| `/oauth/authorize` | sign-in page and form post |
| `/oauth/token` | `authorization_code` and `refresh_token` grants; form or JSON body; `client_id` also via Basic |
| `/mcp` | MCP Streamable HTTP (stateless) |

SQLite (`<DATA_DIR>/gateway.sqlite3`, WAL): `clients(client_id, name, redirect_uris)`, `codes(code_hash,
client_id, account_key, redirect_uri, challenge, expires_at)`, `tokens(token_hash, kind, client_id, account_key,
expires_at)`, `accounts(account_key, blob, updated_at)`. `account_key` is the lower-case login e-mail; `blob` is
the AES-256-GCM sealed JSON `{username, remember}`. TTLs: code 10 min, access 1 h, refresh 30 d. Expired rows
are purged hourly. Rate limits (`app.py`): sign-in 10 / 10 min per IP and 5 / 10 min per e-mail, registration
20 / h per IP, token 60 / min per IP, MCP 120 / min per user.

## How to

**Add a tool.** (1) Add the portal call as a method on `BoschClient` in `client.py` (GET unless you have a
strong reason; use `request(path, params=…)`). (2) Add an `@mcp.tool()` function in `tools.py` that validates
input (IDs with `ID_RE` / `isdigit`, ranges) and returns `await run(lambda c: c.your_call(...))`; annotate
read-only behaviour in the docstring (it becomes the tool description). (3) Add a test in `tests/test_flow.py`
with a respx mock of the portal URL. (4) Update the tools table in `README.md` and bump the version (MINOR).

**React to a portal change.** Re-extract the API calls from the portal bundle (see Portal API notes), compare
with `client.py`, fix, and add a test that pins the new behaviour. A portal change that breaks a tool is a
PATCH release.

**Add a client-compatibility feature.** Keep it standard (RFC-based). Do not add client-specific branches.

**Change the data model.** `store.py` uses `CREATE TABLE IF NOT EXISTS`; there is no migration framework. A
schema change needs an explicit, backwards-compatible migration step in `Store.__init__` and a MAJOR or MINOR
bump with a CHANGELOG note.

**Update a dependency.** Bump in `pyproject.toml`, run `pytest`, build the image. `mcp` is pinned `<2`:
SDK 2.x renamed `FastMCP` to `MCPServer` and implements the 2026-07-28 protocol revision, so moving to it is a
deliberate migration, not a bump.

## Testing

- `pytest -q` runs `tests/test_flow.py` (18 tests): OAuth flow and user isolation, 401 challenge, wrong
  password, single-use code and PKCE, redirect / CSRF rejection, expired-session re-authorisation, refresh and
  cookie rotation, export time zones, read tools against mocked endpoints, `disconnect_account`, encryption at
  rest, Origin / resource / `iss` / `isError`, sign-in and success pages, config secret generation.
- The Bosch portal is mocked with `respx`; tests never touch the network. Helpers: `connect()` runs the whole
  sign-in and returns tokens, `call_tool()` posts a JSON-RPC `tools/call`, `tool_text()` extracts the text.
- Live checks against a real account are manual (see Commands) and use the browser sign-in; never script the
  password. Check the live behaviour of any new endpoint once and record quirks below.
- The Docker image is smoke-tested in CI: `/healthz`, `/mcp` without token → 401, OAuth metadata.

## Conventions

- Code, comments, commit messages, docs and UI text are **English**.
- Tools return text; errors start with `Error:` (the `_res` helper in `tools.py` sets `isError`).
- A tool never takes an account parameter. The user comes from `current_account` only.
- Tools are read-only. The single exception is `disconnect_account` (needs `confirm=true`,
  `destructiveHint`). Do not add write endpoints without an explicit decision and a `confirm` gate.
- Add a test for every behaviour change; run `pytest` before pushing.
- Keep the README for users: no developer notes there.

## Security invariants (do not weaken)

- The portal password is used once to sign in and is never stored or logged. Only the `REMEMBER` cookie is
  kept, sealed with AES-256-GCM (HKDF key from `GATEWAY_SECRET`, `account_key` as AAD).
- Access tokens, refresh tokens and auth codes are stored only as SHA-256 hashes. Codes are single-use,
  refresh tokens rotate, redirect URIs must match exactly, PKCE S256 only.
- `/mcp` requires a valid Bearer token (401 with an RFC 9728 `WWW-Authenticate`), rejects foreign `Origin`
  headers (403) and is rate limited per user. CORS is open only on discovery, registration and token
  endpoints.
- `GATEWAY_SECRET` is generated into `<data>/gateway.secret` (0600) when not set. Setting it as an
  environment variable keeps the key apart from the data it protects.
- Container: non-root, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`.

## Configuration (environment)

| Variable | Default | Notes |
|---|---|---|
| `GATEWAY_SECRET` | generated in `<DATA_DIR>/gateway.secret` | ≥ 32 characters |
| `PUBLIC_URL` | `http://localhost:<PORT>` | OAuth issuer; no trailing slash |
| `DATA_DIR` | `/data` | SQLite and secret (mounted from `./config` by Compose) |
| `TRUST_PROXY` | `0` | `1`: rate limits use `CF-Connecting-IP` / `X-Forwarded-For` |
| `ALLOWED_ORIGINS` | empty | extra browser origins for `/mcp` |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | |

## Versioning and releases

[Semantic Versioning](https://semver.org), `MAJOR.MINOR.PATCH`; the Git tag `vX.Y.Z` is the release.

| Change | Bump |
|---|---|
| Bug fix, small internal change | PATCH |
| New tool or option, backwards compatible | MINOR |
| Breaking change (tool renamed/removed, setup that needs user action) | MAJOR |

Release steps:

1. Set the new version in `pyproject.toml` and add a section to `CHANGELOG.md`.
2. Update the image tag in `docker-compose.yml` and in the README quick start.
3. Commit, push, then `git tag vX.Y.Z && git push origin vX.Y.Z`.

`release.yml` then verifies tag = `pyproject.toml` version, runs the tests, publishes
`ghcr.io/<owner>/<repo>` as `X.Y.Z`, `X.Y`, `X` (not for 0.x) and `latest` for `linux/amd64` and
`linux/arm64`, and creates a GitHub release with generated notes. `ci.yml` (tests, image smoke test with no
environment) runs only when `src/`, `tests/`, `Dockerfile`, `.dockerignore`, `pyproject.toml` or the
workflows change; it never publishes.

## Portal API notes

Source of truth: the module that builds `origin + "/ebikeconnect/api/"` in the portal's `main.*.js` and the
call sites in its lazy chunks (72 URLs). After a portal update, re-extract them to spot changes.

Headers the SPA sends: `Accept: application/vnd.ebike-connect.com.v4+json, application/json`,
`Protect-from: CSRF`. Login: `POST portal/login/public` with `{username, password, rememberme}` returns the
`REMEMBER` cookie (no captcha or MFA observed). `POST portal/logout/public` does **not** invalidate that
cookie (verified); revoking needs a password change.

Exposed (read-only): `user/profile`, `portal/devices/my_ebikes`, `portal/statistics/all_statistics`,
`portal/activities/{trip,ride}/headers`, `activities/{ride,trip}/details/{id}`, GPX / TCX / CSV export,
`portal/navigation/my_items/{route_headers,destinations,route_details,segmented_route_details}`,
`portal/weather/forecast/{daily,hourly}/`, `portal/dashboard/stream`, `portal/{strava,komoot}/*`,
`settings?filter=user`, `purchase/licenses`.

Deliberately not exposed: personal data (`user/full`, `user/addresses`); anything that changes or deletes
data (profile, e-mail, password, account deletion, ride delete/rename, device removal or pairing, GPX import,
saving routes or places, Strava upload/token calls, dismissing hints); low value (`portal/devices/drive_units`,
`deletion_pending`, `next_best_action`, `news/image/`, `user/events`); routing and POI services
(`calc_route`, `segments`, `nearby_search`).

Quirks:

- Exports need `timezone` = minutes east of UTC (SPA: `-(new Date).getTimezoneOffset()`), else HTTP 400.
  `export_activity` takes an IANA name and converts it.
- Ride filters accept whole numbers only (metres, milliseconds), else HTTP 400 (`For input string: "1480.0"`).
  `search_rides` converts from km / minutes.
- Timestamps (`start_time`, `end_time`, `offset`) are Unix milliseconds as strings.
- A "trip" groups a day's rides. Unknown IDs give HTTP 400 ("No activity found."), not 404.
- Empty POST bodies are answered with an empty reply; the client returns `{"status": code}`.
- Sizes for one 1.5 km ride: CSV 0.5 KB (summary), GPX 25 KB, TCX 190 KB; dashboard feed 23 KB for 5 items.

## Known gaps

- Not verified with real data: `get_route_details`, `export_route_gpx`, non-empty `get_saved_places` and
  `get_licenses`. The expired-session path (portal answers 401 → tokens dropped) is covered by tests only.
- Whether the portal ever rotates the `REMEMBER` cookie is unknown (the client persists a rotated value).
- SDK 1.x (protocol up to 2025-11-25). From the 2026-07-28 spec revision not implemented: `server/discover`,
  `MCP-Protocol-Version` / `Mcp-Method` header validation, Client ID Metadata Documents. Custom-scheme
  redirect URIs are still accepted at registration.
- Tested live with a scripted OAuth client and Claude Code; not with Claude Desktop, Claude web or other clients.
- SQLite in one container: no horizontal scaling.

## CI and Docker

- `Dockerfile`: `python:3.12-slim`, installs the package, user `ebike` (uid 10001), `DATA_DIR=/data`, health
  check on `/healthz`, entrypoint `bosch-ebike-connect-classic`. Compose runs it with `user: "1000:1000"` so a
  bind-mounted `./config` is writable; without `--user` the data folder must be owned by `10001:10001`.
- `ci.yml`: tests on Python 3.11 / 3.12 / 3.13, then image build and smoke test. Path-filtered: README and
  CHANGELOG edits do not trigger it. Uses the GitHub Actions cache for Docker layers.
- `release.yml`: only on tags `vX.Y.Z`. Needs no secrets (built-in `GITHUB_TOKEN`; permissions `contents:
  write`, `packages: write`). After the first publish the GHCR package is private until its visibility is
  changed in the package settings.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Container exits with a permission error on `/data` | `./config` is owned by root or another user: `mkdir -p config` before the first start and match `user:` to its owner |
| Every client asks to sign in again | `gateway.secret` / `GATEWAY_SECRET` changed or `./config` was deleted: stored sessions are unreadable |
| `401` on `/mcp` right after sign-in | `PUBLIC_URL` does not match the address the client uses (issuer mismatch) |
| `403 forbidden_origin` | browser-based client: add its origin to `ALLOWED_ORIGINS` |
| Tool answers "session expired" | the Bosch session ended; the client re-authorises on the next call |
| Tool answers `HTTP 400` | portal rejects the parameters (unknown ID, non-integer filter, missing `timezone`); see quirks |
| Image pull is denied | package is private: `docker login ghcr.io` with a `read:packages` token or make it public |
