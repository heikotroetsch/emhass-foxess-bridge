"""Base entity carrying the inverter device information."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import BridgeCoordinator


class BridgeEntity(CoordinatorEntity[BridgeCoordinator]):
    """Entity bound to one FoxESS inverter."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: BridgeCoordinator, key: str) -> None:
        """Derive unique id and device info from the config entry."""
        super().__init__(coordinator)
        runtime = coordinator.config_entry.runtime_data
        detail = runtime.device_detail
        self._attr_unique_id = f"{runtime.device_sn}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.device_sn)},
            manufacturer="FoxESS",
            model=detail.get("deviceType"),
            name=f"FoxESS {detail.get('deviceType', 'inverter')}",
            serial_number=runtime.device_sn,
            sw_version=(
                f"master {detail.get('masterVersion')} / "
                f"manager {detail.get('managerVersion')}"
            ),
        )
