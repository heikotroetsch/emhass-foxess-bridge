"""Sensor platform: the state of the apply loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .entity import BridgeEntity
from .schedule_coordinator import ApplyResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BridgeConfigEntry
    from .schedule import SchedulerGroup
    from .schedule_coordinator import ScheduleCoordinator, ScheduleState

MAX_STATE_LENGTH = 255


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
    """Create the schedule sensors."""
    schedule = entry.runtime_data.schedule
    async_add_entities(
        [
            ScheduleSensor(schedule, PLANNED_SCHEDULE, lambda s: s.planned),
            ScheduleSensor(schedule, ACTIVE_SCHEDULE, lambda s: s.active),
            ApplyResultSensor(schedule),
            LastApplySensor(schedule),
        ]
    )


def _summarise(groups: tuple[SchedulerGroup, ...]) -> str:
    text = ", ".join(str(g) for g in groups) or "none"
    if len(text) > MAX_STATE_LENGTH:
        return f"{len(groups)} groups"
    return text


class ScheduleSensor(BridgeEntity, SensorEntity):
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


class ApplyResultSensor(BridgeEntity, SensorEntity):
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
            "api_remaining": data.api_remaining,
        }


class LastApplySensor(BridgeEntity, SensorEntity):
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
