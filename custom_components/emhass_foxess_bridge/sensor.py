"""Sensor platform: live inverter values and the state of the apply loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower

from .entity import BridgeEntity
from .schedule_coordinator import ApplyResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import BridgeCoordinator
    from .data import BridgeConfigEntry
    from .schedule import SchedulerGroup
    from .schedule_coordinator import ScheduleCoordinator, ScheduleState

MAX_STATE_LENGTH = 255


# FoxESS reports power in kW. EMHASS reads raw sensor states and assumes W,
# so the live power sensors are exposed in W.
POWER_KEYS: frozenset[str] = frozenset(
    {
        "pvPower",
        "loadsPower",
        "gridConsumptionPower",
        "feedinPower",
        "batChargePower",
        "batDischargePower",
    }
)
KW_TO_W = 1000


def _power(key: str, name: str) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        name=name,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    )


LIVE_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
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

PLANNED_SCHEDULE = SensorEntityDescription(
    key="planned_schedule", name="Planned schedule", icon="mdi:calendar-clock"
)
ACTIVE_SCHEDULE = SensorEntityDescription(
    key="active_schedule", name="Active schedule", icon="mdi:calendar-check"
)
APPLY_RESULT = SensorEntityDescription(
    key="apply_result",
    name="Apply result",
    device_class=SensorDeviceClass.ENUM,
    options=[result.value for result in ApplyResult],
    entity_category=EntityCategory.DIAGNOSTIC,
)
LAST_APPLY = SensorEntityDescription(
    key="last_apply",
    name="Last apply",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the live sensors and the schedule sensors."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        LiveSensor(runtime.coordinator, description)
        for description in LIVE_DESCRIPTIONS
    ]
    entities += [
        ScheduleSensor(runtime.schedule, PLANNED_SCHEDULE, lambda s: s.planned),
        ScheduleSensor(runtime.schedule, ACTIVE_SCHEDULE, lambda s: s.active),
        ApplyResultSensor(runtime.schedule),
        LastApplySensor(runtime.schedule),
    ]
    async_add_entities(entities)


class LiveSensor(BridgeEntity["BridgeCoordinator"], SensorEntity):
    """A single real-time value from the inverter."""

    def __init__(
        self, coordinator: BridgeCoordinator, description: SensorEntityDescription
    ) -> None:
        """Bind the description to the coordinator."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Return the latest value for this variable, power converted to W."""
        key = self.entity_description.key
        value = self.coordinator.data.get(key)
        if key in POWER_KEYS and value is not None:
            try:
                return round(float(value) * KW_TO_W, 1)
            except TypeError, ValueError:
                return None
        return value


def _summarise(groups: tuple[SchedulerGroup, ...]) -> str:
    text = ", ".join(str(g) for g in groups) or "none"
    if len(text) > MAX_STATE_LENGTH:
        return f"{len(groups)} groups"
    return text


class ScheduleSensor(BridgeEntity["ScheduleCoordinator"], SensorEntity):
    """Human readable schedule; the groups travel in the attributes."""

    def __init__(
        self,
        coordinator: ScheduleCoordinator,
        description: SensorEntityDescription,
        pick: Callable[[ScheduleState], tuple[SchedulerGroup, ...]],
    ) -> None:
        """Bind to planned or active groups."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._pick = pick

    @property
    def native_value(self) -> str:
        """Comma separated groups, or 'none'."""
        return _summarise(self._pick(self.coordinator.data))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Groups in API shape plus the master flag for the active schedule."""
        data = self.coordinator.data
        attributes: dict[str, Any] = {
            "groups": [g.to_api() for g in self._pick(data)],
        }
        if self.entity_description.key == ACTIVE_SCHEDULE.key:
            attributes["scheduler_enabled"] = data.scheduler_enabled
        return attributes


class ApplyResultSensor(BridgeEntity["ScheduleCoordinator"], SensorEntity):
    """Outcome of the last apply run."""

    entity_description = APPLY_RESULT

    def __init__(self, coordinator: ScheduleCoordinator) -> None:
        """Bind to the schedule coordinator."""
        super().__init__(coordinator, APPLY_RESULT.key)

    @property
    def native_value(self) -> str:
        """One of the ApplyResult values."""
        return self.coordinator.data.last_result.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Detail text and, when paused, the time writes resume."""
        data = self.coordinator.data
        return {
            "detail": data.last_detail,
            "bridge_enabled": self.coordinator.bridge_enabled,
            "dry_run": self.coordinator.dry_run,
            "writes_blocked_until": (
                data.writes_blocked_until.isoformat()
                if data.writes_blocked_until
                else None
            ),
        }


class LastApplySensor(BridgeEntity["ScheduleCoordinator"], SensorEntity):
    """When the apply loop last ran."""

    entity_description = LAST_APPLY

    def __init__(self, coordinator: ScheduleCoordinator) -> None:
        """Bind to the schedule coordinator."""
        super().__init__(coordinator, LAST_APPLY.key)

    @property
    def native_value(self) -> datetime | None:
        """Timestamp of the last run."""
        return self.coordinator.data.last_apply_at

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Result and detail of that run."""
        data = self.coordinator.data
        return {"result": data.last_result.value, "detail": data.last_detail}
