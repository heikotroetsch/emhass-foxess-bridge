"""Binary sensor flagging a missing or outdated EMHASS plan."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)

from .entity import BridgeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BridgeConfigEntry
    from .schedule_coordinator import ScheduleCoordinator

PLAN_STALE = BinarySensorEntityDescription(
    key="plan_stale",
    name="Plan stale",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the plan-stale indicator."""
    async_add_entities([PlanStaleSensor(entry.runtime_data.schedule)])


class PlanStaleSensor(BridgeEntity, BinarySensorEntity):
    """On when the bridge has no usable plan and therefore keeps self-use."""

    entity_description = PLAN_STALE

    def __init__(self, coordinator: ScheduleCoordinator) -> None:
        """Bind to the schedule coordinator."""
        super().__init__(coordinator, PLAN_STALE.key)

    @property
    def is_on(self) -> bool:
        """True when the plan is missing, unreadable or too old."""
        return self.coordinator.data.plan_stale

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain why, and when the plan was last updated."""
        data = self.coordinator.data
        return {
            "reason": data.stale_reason,
            "plan_updated_at": (
                data.plan_updated_at.isoformat() if data.plan_updated_at else None
            ),
        }
