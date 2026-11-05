# bosch-ebike-connect-classic

Remote **MCP server** for the **Bosch eBike Connect *Classic*** portal ([ebike-connect.com](https://www.ebike-connect.com)).
Connect your account to Claude, ChatGPT, Cursor or any MCP client and ask about your rides, statistics and
bikes. Multi-user (everyone signs in with their own account), OAuth 2.1, one Docker container.

> **Unofficial.** Not affiliated with Bosch. It uses the portal's own JSON endpoints, which can change
> without notice, and Bosch's terms may restrict automated access. Use at your own risk.
> Classic system only (ebike-connect.com), not the Smart System / eBike Flow app.

**Developers and contributors:** everything technical (architecture, tests, security rules, releases, portal API
notes) is in **[AGENTS.md](AGENTS.md)**. This README is for people who run and use the server.

## Quick start

Run the published image. You need Docker with Compose and one file in an empty folder.

**1. `docker-compose.yml`**

```yaml
services:
  bosch-ebike-connect-classic:
    image: ghcr.io/edocsyl/bosch-ebike-connect-classic:1.0.0
    user: "1000:1000"               # owner of ./config (run `id -u` / `id -g`)
    ports:
      - "127.0.0.1:8080:8080"
    environment:
      PUBLIC_URL: http://localhost:8080     # address your clients use (https://... when public)
      # TRUST_PROXY: "xff"                  # behind a reverse proxy ("cloudflare" for a Cloudflare tunnel)
      # LOG_LEVEL: info                     # debug | info | warning | error
      # GATEWAY_SECRET: ...                 # optional: else generated into ./config/gateway.secret
    volumes:
      - ./config:/data              # everything the server keeps: database + secret
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 512m
    pids_limit: 200
```

**2. Start**

```bash
mkdir -p config                         # create it yourself, so it belongs to you and not to root
docker compose up -d
curl http://localhost:8080/healthz      # -> ok
```

**3. Connect a client**, for example Claude Code:

```bash
claude mcp add --transport http bosch-ebike-connect-classic http://localhost:8080/mcp
# then in Claude Code: /mcp -> bosch-ebike-connect-classic -> Authenticate
```

Sign in with your eBike Connect e-mail and password in the browser, then ask: *"Show my e-bikes"* or
*"When was my last ride?"*

> The image is private until you make it public (GitHub → Packages → *Package settings* → Visibility), or
> log in first: `echo "$TOKEN" | docker login ghcr.io -u <github-user> --password-stdin` (token with
> `read:packages`). To build it yourself instead: clone this repository and run `docker compose up -d --build`.

## Connect clients

The server URL is `<PUBLIC_URL>/mcp`; the landing page at `<PUBLIC_URL>/` shows it.

| Client | How |
|---|---|
| Claude (web, desktop) | *Settings → Connectors → Add custom connector* → `https://<host>/mcp` (needs public https) |
| Claude Code | `claude mcp add --transport http bosch-ebike-connect-classic https://<host>/mcp`, then `/mcp` → Authenticate |
| ChatGPT, Cursor, VS Code, MCP Inspector, … | add a remote MCP server of type *Streamable HTTP* with the URL |

Standard MCP Streamable HTTP and OAuth 2.1 (dynamic client registration, PKCE S256, refresh-token
rotation, RFC 9728 / 8414 / 8707 / 9207), so no client-specific setup. The legacy SSE transport is not supported.

## Configuration and data

Everything is set in the `environment:` block of the compose file:

| Variable | Default | Meaning |
|---|---|---|
| `PUBLIC_URL` | `http://localhost:8080` | Address your clients use, no trailing slash. Use your https address when public. |
| `GATEWAY_SECRET` | generated | ≥ 32 characters. If unset, one is generated on first start and kept in `./config/gateway.secret`. |
| `TRUST_PROXY` | off | `xff` (or `1`): behind a reverse proxy, the client IP is taken from `X-Forwarded-For`. `cloudflare`: from `CF-Connecting-IP`. Only enable it when the server is reachable **only** through that proxy, otherwise clients can forge their IP and skip the rate limits. |
| `PROXY_HOPS` | `1` | `xff` mode: number of trusted proxies in front of the server. |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`; see "Logs" below. |
| `ALLOWED_ORIGINS` | empty | Extra browser origins allowed to call `/mcp` (browser-based clients only). |
| `ALLOWED_REDIRECT_HOSTS` | empty | Comma list. If set, `https` redirect URIs of clients must use one of these hosts (e.g. `claude.ai,chatgpt.com`). Strongest protection against phishing via self-registered clients on a public server. |
| `ALLOWED_REDIRECT_SCHEMES` | empty | Extra custom URL schemes clients may use (built in: `cursor`, `vscode`, `claude`, `windsurf`, `zed`, … and any reverse-DNS scheme like `com.example.app`). |
| `ALLOW_INSECURE_HTTP` | off | `1` permits a plain `http://` `PUBLIC_URL` on a non-local address. Passwords would travel unencrypted: do not use on the internet. |

**Limits** (all optional, whole numbers):

| Variable | Default | Limits |
|---|---|---|
| `RATE_LOGIN_IP` | `10` | sign-in attempts per IP per 10 min |
| `RATE_LOGIN_ACCOUNT` | `5` | *failed* sign-ins per e-mail per 10 min (successful ones do not count) |
| `RATE_REGISTER_IP` | `20` | client registrations per IP per hour |
| `RATE_TOKEN_IP` | `60` | token requests per IP per minute |
| `RATE_MCP_USER` | `120` | MCP calls per user per minute |
| `MAX_BODY_BYTES` | `16384` | request body size for `/oauth/*` |
| `MAX_MCP_BODY_BYTES` | `1048576` | request body size for `/mcp` |
| `MAX_CLIENTS` | `10000` | registered OAuth clients (unused ones are purged after 7 days) |
| `MAX_PORTAL_RESPONSE_BYTES` | `10485760` | largest accepted reply from the Bosch portal |

Rate limits live in memory of the one container; they reset on restart.

**Logs:** `docker compose logs -f`. `LOG_LEVEL` decides how much is written:

| Level | Logs |
|---|---|
| `error` | only failures that need attention (unexpected errors) |
| `warning` | plus rejected sign-ins, rate limits, foreign origins, expired Bosch sessions, invalid tokens |
| `info` (default) | plus start-up, sign-ins, registered clients, disconnects, portal errors |
| `debug` | plus every tool call, every request to the portal (path, status, duration), token requests, HTTP access log |

Logs never contain passwords, tokens, cookies or the full e-mail address (shown as `a***@e***.com`).

**Data:** the `./config` folder holds everything the server keeps: `gateway.sqlite3` (encrypted Bosch
sessions, OAuth clients, tokens) and `gateway.secret`. Back it up as a whole. Create it yourself before the
first start (otherwise Docker creates it as root and the container cannot write); `user:` in the compose
file must be its owner. To put the data elsewhere, change the left side of the volume, e.g.
`/srv/ebike:/data`. Deleting the folder (container stopped) removes all users' sessions. For a stricter
setup set `GATEWAY_SECRET` yourself and keep it out of `./config`, so a copy of the folder alone is useless.

**Updates:** pin an exact version in `image:` (e.g. `:1.0.0`), change it to update, then
`docker compose pull && docker compose up -d`. The data in `./config` is kept; go back by setting the old
version. What changed in each version: [Releases](../../releases).

**Going public:** put TLS in front (for example `cloudflared tunnel --url http://localhost:8080`), set
`PUBLIC_URL` to the https address and `TRUST_PROXY` (`xff` or `cloudflare`); for a public server also set `ALLOWED_REDIRECT_HOSTS`. You then hold other people's Bosch sessions and ride
data: provide a privacy notice (GDPR / Swiss DSG) and start with a trusted circle.

## Tools

Read-only, except `disconnect_account`. Errors come back as `Error: …` with `isError: true`.

| Tool | Returns |
|---|---|
| `get_user_profile` | name, units, e-mail |
| `get_my_ebikes` | drive unit, display, batteries, firmware |
| `get_statistics` | totals per year and month, plus all-time |
| `get_activities` | trips with their rides; `offset` (epoch ms) pages backwards |
| `get_activity_details` | ride or trip aggregates; `include_series=true` adds speed, cadence, heart rate, GPS, power (large) |
| `export_activity` | GPX, TCX or CSV; `timezone` (IANA, default `Europe/Zurich`). Prefer GPX/CSV, TCX is very large |
| `search_rides` | filter by title, `*_distance_km`, `*_duration_min` |
| `get_routes`, `get_route_details`, `export_route_gpx` | saved routes (`segmented=true` for route-builder routes) |
| `get_saved_places` | favourites |
| `get_weather_forecast` | daily or hourly forecast for a latitude / longitude |
| `get_dashboard_feed` | news and latest activities incl. GPS tracks; keep `count` small |
| `get_integrations` | Strava / Komoot connection state (no tokens) |
| `get_user_settings`, `get_licenses` | account settings; purchased licences |
| `disconnect_account` | signs out and deletes the user's data on this server; needs `confirm=true` |

## Disconnect / remove an account

Ask the assistant to run **`disconnect_account`** with `confirm=true`. It sends the portal's logout request,
then deletes that user's stored session, tokens and codes. Afterwards the connector must be authorised again.

> **The portal does not revoke the old session on logout** (verified: the cookie still worked afterwards).
> To really end the session at Bosch, change your password on ebike-connect.com.

As operator you can also delete one account (replace the e-mail, lower case) or everything:

```bash
docker compose exec bosch-ebike-connect-classic python -c "
import sqlite3; db = sqlite3.connect('/data/gateway.sqlite3'); k = 'you@example.com'
[db.execute(f'delete from {t} where account_key=?', (k,)) for t in ('accounts', 'tokens', 'codes')]
db.commit()"
# everything: docker compose down && rm -rf config
```

## How it works and security

```
Client ──OAuth 2.1──▶ /oauth/*  ── sign-in page ──▶ ebike-connect.com
Client ──POST /mcp + Bearer──▶ guard ─▶ MCP tools ─▶ ebike-connect.com
```

- The password goes to Bosch once and is **never stored**. Only the session cookie is kept, encrypted
  with AES-256-GCM (key derived from `GATEWAY_SECRET`).
- Tokens and authorization codes are stored only as SHA-256 hashes; refresh tokens rotate; codes are
  single-use; redirect URIs must match exactly.
- Tools take no account parameter: the user comes from the validated token, so users cannot reach each other.
- An expired Bosch session drops the tokens; the next call gets `401` and the client signs in again.
- `/mcp` rejects foreign browser origins; sign-in, registration, token and MCP calls are rate limited.
- Sign-in page shows where your browser is sent afterwards and that the app name is unverified; custom URL
  schemes are restricted, request bodies are size-capped, rate limits are bounded and configurable.
- A replayed refresh token revokes all tokens of that client and account.
- The container runs as a non-root user with a read-only filesystem, no capabilities, and memory / process limits.

## Known limitations

- Unofficial API: a portal change can break tools. Tested live with one real Classic account, with a
  scripted OAuth client and with Claude Code; not with Claude Desktop, Claude web or other vendors' clients.
- Not verified with real data: `get_route_details`, `export_route_gpx`, non-empty `get_saved_places` and
  `get_licenses` (the test account has none). The expired-session path is tested with a fake Bosch only.
- SQLite and in-memory rate limits in one container: fine for a small group, not horizontally scalable.
- Reviewed by an automated security review, not by an independent audit or penetration test. If you run it
  publicly, you carry the responsibility for the sessions you store.

## Contributing, credits and license

Contributions are welcome: start with [AGENTS.md](AGENTS.md). Endpoints found in the portal's web bundle and in [ebike-connect-js](https://github.com/FlorianCassayre/ebike-connect-js).
Gateway architecture inspired by [missingmcp](https://github.com/VelkyVenik/missingmcp) and
[garmin_mcp](https://github.com/taxuspt/garmin_mcp) (no code shared). Licensed under the [MIT License](LICENSE);
"Bosch" and "eBike Connect" are trademarks of their owners.
