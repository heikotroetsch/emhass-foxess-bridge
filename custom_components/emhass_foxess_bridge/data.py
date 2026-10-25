"""Runtime data types for the EMHASS FoxESS Bridge integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .backend import FoxBackend, FoxDevice
    from .schedule_coordinator import ScheduleCoordinator


type BridgeConfigEntry = ConfigEntry[BridgeRuntimeData]


@dataclass
class BridgeRuntimeData:
    """Objects shared by the platforms of one config entry."""

    device: FoxDevice
    backend: FoxBackend
    schedule: ScheduleCoordinator
