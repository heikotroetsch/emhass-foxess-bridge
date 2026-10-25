"""Runtime data types for the EMHASS FoxESS Bridge integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api import FoxOpenApiClient
    from .coordinator import BridgeCoordinator
    from .schedule_coordinator import ScheduleCoordinator


type BridgeConfigEntry = ConfigEntry[BridgeRuntimeData]


@dataclass
class BridgeRuntimeData:
    """Objects shared by the platforms of one config entry."""

    client: FoxOpenApiClient
    coordinator: BridgeCoordinator
    schedule: ScheduleCoordinator
    device_sn: str
    device_detail: dict[str, Any]
    min_soc_on_grid: int
