"""Sensor platform exposing live inverter values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower

from .entity import BridgeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import BridgeCoordinator
    from .data import BridgeConfigEntry


def _power(key: str, name: str) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        name=name,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    )


ENTITY_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="SoC",
        name="Battery SoC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _power("pvPower", "PV power"),
    _power("loadsPower", "Load power"),
    _power("gridConsumptionPower", "Grid import power"),
    _power("feedinPower", "Grid export power"),
    _power("batChargePower", "Battery charge power"),
    _power("batDischargePower", "Battery discharge power"),
    SensorEntityDescription(
        key="runningState",
        name="Running state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one sensor per real-time variable."""
    async_add_entities(
        BridgeSensor(entry.runtime_data.coordinator, description)
        for description in ENTITY_DESCRIPTIONS
    )


class BridgeSensor(BridgeEntity, SensorEntity):
    """A single real-time value from the inverter."""

    def __init__(
        self,
        coordinator: BridgeCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Bind the description to the coordinator."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Return the latest value for this variable."""
        return self.coordinator.data.get(self.entity_description.key)
