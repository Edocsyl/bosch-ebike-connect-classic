"""MCP tools. The calling user is taken from a contextvar set by the auth guard,
so tools never see (or accept) an account parameter: one user can only reach
their own Bosch session."""

from __future__ import annotations

import asyncio
import json
import re
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .client import AuthRequired, BoschClient, EBikeError
from .store import Store

current_account: ContextVar[str | None] = ContextVar("current_account", default=None)

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SERIES_KEYS ={"cadence", "heart_rate", "speed", "coordinates", "portal_altitudes", "power_output"}

EXPIRED = ("Error: the eBike Connect session expired. Disconnect and reconnect this "
           "connector to sign in again.")


def _res(text: str) -> CallToolResult:
    """Tool result; text starting with 'Error:' is flagged isError (MCP tool-error convention)."""
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          isError=text.startswith("Error:"))


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def strip_series(detail: dict) -> dict:
    out = {k: v for k, v in detail.items() if k not in SERIES_KEYS}
    out["_series_omitted"] = sorted(k for k in SERIES_KEYS if k in detail)
    return out


def create_mcp(store: Store, host: str = "0.0.0.0", port: int = 8080) -> FastMCP:
    mcp = FastMCP("bosch-ebike-connect-classic", host=host, port=port,
                  stateless_http=True, json_response=True)

    async def run(fn) -> CallToolResult:
        key = current_account.get()
        if not key:
            return _res(EXPIRED)
        acc = store.get_account(key)
        if not acc:
            return _res(EXPIRED)
        client = BoschClient(acc["remember"], on_rotate=lambda t: store.update_remember(key, t))
        try:
            result = await fn(client)
            return _res(result if isinstance(result, str) else _dump(result))
        except AuthRequired:
            # Drop our tokens: the next MCP call gets a 401 challenge and the
            # client re-runs the OAuth sign-in by itself.
            store.revoke_tokens(key)
            return _res(EXPIRED)
        except EBikeError as e:
            return _res(f"Error: {e}")
        except Exception as e:  # network errors etc.
            return _res(f"Error: {type(e).__name__}: {e}")

    @mcp.tool()
    async def get_user_profile() -> CallToolResult:
        """Profile of the connected eBike Connect account (name, units, settings)."""
        return await run(lambda c: c.user_profile())

    @mcp.tool()
    async def get_my_ebikes() -> CallToolResult:
        """Bikes and components: drive unit, display (BUI), batteries, firmware, serials."""
        return await run(lambda c: c.my_ebikes())

    @mcp.tool()
    async def get_statistics() -> CallToolResult:
        """Lifetime statistics (distance, riding time, ...) as on the portal dashboard."""
        return await run(lambda c: c.statistics())

    @mcp.tool()
    async def get_activities(max_items: int = 20, offset: int | None = None) -> CallToolResult:
        """List recent trips with their rides (headers only, no graph data).

        Args:
            max_items: Number of trips to return (1-100, default 20).
            offset: Pagination cursor in epoch milliseconds; returns trips older than this.
                Defaults to now. Use the oldest start_time of the previous page to page.
        """
        n = max(1, min(int(max_items), 100))
        return await run(lambda c: c.activity_headers(n, offset))

    @mcp.tool()
    async def get_activity_details(
        activity_id: str,
        kind: Literal["ride", "trip"] = "ride",
        include_series: bool = False,
    ) -> CallToolResult:
        """Full details of one ride or trip (power, calories, assistance levels, battery use).

        Args:
            activity_id: ID from get_activities.
            kind: 'ride' (single ride) or 'trip' (group of rides).
            include_series: Include time series (speed, cadence, heart rate, GPS, altitude,
                power). Large; default False returns aggregates only.
        """
        if not activity_id.isdigit():
            return _res("Error: activity_id must be numeric")

        async def go(c: BoschClient):
            d = await c.activity_details(kind, activity_id)
            return d if include_series or not isinstance(d, dict) else strip_series(d)

        return await run(go)

    @mcp.tool()
    async def export_activity(
        activity_id: str,
        format: Literal["gpx", "tcx", "csv"] = "gpx",
        kind: Literal["ride", "trip"] = "ride",
        timezone: str = "Europe/Zurich",
    ) -> CallToolResult:
        """Export an activity as GPX, TCX or CSV text.

        CSV is a one-line summary; GPX has the track (about 25 KB for a 1.5 km ride); TCX is
        very large (about 190 KB for the same ride): prefer gpx or csv.

        Args:
            activity_id: ID from get_activities.
            format: gpx | tcx | csv.
            kind: 'ride' or 'trip'.
            timezone: IANA time zone for local times in the export (default Europe/Zurich).
        """
        if not activity_id.isdigit():
            return _res("Error: activity_id must be numeric")
        try:
            offset = datetime.now(ZoneInfo(timezone)).utcoffset()
            minutes = int(offset.total_seconds() // 60)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return _res(f"Error: unknown time zone '{timezone}' (use e.g. Europe/Zurich)")
        return await run(lambda c: c.export_activity(kind, activity_id, format, minutes))

    @mcp.tool()
    async def get_routes(max_items: int = 25) -> CallToolResult:
        """Saved navigation routes (headers)."""
        n = max(1, min(int(max_items), 100))
        return await run(lambda c: c.routes(n))

    @mcp.tool()
    async def search_rides(
        title: str | None = None,
        min_distance_km: float | None = None,
        max_distance_km: float | None = None,
        min_duration_min: float | None = None,
        max_duration_min: float | None = None,
        max_items: int = 20,
        offset: int | None = None,
    ) -> CallToolResult:
        """Search single rides like the portal's filter dialog (title, distance, duration).

        Args:
            title: Text the ride title must contain.
            min_distance_km / max_distance_km: Distance range in kilometres.
            min_duration_min / max_duration_min: Riding-time range in minutes.
            max_items: 1-100 (default 20).
            offset: Pagination cursor in epoch milliseconds (rides older than this).

        The result also contains total_number_of_filtered_bike_rides.
        """
        n = max(1, min(int(max_items), 100))

        def conv(v, factor):  # the portal wants whole numbers: metres and milliseconds
            return None if v is None else max(0, int(round(v * factor)))

        return await run(lambda c: c.search_rides(
            n, offset, title=title,
            min_distance=conv(min_distance_km, 1000), max_distance=conv(max_distance_km, 1000),
            min_driving_time=conv(min_duration_min, 60000), max_driving_time=conv(max_duration_min, 60000)))

    @mcp.tool()
    async def get_saved_places() -> CallToolResult:
        """Saved destinations/favourites from the navigation section."""
        return await run(lambda c: c.saved_places())

    @mcp.tool()
    async def get_route_details(route_id: str, segmented: bool = False) -> CallToolResult:
        """Details of a saved route.

        Args:
            route_id: ID from get_routes.
            segmented: True for routes planned with the route builder (segments),
                False for imported GPX routes.
        """
        if not ID_RE.match(route_id):
            return _res("Error: invalid route_id")
        return await run(lambda c: c.route_details(route_id, segmented))

    @mcp.tool()
    async def export_route_gpx(route_id: str) -> CallToolResult:
        """Export a saved route as GPX text."""
        if not ID_RE.match(route_id):
            return _res("Error: invalid route_id")
        return await run(lambda c: c.export_route_gpx(route_id))

    @mcp.tool()
    async def get_weather_forecast(
        latitude: float, longitude: float, period: Literal["daily", "hourly"] = "daily"
    ) -> CallToolResult:
        """Weather forecast from the portal's weather service for a location.

        Args:
            latitude: -90..90 (rounded to 2 decimals).
            longitude: -180..180 (rounded to 2 decimals).
            period: 'daily' or 'hourly'.
        """
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return _res("Error: latitude/longitude out of range")
        return await run(lambda c: c.weather(latitude, longitude, period))

    @mcp.tool()
    async def get_dashboard_feed(
        count: int = 5, offset: int | None = None,
        types: list[Literal["news", "activities"]] | None = None,
    ) -> CallToolResult:
        """The portal dashboard feed (news and latest activities).

        Args:
            count: 1-50 items (default 5).
            offset: Pagination cursor in epoch milliseconds.
            types: Subset of ['news', 'activities'] (default both).
        """
        n = max(1, min(int(count), 50))
        return await run(lambda c: c.dashboard_stream(n, offset, tuple(types or ("news", "activities"))))

    @mcp.tool()
    async def get_integrations() -> CallToolResult:
        """Status of the Strava and Komoot connections (connected, upload option, last sync)."""
        async def go(c: BoschClient):
            calls = {"strava_status": c.strava_status, "strava_upload_option": c.strava_upload_option,
                     "komoot_status": c.komoot_status, "komoot_upload_option": c.komoot_upload_option}
            res = await asyncio.gather(*(f() for f in calls.values()), return_exceptions=True)
            for r in res:
                if isinstance(r, AuthRequired):
                    raise r
            return {k: (f"error: {r}" if isinstance(r, Exception) else r) for k, r in zip(calls, res)}

        return await run(go)

    @mcp.tool()
    async def get_user_settings() -> CallToolResult:
        """Account settings of the portal (e.g. whether fitness data is allowed)."""
        return await run(lambda c: c.user_settings())

    @mcp.tool()
    async def get_licenses() -> CallToolResult:
        """Purchased licences/products of the account (e.g. Premium Custom Modes)."""
        return await run(lambda c: c.licenses())

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                          idempotentHint=True, openWorldHint=True))
    async def disconnect_account(confirm: bool = False) -> CallToolResult:
        """Sign out of eBike Connect and delete everything this server stores for you.

        Sends the portal logout request (the portal does not invalidate the cookie), then deletes the stored session, all
        access/refresh tokens and pending codes. Afterwards this connector must be
        re-authorised. This is the only tool that changes anything.

        Args:
            confirm: Must be true. Without it nothing happens and the effect is only described.
        """
        key = current_account.get()
        if not key:
            return _res(EXPIRED)
        if not confirm:
            return _res("Nothing was changed. Call disconnect_account with confirm=true to sign out of "
                        "eBike Connect and delete the stored session and tokens; the connector must "
                        "then be authorised again.")
        acc = store.get_account(key)
        portal = "no stored session"
        if acc:
            try:
                await BoschClient(acc["remember"]).logout()
                portal = ("logout request sent to ebike-connect.com (HTTP 200), but the portal keeps the old "
                          "session cookie valid; change your Bosch password to revoke it there")
            except AuthRequired:
                portal = "session had already expired at ebike-connect.com"
            except Exception as e:  # network/portal error: still delete our copy
                portal = f"portal sign-out failed ({type(e).__name__}); the session may stay valid there"
        store.delete_account(key)
        return _res(f"Disconnected. {portal.capitalize()}. Stored session and tokens deleted; reconnect to use the tools again.")

    return mcp
