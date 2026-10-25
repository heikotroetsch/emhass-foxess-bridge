"""Polling coordinator for live inverter values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FoxApiError, FoxApiRateLimitError
from .const import REALTIME_VARIABLES

if TYPE_CHECKING:
    from .api import FoxOpenApiClient
    from .data import BridgeConfigEntry


class BridgeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch the real-time variables the bridge and its sensors rely on."""

    config_entry: BridgeConfigEntry

    def __init__(
        self, *args: Any, client: FoxOpenApiClient, device_sn: str, **kwargs: Any
    ) -> None:
        """Keep the client and serial so updates need no runtime_data lookup."""
        super().__init__(*args, **kwargs)
        self._client = client
        self._device_sn = device_sn

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._client.async_get_real_time(
                self._device_sn, REALTIME_VARIABLES
            )
        except FoxApiRateLimitError as exc:
            # Quota resets at midnight; back off for an hour instead of hammering.
            raise UpdateFailed(exc, retry_after=3600) from exc
        except FoxApiError as exc:
            raise UpdateFailed(exc) from exc
