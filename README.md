# bosch-ebike-connect-classic

Multi-user **remote MCP server** for the **Bosch eBike Connect *Classic* system**
(<https://www.ebike-connect.com>). Each user connects their own account from any MCP client
(Claude, Claude Code, ChatGPT, Cursor, VS Code, …) via OAuth 2.1, then asks their assistant about
rides, statistics, bikes and more. One Docker container serves everyone.

> **Unofficial.** Not affiliated with or endorsed by Bosch. Bosch offers no public API for private
> users; this project calls the portal's own JSON endpoints, which can change without notice, and
> Bosch's terms may restrict automated access. Use at your own risk.

## Contents

- [Which Bosch platform?](#which-bosch-platform)
- [Features](#features)
- [Quick start](#quick-start)
- [Connecting clients](#connecting-clients)
- [Configuration](#configuration)
- [Public hosting](#public-hosting)
- [Docker image and CI](#docker-image-and-ci)
- [Tools](#tools)
- [Disconnect / remove an account](#disconnect--remove-an-account)
- [How it works and security](#how-it-works-and-security)
- [Portal API coverage](#portal-api-coverage)
- [Portal quirks](#portal-quirks)
- [Known limitations](#known-limitations)
- [Development](#development)
- [Credits](#credits)
- [License](#license)

## Which Bosch platform?

Bosch runs several eBike platforms with separate backends. This project covers only one:

| Platform | Portal / app | Covered |
|---|---|---|
| **Classic system** | ebike-connect.com + eBike Connect app | **yes (this project)** |
| Smart System | eBike Flow app / Bosch Flow | no (see e.g. [bosch-flow-mcp](https://pypi.org/project/bosch-flow-mcp/)) |

The `-classic` suffix in the name is there so it is not mistaken for a Smart System / Flow connector.

## Features

- **17 tools**: profile, bikes and components, lifetime statistics, trips and rides, ride details,
  GPX / TCX / CSV export, ride search (title, distance, duration), saved routes and places, weather
  forecast, dashboard feed, Strava / Komoot status, settings, licences.
- **Read-only** apart from one tool, `disconnect_account`, which signs the user out and deletes their
  data on the server.
- **Multi-user:** every user signs in on your sign-in page; sessions are isolated per user.
- **Client-agnostic:** plain MCP Streamable HTTP plus standard OAuth 2.1 (dynamic client registration,
  PKCE). No client-specific code.
- **Careful with secrets:** the Bosch password is never stored; the session cookie is encrypted at
  rest; tokens are stored only as hashes.
- **Docker-first:** non-root, read-only filesystem, all capabilities dropped.
- Clean sign-in and success pages (English, no JavaScript) that also explain how to disconnect.

## Quick start

Requirements: Docker with Compose.

```bash
cp .env.example .env     # set GATEWAY_SECRET (openssl rand -base64 48) and PUBLIC_URL
mkdir -p config          # persistent data lives here (see "Persistent data")
docker compose up -d     # pulls the published image; add --build to build it locally instead
curl http://localhost:8080/healthz   # -> ok
```

The published image is private until you make it public; run
`echo <token> | docker login ghcr.io -u <user> --password-stdin` with a token that has
`read:packages` first, or use `--build`.

For a local test set `PUBLIC_URL=http://localhost:8080`. Add it to Claude Code (which accepts
`http://localhost` for OAuth):

```bash
claude mcp add --transport http bosch-ebike-connect-classic http://localhost:8080/mcp
# in Claude Code: /mcp -> bosch-ebike-connect-classic -> Authenticate
```

Sign in with your eBike Connect e-mail and password in the browser window that opens. Then ask, for
example: *"Show my e-bikes"* or *"When was my last ride?"*.

Stop with `docker compose down` (the data in `./config` stays). Logs:
`docker logs -f bosch-ebike-connect-classic`.

## Connecting clients

The server URL is `<PUBLIC_URL>/mcp`. The landing page at `<PUBLIC_URL>/` shows it with short
instructions.

| Client | How |
|---|---|
| Claude (web / desktop) | *Settings → Connectors → Add custom connector* → `https://<host>/mcp` (needs a public https URL) |
| Claude Code | `claude mcp add --transport http bosch-ebike-connect-classic https://<host>/mcp`, then `/mcp` → Authenticate |
| ChatGPT, Cursor, VS Code, MCP Inspector, … | add a remote MCP server of type *Streamable HTTP* with the URL; the client discovers sign-in by itself |

Standards implemented: protected-resource metadata (RFC 9728), authorization-server metadata
(RFC 8414, also served at `/.well-known/openid-configuration`), dynamic client registration
(RFC 7591), PKCE with S256, refresh-token rotation, `resource` check (RFC 8707) and `iss` response
parameter (RFC 9207). The token endpoint accepts form or JSON bodies and a `client_id` in Basic auth.
Browser-based clients get CORS on the discovery, registration and token endpoints; for `/mcp` add
their origin to `ALLOWED_ORIGINS`. The legacy HTTP+SSE transport (`/sse`) is not supported.

## Configuration

Environment variables (see `.env.example`):

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `GATEWAY_SECRET` | yes | – | At least 32 characters. Derives the key that encrypts stored sessions. Losing or changing it invalidates all stored sessions. |
| `PUBLIC_URL` | yes | – | Externally visible base URL without trailing slash; it is the OAuth issuer and must match what clients connect to. |
| `TRUST_PROXY` | no | `0` | `1` behind Cloudflare or another trusted proxy: rate limits use `CF-Connecting-IP` / `X-Forwarded-For`. |
| `ALLOWED_ORIGINS` | no | empty | Extra browser origins (comma separated) allowed to call `/mcp`, only for browser-based MCP clients. |
| `DATA_DIR` | no | `/data` | Where the SQLite database lives *inside* the container. Leave it; change the host folder with `DATA_PATH` (below). |
| `HOST` / `PORT` | no | `0.0.0.0` / `8080` | Listen address. Compose publishes `127.0.0.1:8080` only. |

Docker Compose only (set in `.env`, not read by the app):

| Variable | Default | Meaning |
|---|---|---|
| `DATA_PATH` | `./config` | Host folder for the persistent data, mounted at `/data`. Relative paths are relative to the compose file; absolute paths work too. |
| `PUID` / `PGID` | `1000` / `1000` | Numeric user and group that own `DATA_PATH` on the host (`id -u`, `id -g`). The container runs as this user so it can write there. |
| `IMAGE` / `IMAGE_TAG` | `ghcr.io/edocsyl/bosch-ebike-connect-classic` / `1.0.0` | Published image and version to run. |

### Persistent data

Everything the server keeps (the encrypted Bosch sessions, OAuth clients and tokens) is one SQLite
database, `gateway.sqlite3`, in the folder mounted at `/data`. By default that is **`./config`** next to
`docker-compose.yml`; change it with `DATA_PATH`, for example `DATA_PATH=/srv/ebike`.

- Create the folder yourself before the first start (`mkdir -p config`). If Docker creates it, it
  belongs to root and the container cannot write to it.
- The folder must be owned by `PUID:PGID` (default `1000:1000`, the usual first user on Linux). If your
  user has other ids, set them in `.env`.
- Back up this folder. Without the same `GATEWAY_SECRET` the sessions in it are unreadable.
- Deleting the folder (with the container stopped) removes every user's stored session.

## Public hosting

Put TLS in front, for example a Cloudflare Tunnel:

```bash
cloudflared tunnel --url http://localhost:8080   # or a named tunnel to your hostname
```

Then set `PUBLIC_URL` to the public https URL and `TRUST_PROXY=1`, and rebuild. Behind nginx, disable
response buffering and allow long timeouts for `/mcp`.

**Before you let other people connect:** you then hold their Bosch sessions and can read their ride
and location data. You need a privacy notice (GDPR / Swiss DSG) and should start with a trusted
circle. Each user can remove their data at any time (see below).

## Docker image and CI

`.github/workflows/ci.yml` runs on every push and pull request:

1. **test:** `pytest` on Python 3.11, 3.12 and 3.13.
2. **docker:** builds the image, starts it and smoke-tests it (`/healthz` answers `ok`, `/mcp` without a
   token answers `401`, OAuth metadata is served). Pull requests stop here.
3. **publish:** on pushes to `main` and on version tags, builds `linux/amd64` and `linux/arm64` and
   pushes to the GitHub Container Registry as `ghcr.io/<owner>/<repo>` (with SBOM and provenance).

Tags: `latest` and `main` from the default branch; `1.0.0`, `1.0` from a tag `v1.0.0`.

```bash
git tag v1.0.0 && git push origin v1.0.0     # publishes ghcr.io/<owner>/<repo>:1.0.0
```

After the first publish, set the package visibility in the package settings on GitHub (new packages
are private by default).

### Deploy the published image

The image is published at `ghcr.io/edocsyl/bosch-ebike-connect-classic` (replace the path with your own
`<owner>/<repo>` if you fork the project). It supports `linux/amd64` and `linux/arm64`, so it also runs
on a Raspberry Pi or an ARM server.

**1. Make the image pullable.** Packages are private after the first publish. Either:

- *Make it public:* on GitHub open your profile → *Packages* → `bosch-ebike-connect-classic` →
  *Package settings* → *Change visibility* → Public. Then no login is needed to pull.
- *Or keep it private and log in on every server that pulls it,* with a personal access token
  (classic) that has only the `read:packages` scope:

  ```bash
  echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
  ```

**2. Deploy with Docker Compose (recommended).** You do not need to clone the repository, only a folder
with two files. Create `docker-compose.yml`:

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
      - "127.0.0.1:8080:8080"          # put TLS / a Cloudflare Tunnel in front
    volumes:
      - ${DATA_PATH:-./config}:/data   # persistent data: ./config, or set DATA_PATH
    restart: unless-stopped
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
```

and a `.env` (`GATEWAY_SECRET` from `openssl rand -base64 48`; `PUBLIC_URL` is the address your clients
use; see [Configuration](#configuration) for the optional values):

```
GATEWAY_SECRET=<at least 32 characters>
PUBLIC_URL=https://ebike.example.com
TRUST_PROXY=1
#DATA_PATH=/srv/ebike
#PUID=1000
#PGID=1000
```

Then:

```bash
mkdir -p config                       # persistent data; must be owned by PUID:PGID
docker compose pull && docker compose up -d
curl http://localhost:8080/healthz    # -> ok
```

If you cloned the repository instead, its `docker-compose.yml` already points at this image: use the
same commands (`docker compose up -d` pulls, `--build` builds locally).

**3. Update.** Set the new version in `image:` (or `IMAGE_TAG` in `.env` when using the repository's
compose file) and run `docker compose pull && docker compose up -d`. The data in `./config` is kept. Tags
are `1.0.0` (exact), `1.0` (latest patch), `latest` and `main` (current development state). Pin an exact
version in production.

**4. Roll back.** Set the previous version tag and run the same two commands.

**Plain `docker run`**, with the data in `./config` (change the left side of `-v` for another folder):

```bash
mkdir -p config
docker run -d --name bosch-ebike-connect-classic --restart unless-stopped \
  -p 127.0.0.1:8080:8080 \
  --user "$(id -u):$(id -g)" -v "$PWD/config:/data" \
  --env-file .env -e DATA_DIR=/data \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  ghcr.io/edocsyl/bosch-ebike-connect-classic:1.0.0
```

Use an `--env-file` with a fixed `GATEWAY_SECRET`: a new secret makes the stored sessions unreadable.
Without `--user` the image runs as uid 10001, so the mounted folder must then be owned by `10001:10001`
(`sudo chown 10001:10001 config`).

No secrets are needed in GitHub: publishing uses the built-in `GITHUB_TOKEN`.

## Tools

All tools are read-only except `disconnect_account`. Tool errors are returned as text starting with
`Error:` and flagged `isError: true`.

| Tool | Portal endpoint (`/ebikeconnect/api/…`) | Notes |
|---|---|---|
| `get_user_profile` | `user/profile` | name, units, e-mail |
| `get_my_ebikes` | `portal/devices/my_ebikes` | drive unit, display, batteries, firmware |
| `get_statistics` | `portal/statistics/all_statistics` | per year and month, plus all-time |
| `get_activities` | `portal/activities/trip/headers` | trips with their rides; `offset` (epoch ms) pages backwards |
| `get_activity_details` | `activities/{ride\|trip}/details/{id}` | aggregates; `include_series=true` adds speed, cadence, heart rate, GPS and power series (large) |
| `export_activity` | `gpx/export/…`, `activities/…/export/{tcx\|csv}` | GPX, TCX, CSV text; `timezone` (IANA, default `Europe/Zurich`). Prefer GPX or CSV: TCX is very large |
| `get_routes` | `portal/navigation/my_items/route_headers` | saved routes |
| `search_rides` | `portal/activities/ride/headers` | filter by title, `*_distance_km`, `*_duration_min`; also returns `total_number_of_filtered_bike_rides` |
| `get_saved_places` | `portal/navigation/my_items/destinations` | favourites |
| `get_route_details` | `…/route_details/{id}`, `…/segmented_route_details/{id}` | `segmented=true` for route-builder routes |
| `export_route_gpx` | `gpx/export/route/{id}` | |
| `get_weather_forecast` | `portal/weather/forecast/{daily\|hourly}/` | latitude / longitude rounded to 2 decimals |
| `get_dashboard_feed` | `portal/dashboard/stream` | news and latest activities, including GPS tracks; keep `count` small |
| `get_integrations` | `portal/{strava\|komoot}/{connect_status\|upload_option}` | connection state only, no tokens |
| `get_user_settings` | `settings?filter=user` | |
| `get_licenses` | `purchase/licenses` | e.g. Premium Custom Modes |
| `disconnect_account` | `POST portal/logout/public` + local delete | the only tool that changes anything; needs `confirm=true`; marked `destructiveHint` |

## Disconnect / remove an account

**Self-service (recommended):** ask the assistant to run `disconnect_account` with `confirm=true`. It
sends the portal's logout request (`POST portal/logout/public`, what the portal's own logout button
sends), then deletes this user's stored Bosch session, all access and refresh tokens and pending
codes. Without `confirm=true` it only describes the effect. If the portal request fails, the local
data is still deleted and the answer says so. Afterwards the connector must be authorised again. It
touches only the calling user's data. The success page after sign-in repeats these instructions.

> **The portal does not revoke the old session on logout.** Verified live: after the logout request
> the `REMEMBER` cookie still worked. `disconnect_account` therefore removes this server's copy only.
> To really end the session at Bosch, change your password on ebike-connect.com.

**Manual, as operator** (also works when the user cannot reach the tool):

1. *Client side:* Claude Code → `/mcp` → select the server → clear authentication; or remove the
   connector (`claude mcp remove bosch-ebike-connect-classic`; Claude web / desktop: remove the
   custom connector). This only drops the client's own token.
2. *Server side, one account:*

   ```bash
   docker exec bosch-ebike-connect-classic python -c "
   import sqlite3; db = sqlite3.connect('/data/gateway.sqlite3'); k = 'you@example.com'  # lower-case login e-mail
   [db.execute(f'delete from {t} where account_key=?', (k,)) for t in ('accounts', 'tokens', 'codes')]
   db.commit()"
   ```

3. *Server side, everything:* stop the container and delete the data folder (`docker compose down && rm -rf config`) to remove all users' sessions.

Expired tokens are purged hourly; a Bosch session stays stored until it is deleted as above.

## How it works and security

```
Client ──OAuth 2.1 (DCR + PKCE S256)──▶ /oauth/*  ── sign-in form ──▶ ebike-connect.com
Client ──POST /mcp + Bearer───────────▶ guard ─▶ in-process MCP tools ─▶ ebike-connect.com
```

- The user signs in on **your** `/oauth/authorize` page. The password goes to Bosch once and is
  **never stored**; only the resulting `REMEMBER` session cookie is kept, sealed with AES-256-GCM (key
  derived from `GATEWAY_SECRET` with HKDF, the account bound as associated data).
- Access tokens, refresh tokens and authorization codes are stored **only as SHA-256 hashes**.
  Refresh tokens rotate; codes are single-use and valid for 10 minutes; redirect URIs must match the
  registration exactly; sign-in uses a CSRF cookie.
- Tools read the user from the validated Bearer token only. There is no account parameter, so one
  user cannot reach another user's session.
- If the Bosch session expires, the tokens are dropped, the next call gets `401` with an RFC 9728
  challenge and the client runs the sign-in again.
- `/mcp` rejects foreign browser origins (DNS-rebinding protection).
- Rate limits: sign-in per IP and per e-mail (protects the Bosch account from lock-outs), dynamic
  client registration, token endpoint, MCP calls per user.
- Container: non-root user, read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`.

## Portal API coverage

The portal's Angular bundle references 72 API URLs. How they are handled:

- **Exposed (read-only GET):** everything in the [tools table](#tools).
- **Deliberately not exposed, personal data:** `user/full` (complete user object) and `user/addresses`
  (postal addresses).
- **Deliberately not exposed, changes or deletes data:** profile, e-mail and password changes;
  account deletion (`logout` is used only by `disconnect_account`); deleting or renaming rides and
  trips; removing or re-pairing devices (`portal/devices/bui|drive_unit|battery|my_ebike`,
  `login/generate_wifi_pin`, `custom_assistance_levels/reset`); GPX import; saving or changing routes
  and places; Strava upload and Strava / Komoot token or sync calls; dismissing dashboard hints.
- **Not exposed, little value:** `portal/devices/drive_units` (mostly contained in `get_my_ebikes`),
  `portal/activities/deletion_pending`, `user/next_best_action`, `news/image/`, `user/events`,
  `versionNumber.txt`.
- **Not exposed, not a data read:** routing and POI services (`navigation/service/calc_route`,
  `…/segments`, `…/nearby_search`).

Source of truth: the module that builds `origin + "/ebikeconnect/api/"` in the portal's `main.*.js`
plus the call sites in its lazy-loaded chunks. After a portal update, re-run that extraction to spot
new or changed endpoints.

## Portal quirks

Found while testing against a real account:

- Exports (`gpx`, `tcx`, `csv`) fail with `HTTP 400: Required request parameter 'timezone'` unless
  `timezone` = minutes east of UTC is sent (the portal's SPA sends `-(new Date).getTimezoneOffset()`).
  `export_activity` takes an IANA name and converts it.
- Ride filters accept **whole numbers only** (`HTTP 400: For input string: "1480.0"`): distance in
  metres, riding time in milliseconds. `search_rides` takes km / minutes and converts.
- Timestamps (`start_time`, `end_time`, `offset`) are Unix milliseconds as strings.
- A "trip" groups the day's rides; its start and end span the first departure to the last stop.
- Unknown IDs give `HTTP 400` ("No activity found." / "Route … not found"), not 404.
- Export sizes for one 1.5 km ride: CSV about 0.5 KB (summary only), GPX about 25 KB, TCX about
  190 KB. `get_dashboard_feed` is about 23 KB for 5 items because it includes GPS tracks.

## Known limitations

- **Unofficial API:** a portal change can break tools at any time.
- **Tested live** against one real Classic account with a scripted OAuth client and with Claude Code.
  Not tested with Claude Desktop, Claude web or other vendors' clients.
- Not verified with real data: `get_route_details`, `export_route_gpx`, and non-empty
  `get_saved_places` / `get_licenses` (the test account has none).
- An expired Bosch session is handled as `401` → tokens dropped → client re-authorises. This path is
  covered by tests with a fake Bosch but was not observed live. Whether the `REMEMBER` cookie ever
  rotates is unknown (the client persists a rotated value if one arrives).
- Runs on the `mcp` 1.x SDK (protocol up to 2025-11-25). Not implemented from the 2026-07-28
  revision: `server/discover` and per-request metadata, `MCP-Protocol-Version` / `Mcp-Method` header
  validation, Client ID Metadata Documents. Custom-scheme redirect URIs (e.g. `app://`) are still
  accepted at client registration.
- SQLite on a single container: fine for a small circle of users, not horizontally scalable.

## Development

```bash
pip install -e ".[dev]" && pytest
```

The 17 tests run the whole OAuth flow and the MCP calls against a fake Bosch (respx): user isolation,
token and code handling, session expiry, cookie rotation, export time zones, `disconnect_account`,
encryption at rest and the spec hardening. They do not touch the live portal.

Layout: `src/bosch_ebike_connect_classic/` with `app.py` (OAuth server, MCP guard, CORS), `tools.py`
(MCP tools), `client.py` (portal client), `store.py` (SQLite, encryption), `pages.py` (HTML),
`ratelimit.py`, `config.py`.

## Credits

The portal endpoints were found in the portal's own web bundle and in
[ebike-connect-js](https://github.com/FlorianCassayre/ebike-connect-js). The multi-user gateway
architecture is inspired by [missingmcp](https://github.com/VelkyVenik/missingmcp) and
[garmin_mcp](https://github.com/taxuspt/garmin_mcp); no code is shared with either.

## License

[MIT](LICENSE). The software is provided as is, without warranty. "Bosch" and "eBike Connect" are
trademarks of their owners; this project is not affiliated with them.
