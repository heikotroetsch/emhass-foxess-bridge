"""
Minimal async client for the FoxESS Cloud Open API.

Only the endpoints the bridge needs are implemented. Paths and the signature
scheme follow the official Open API document and were verified against an
H3-5.0-E inverter (master 1.91, manager 1.81) on 2026-10-25.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import time
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

BASE_URL = "https://www.foxesscloud.com"
USER_AGENT = (
    "emhass-foxess-bridge/0.1 (+https://github.com/heikotroetsch/emhass-foxess-bridge)"
)

ERRNO_OK = 0
ERRNO_RATE_LIMIT = 40400  # documented: daily request limit exceeded
ERRNO_DEVICE_NOT_FOUND = 41930  # observed: "Device does not exist or is already in use"
# An invalid key or signature is answered with HTTP 401 (observed 2026-10-25).
AUTH_HTTP_STATUSES = frozenset({401, 403})

REQUEST_TIMEOUT_S = 20
# The Open API allows one write call every two seconds per endpoint.
MIN_SECONDS_BETWEEN_WRITES = 2.0

PATH_DEVICE_DETAIL = "/op/v1/device/detail"
PATH_REAL_QUERY = "/op/v1/device/real/query"
PATH_SCHEDULER_FLAG = "/op/v0/device/scheduler/get/flag"
PATH_SCHEDULER_GET = "/op/v0/device/scheduler/get"
PATH_SCHEDULER_ENABLE = "/op/v0/device/scheduler/enable"
PATH_SCHEDULER_SET = "/op/v0/device/scheduler/set"
PATH_ACCESS_COUNT = "/op/v0/user/getAccessCount"
PATH_BATTERY_SOC = "/op/v0/device/battery/soc/get"


class FoxApiError(Exception):
    """Base error for the FoxESS Open API client."""

    def __init__(self, message: str, errno: int | None = None) -> None:
        """Store the API error number alongside the message."""
        super().__init__(message)
        self.errno = errno


class FoxApiCommunicationError(FoxApiError):
    """Network level failure (timeout, DNS, HTTP error)."""


class FoxApiAuthError(FoxApiError):
    """The API key or signature was rejected (HTTP 401/403)."""


class FoxApiResponseError(FoxApiError):
    """The API answered with a non-zero errno."""


class FoxApiRateLimitError(FoxApiResponseError):
    """The daily request quota is exhausted (errno 40400)."""


class FoxApiDeviceNotFoundError(FoxApiResponseError):
    """The serial number is unknown to this account (errno 41930)."""


def build_signature(path: str, token: str, timestamp_ms: int | str) -> str:
    """Return the request signature the Open API expects."""
    raw = f"{path}\r\n{token}\r\n{timestamp_ms}"
    # MD5 is mandated by the FoxESS API; it is not used for security here.
    return hashlib.md5(raw.encode()).hexdigest()  # noqa: S324


class FoxOpenApiClient:
    """Thin wrapper around the handful of Open API calls the bridge uses."""

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        base_url: str = BASE_URL,
    ) -> None:
        """Create a client bound to one API key and aiohttp session."""
        self._api_key = api_key
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._write_lock = asyncio.Lock()
        self._last_write_monotonic = 0.0

    def _headers(self, path: str) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        return {
            "token": self._api_key,
            "timestamp": timestamp_ms,
            "signature": build_signature(path, self._api_key, timestamp_ms),
            "lang": "en",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
    ) -> Any:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                response = await self._session.request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    json=json,
                    headers=self._headers(path),
                )
                if response.status in AUTH_HTTP_STATUSES:
                    msg = f"FoxESS rejected the API key (HTTP {response.status})"
                    raise FoxApiAuthError(msg)
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except TimeoutError as exc:
            msg = f"Timeout calling {path}"
            raise FoxApiCommunicationError(msg) from exc
        except (aiohttp.ClientError, socket.gaierror) as exc:
            msg = f"Error calling {path}: {exc}"
            raise FoxApiCommunicationError(msg) from exc

        if not isinstance(payload, dict):
            msg = f"Unexpected payload from {path}: {payload!r}"
            raise FoxApiResponseError(msg)

        errno = payload.get("errno", ERRNO_OK)
        if errno == ERRNO_RATE_LIMIT:
            msg = "FoxESS Open API request limit exceeded"
            raise FoxApiRateLimitError(msg, errno=errno)
        if errno == ERRNO_DEVICE_NOT_FOUND:
            msg = f"FoxESS does not know this device: {payload.get('msg')}"
            raise FoxApiDeviceNotFoundError(msg, errno=errno)
        if errno != ERRNO_OK:
            msg = f"FoxESS Open API error {errno} on {path}: {payload.get('msg')}"
            raise FoxApiResponseError(msg, errno=errno)
        return payload.get("result")

    async def _write(self, path: str, json: Any) -> Any:
        """POST with the write pacing the API requires."""
        async with self._write_lock:
            wait = MIN_SECONDS_BETWEEN_WRITES - (
                time.monotonic() - self._last_write_monotonic
            )
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await self._request("POST", path, json=json)
            finally:
                self._last_write_monotonic = time.monotonic()

    async def async_get_device_detail(self, device_sn: str) -> dict[str, Any]:
        """Return inverter details including firmware and scheduler support."""
        result = await self._request(
            "GET", PATH_DEVICE_DETAIL, params={"sn": device_sn}
        )
        if not isinstance(result, dict):
            msg = "Device detail result is not an object"
            raise FoxApiResponseError(msg)
        return result

    async def async_get_real_time(
        self, device_sn: str, variables: Iterable[str]
    ) -> dict[str, Any]:
        """Return the latest value per requested variable plus the sample time."""
        result = await self._request(
            "POST",
            PATH_REAL_QUERY,
            json={"sns": [device_sn], "variables": list(variables)},
        )
        values: dict[str, Any] = {}
        for device in result or []:
            if device.get("deviceSN") not in (None, device_sn):
                continue
            values["time"] = device.get("time")
            for entry in device.get("datas", []):
                values[entry["variable"]] = entry.get("value")
        return values

    async def async_get_scheduler_flag(self, device_sn: str) -> dict[str, Any]:
        """Return {'enable': bool, 'support': bool} for the mode scheduler."""
        result = await self._request(
            "POST", PATH_SCHEDULER_FLAG, json={"deviceSN": device_sn}
        )
        return result if isinstance(result, dict) else {}

    async def async_get_scheduler(self, device_sn: str) -> list[dict[str, Any]]:
        """Return the configured scheduler groups (empty when none)."""
        result = await self._request(
            "POST", PATH_SCHEDULER_GET, json={"deviceSN": device_sn}
        )
        if isinstance(result, dict):
            return list(result.get("groups") or [])
        return []

    async def async_set_scheduler_groups(
        self, device_sn: str, groups: list[dict[str, Any]]
    ) -> None:
        """Replace all scheduler groups on the inverter."""
        await self._write(
            PATH_SCHEDULER_ENABLE, {"deviceSN": device_sn, "groups": groups}
        )

    async def async_set_scheduler_enabled(
        self, device_sn: str, *, enabled: bool
    ) -> None:
        """Switch the mode scheduler master flag."""
        await self._write(
            PATH_SCHEDULER_SET, {"deviceSN": device_sn, "enable": int(enabled)}
        )

    async def async_get_battery_soc(self, device_sn: str) -> dict[str, Any]:
        """Return {'minSoc': int, 'minSocOnGrid': int} as configured on the inverter."""
        result = await self._request("GET", PATH_BATTERY_SOC, params={"sn": device_sn})
        return result if isinstance(result, dict) else {}

    async def async_get_access_count(self) -> dict[str, Any]:
        """Return the daily request quota ('total' and 'remaining')."""
        result = await self._request("GET", PATH_ACCESS_COUNT)
        return result if isinstance(result, dict) else {}
