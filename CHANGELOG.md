# Changelog

Versions follow [Semantic Versioning](https://semver.org). Newest first.

## 1.1.0

- No `.env` file needed: `GATEWAY_SECRET` is generated on first start and kept in the data folder
  (`gateway.secret`); `PUBLIC_URL` defaults to `http://localhost:8080`. Both can still be set as
  environment variables.
- Compose file reduced to the essentials; all persistent data lives in `./config`.

## 1.0.0

First release: multi-user, OAuth 2.1 protected remote MCP server for the Bosch eBike Connect Classic
portal with 17 tools, encrypted session storage and a Docker image (`linux/amd64`, `linux/arm64`).
