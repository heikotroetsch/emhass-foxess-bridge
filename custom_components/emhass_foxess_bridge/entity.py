"""Base entity: the bridge's own service device, linked to the inverter device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, NAME
from .schedule_coordinator import ScheduleCoordinator


class BridgeEntity(CoordinatorEntity[ScheduleCoordinator]):
    """
    Entity on the bridge device for one inverter.

    Device identifiers are unique per config entry since Home Assistant
    2026.10, so the bridge cannot join the device ha-foxess-openapi created.
    It gets its own service device and points at the inverter with
    ``via_device_id``.
    """

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: ScheduleCoordinator, key: str) -> None:
        """Derive unique id and device info from the resolved inverter."""
        super().__init__(coordinator)
        device = coordinator.config_entry.runtime_data.device
        self._attr_unique_id = f"{device.serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.serial)},
            name=f"EMHASS bridge {device.name}",
            model=NAME,
            entry_type=DeviceEntryType.SERVICE,
            via_device_id=device.device_id,
        )
