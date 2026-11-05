# bosch-ebike-connect-classic

Remote **MCP server** for the **Bosch eBike Connect *Classic*** portal ([ebike-connect.com](https://www.ebike-connect.com)).
Connect your account to Claude, ChatGPT, Cursor or any MCP client and ask about your rides, statistics and
bikes. Multi-user (everyone signs in with their own account), OAuth 2.1, one Docker container.

> **Unofficial.** Not affiliated with Bosch. It uses the portal's own JSON endpoints, which can change
> without notice, and Bosch's terms may restrict automated access. Use at your own risk.
> Classic system only (ebike-connect.com), not the Smart System / eBike Flow app.

## Quick start

Run the published image. You need Docker with Compose and two files in an empty folder.

**1. `docker-compose.yml`**

```yaml
services:
  bosch-ebike-connect-classic:
    image: ghcr.io/edocsyl/bosch-ebike-connect-classic:1.0.0
    container_name: bosch-ebike-connect-classic
    env_file: .env
    environment:
      - DATA_DIR=/data
    user: "${PUID:-1000}:${PGID:-1000}"
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - ${DATA_PATH:-./config}:/data
    restart: unless-stopped
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
```

**2. `.env`** (create the secret with `openssl rand -base64 48`)

```
GATEWAY_SECRET=<at least 32 characters>
PUBLIC_URL=http://localhost:8080
```

**3. Start**

```bash
mkdir -p config                         # persistent data, see below
docker compose up -d
curl http://localhost:8080/healthz      # -> ok
```

**4. Connect a client**, for example Claude Code:

```bash
claude mcp add --transport http bosch-ebike-connect-classic http://localhost:8080/mcp
# then in Claude Code: /mcp -> bosch-ebike-connect-classic -> Authenticate
```

Sign in with your eBike Connect e-mail and password in the browser, then ask: *"Show my e-bikes"* or
*"When was my last ride?"*

> The image is private until you make it public (GitHub → Packages → *Package settings* → Visibility), or
> log in first: `echo "$TOKEN" | docker login ghcr.io -u <github-user> --password-stdin` (token with
> `read:packages`). To build locally instead: clone this repository and run `docker compose up -d --build`.

## Connect clients

The server URL is `<PUBLIC_URL>/mcp`; the landing page at `<PUBLIC_URL>/` shows it.

| Client | How |
|---|---|
| Claude (web, desktop) | *Settings → Connectors → Add custom connector* → `https://<host>/mcp` (needs public https) |
| Claude Code | `claude mcp add --transport http bosch-ebike-connect-classic https://<host>/mcp`, then `/mcp` → Authenticate |
| ChatGPT, Cursor, VS Code, MCP Inspector, … | add a remote MCP server of type *Streamable HTTP* with the URL |

Standard MCP Streamable HTTP and OAuth 2.1 (dynamic client registration, PKCE S256, refresh-token
rotation, RFC 9728 / 8414 / 8707 / 9207), so no client-specific setup. The legacy SSE transport is not supported.

## Configuration

Set in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `GATEWAY_SECRET` | – (required) | ≥ 32 characters; encrypts stored sessions. Changing it invalidates them. |
| `PUBLIC_URL` | – (required) | Address clients use, no trailing slash. Must match, it is the OAuth issuer. |
| `TRUST_PROXY` | `0` | `1` behind Cloudflare or another trusted proxy (rate limits use the real client IP). |
| `ALLOWED_ORIGINS` | empty | Extra browser origins allowed to call `/mcp` (browser-based clients only). |
| `DATA_PATH` | `./config` | Host folder for persistent data (Compose only). |
| `PUID` / `PGID` | `1000` / `1000` | Owner of `DATA_PATH` on the host (`id -u`, `id -g`); the container runs as this user (Compose only). |
| `IMAGE_TAG` | `1.0.0` | Version to run when using this repository's `docker-compose.yml` (Compose only). |

**Persistent data** is one SQLite file (`gateway.sqlite3`: encrypted Bosch sessions, OAuth clients and
tokens) in `./config`, or in `DATA_PATH`. Create the folder yourself before the first start, otherwise
Docker creates it as root and the container cannot write. Back it up; it is useless without the same
`GATEWAY_SECRET`. Deleting it (container stopped) removes all users' sessions.

**Going public:** put TLS in front (for example `cloudflared tunnel --url http://localhost:8080`), set
`PUBLIC_URL` to the https address and `TRUST_PROXY=1`. You then hold other people's Bosch sessions and ride
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
docker exec bosch-ebike-connect-classic python -c "
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
- The container runs as a non-root user with a read-only filesystem and no capabilities.

## Versions and releases

Versions follow [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.

| Change | Bump | Example |
|---|---|---|
| Bug fix, small internal change | PATCH | 1.0.0 → 1.0.1 |
| New tool or option, backwards compatible | MINOR | 1.0.1 → 1.1.0 |
| Breaking change (tool renamed or removed, config changes that need action) | MAJOR | 1.1.0 → 2.0.0 |

The Git tag `vX.Y.Z` is the release. To publish one:

```bash
# 1. set the same version in pyproject.toml ("1.1.0"), add a line to CHANGELOG.md, commit, push
# 2. tag and push
git tag v1.1.0 && git push origin v1.1.0
```

The pipeline then runs the tests and publishes `ghcr.io/edocsyl/bosch-ebike-connect-classic` as `1.1.0`,
`1.1`, `1` and `latest` (`linux/amd64` + `linux/arm64`) and creates a GitHub release with notes. It refuses
to publish if the tag and `pyproject.toml` disagree. In production pin an exact version (`1.1.0`); update by
changing the tag and running `docker compose pull && docker compose up -d` (data in `./config` is kept).
Roll back the same way with the older version.

`CI` (tests and an image smoke test) runs on pushes and pull requests only when code, `Dockerfile`,
`pyproject.toml` or the workflows change, not for README edits. Changes to `main` do not publish an image.

## Known limitations

- Unofficial API: a portal change can break tools. Tested live with one real Classic account, with a
  scripted OAuth client and with Claude Code; not with Claude Desktop, Claude web or other vendors' clients.
- Not verified with real data: `get_route_details`, `export_route_gpx`, non-empty `get_saved_places` and
  `get_licenses` (the test account has none). The expired-session path is tested with a fake Bosch only.
- Runs on the `mcp` 1.x SDK (protocol up to 2025-11-25). Not implemented from the 2026-07-28 revision:
  `server/discover`, `MCP-Protocol-Version` / `Mcp-Method` header validation, Client ID Metadata Documents.
- SQLite in one container: fine for a small group, not horizontally scalable.

<details>
<summary>Portal API coverage and quirks</summary>

The portal's Angular bundle references 72 API URLs. All read endpoints listed above are exposed. Left out on
purpose: personal data (`user/full`, `user/addresses`); everything that changes or deletes data (profile,
e-mail, password, account deletion, deleting or renaming rides, removing or pairing devices, GPX import,
saving routes or places, Strava upload and token calls, dismissing hints; `logout` is used only by
`disconnect_account`); low-value endpoints (`portal/devices/drive_units`, `deletion_pending`,
`next_best_action`, `news/image/`, `user/events`); routing and POI services (`calc_route`, `segments`,
`nearby_search`). After a portal update, re-extract the calls from `main.*.js` to spot changes.

Quirks found while testing:

- Exports need `timezone` = minutes east of UTC, otherwise `HTTP 400`; `export_activity` converts an IANA name.
- Ride filters accept whole numbers only (metres, milliseconds); `search_rides` converts from km / minutes.
- Timestamps (`start_time`, `end_time`, `offset`) are Unix milliseconds as strings.
- A "trip" groups the day's rides. Unknown IDs give `HTTP 400`, not 404.
- Sizes for one 1.5 km ride: CSV about 0.5 KB (summary), GPX 25 KB, TCX 190 KB; dashboard feed 23 KB for 5 items.

</details>

## Development

```bash
pip install -e ".[dev]" && pytest
```

17 tests run the whole OAuth flow and the MCP calls against a fake Bosch (respx); they do not touch the
live portal. Code is in `src/bosch_ebike_connect_classic/`: `app.py` (OAuth, guard, CORS), `tools.py`,
`client.py` (portal), `store.py` (SQLite, encryption), `pages.py` (HTML).

## Credits and license

Endpoints found in the portal's web bundle and in [ebike-connect-js](https://github.com/FlorianCassayre/ebike-connect-js).
Gateway architecture inspired by [missingmcp](https://github.com/VelkyVenik/missingmcp) and
[garmin_mcp](https://github.com/taxuspt/garmin_mcp) (no code shared). Licensed under the [MIT License](LICENSE);
"Bosch" and "eBike Connect" are trademarks of their owners.
