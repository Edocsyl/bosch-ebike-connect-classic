# AGENTS.md

Developer guide for humans and coding agents. User documentation is in [README.md](README.md); keep it
for end users only and put everything developer-facing here.

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
