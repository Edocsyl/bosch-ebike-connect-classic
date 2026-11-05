# Changelog

Versions follow [Semantic Versioning](https://semver.org). Newest first.

## 1.0.0

First release: multi-user, OAuth 2.1 protected remote MCP server for the Bosch eBike Connect Classic
portal.

- 17 tools (rides, statistics, bikes, exports, search, routes, weather, dashboard, settings, licences) and
  `disconnect_account`; read-only otherwise.
- OAuth 2.1 with dynamic client registration and PKCE; works with any MCP client that supports remote
  servers (Streamable HTTP).
- Encrypted session storage; no `.env` file needed: `GATEWAY_SECRET` is generated on first start and kept in
  the data folder, all persistent data lives in `./config`.
- Docker image for `linux/amd64` and `linux/arm64`.
