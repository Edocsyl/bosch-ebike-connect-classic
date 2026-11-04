"""Async client for the (undocumented) eBike Connect portal API.

Endpoints and headers come from the portal's Angular bundle (module that builds
``origin + "/ebikeconnect/api/"``) and FlorianCassayre/ebike-connect-js. The SPA
sends ``Accept: application/vnd.ebike-connect.com.v4+json`` and ``Protect-from: CSRF``.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

BASE = "https://www.ebike-connect.com"
API = f"{BASE}/ebikeconnect/api"
COOKIE = "REMEMBER"
HEADERS = {
    "Accept": "application/vnd.ebike-connect.com.v4+json, application/json",
    "Protect-from": "CSRF",
    "Cache-Control": "no-cache",
    "User-Agent": "Mozilla/5.0 (compatible; bosch-ebike-connect-classic)",
}


class EBikeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class AuthRequired(EBikeError):
    """Credentials rejected / session expired."""


def _err_message(resp: httpx.Response) -> str:
    try:
        errs = resp.json().get("errors") or []
        return ", ".join(str(e.get("message", "?")) for e in errs) or resp.text[:200]
    except Exception:
        return resp.text[:200]


async def login(username: str, password: str, http: httpx.AsyncClient | None = None) -> str:
    """POST credentials, return the REMEMBER cookie. Raises AuthRequired on rejection."""
    own = http is None
    http = http or httpx.AsyncClient(timeout=20)
    try:
        resp = await http.post(
            f"{API}/portal/login/public",
            json={"username": username, "password": password, "rememberme": True},
            headers=HEADERS,
        )
    finally:
        if own:
            await http.aclose()
    if resp.status_code >= 400:
        cls = AuthRequired if resp.status_code in (400, 401, 403) else EBikeError
        raise cls(resp.status_code, _err_message(resp))
    token = resp.cookies.get(COOKIE)
    if not token:
        raise AuthRequired(resp.status_code, "login returned no REMEMBER cookie")
    return token


class BoschClient:
    """Per-request client bound to one user's REMEMBER cookie.

    ``on_rotate`` is called with a new cookie value if the portal rotates it,
    so the caller can persist it before it is used again.
    """

    def __init__(self, remember: str, on_rotate: Callable[[str], None] | None = None,
                 timeout: float = 30.0) -> None:
        self.remember, self.on_rotate, self.timeout = remember, on_rotate, timeout

    async def request(self, path: str, *, params: "dict[str, Any] | list[tuple[str, Any]] | None" = None,
                      raw: bool = False, method: str = "GET"):
        items = params.items() if isinstance(params, dict) else (params or [])
        query = [(k, v) for k, v in items if v is not None]
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False,
                                     cookies={COOKIE: self.remember}) as http:
            resp = await http.request(method, f"{API}/{path.lstrip('/')}", params=query,
                                      headers=HEADERS, json={} if method == "POST" else None)
        rotated = resp.cookies.get(COOKIE)
        if rotated and rotated != self.remember:
            self.remember = rotated
            if self.on_rotate:
                self.on_rotate(rotated)
        if resp.status_code == 401:
            raise AuthRequired(401, "session expired")
        if resp.status_code >= 400:
            raise EBikeError(resp.status_code, _err_message(resp))
        if method == "POST" and not resp.content:
            return {"status": resp.status_code}
        return resp.text if raw else resp.json()

    # --- read-only endpoints ----------------------------------------------------
    async def api_version(self):
        return await self.request("api_version")

    async def check_session(self):
        return await self.request("portal/check_session/public")

    async def user_profile(self):
        return await self.request("user/profile")

    async def my_ebikes(self):
        return await self.request("portal/devices/my_ebikes")

    async def statistics(self):
        return await self.request("portal/statistics/all_statistics")

    async def activity_headers(self, max_items: int = 20, offset: int | None = None):
        return await self.request(
            "portal/activities/trip/headers",
            params={"max": max_items, "offset": offset if offset is not None else int(time.time() * 1000)},
        )

    async def activity_details(self, kind: str, activity_id: str):
        return await self.request(f"activities/{kind}/details/{activity_id}")

    async def export_activity(self, kind: str, activity_id: str, fmt: str, tz_offset_min: int = 0):
        """The portal requires ``timezone`` = minutes east of UTC (its SPA sends
        ``-(new Date).getTimezoneOffset()``); without it the export answers HTTP 400."""
        path = (f"gpx/export/{kind}/{activity_id}" if fmt == "gpx"
                else f"activities/{kind}/{activity_id}/export/{fmt}")
        return await self.request(path, params={"timezone": tz_offset_min}, raw=True)

    async def routes(self, max_items: int = 25):
        return await self.request("portal/navigation/my_items/route_headers",
                                  params={"max_items": max_items})

    async def logout(self):
        """POST portal/logout/public (what the portal's own logout button sends)."""
        return await self.request("portal/logout/public", method="POST")

    # --- added after reading the portal's lazy-loaded chunks (all GET) ---------------
    async def search_rides(self, max_items: int = 20, offset: int | None = None, *,
                           title: str | None = None, min_distance: float | None = None,
                           max_distance: float | None = None, min_driving_time: float | None = None,
                           max_driving_time: float | None = None):
        """portal/activities/ride/headers: same list as the portal's filter dialog."""
        return await self.request("portal/activities/ride/headers", params={
            "max": max_items,
            "offset": offset if offset is not None else int(time.time() * 1000),
            "title": title, "minTotalDistance": min_distance, "maxTotalDistance": max_distance,
            "minDrivingTime": min_driving_time, "maxDrivingTime": max_driving_time})

    async def saved_places(self):
        return await self.request("portal/navigation/my_items/destinations")

    async def route_details(self, route_id: str, segmented: bool = False):
        base = ("portal/navigation/my_items/segmented_route_details/" if segmented
                else "portal/navigation/my_items/route_details/")
        return await self.request(base + route_id)

    async def export_route_gpx(self, route_id: str):
        return await self.request(f"gpx/export/route/{route_id}", raw=True)

    async def weather(self, latitude: float, longitude: float, period: str = "daily"):
        # the SPA sends coordinates rounded to 2 decimals (toFixed(2))
        return await self.request(f"portal/weather/forecast/{period}/",
                                  params={"latitude": f"{latitude:.2f}", "longitude": f"{longitude:.2f}"})

    async def dashboard_stream(self, count: int = 5, offset: int | None = None,
                               types: tuple[str, ...] = ("news", "activities")):
        params = [("count", count), *[("types", t) for t in types],
                  ("offset", offset if offset is not None else int(time.time() * 1000))]
        return await self.request("portal/dashboard/stream", params=params)

    async def user_settings(self):
        return await self.request("settings", params={"filter": "user"})

    async def licenses(self):
        return await self.request("purchase/licenses")

    async def strava_status(self):
        return await self.request("portal/strava/connect_status")

    async def strava_upload_option(self):
        return await self.request("portal/strava/upload_option")

    async def komoot_status(self):
        return await self.request("portal/komoot/connect_status")

    async def komoot_upload_option(self):
        return await self.request("portal/komoot/upload_option")
