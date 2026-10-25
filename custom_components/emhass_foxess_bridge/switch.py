"""Switches that gate writes: bridge enabled (default off) and dry run (default on)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory

from .const import CONF_DRY_RUN, CONF_ENABLED, DEFAULT_DRY_RUN, DEFAULT_ENABLED
from .entity import BridgeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import BridgeConfigEntry
    from .schedule_coordinator import ScheduleCoordinator

SWITCHES: tuple[tuple[SwitchEntityDescription, str, bool], ...] = (
    (
        SwitchEntityDescription(
            key="bridge_enabled",
            name="Bridge enabled",
            icon="mdi:power",
            entity_category=EntityCategory.CONFIG,
        ),
        CONF_ENABLED,
        DEFAULT_ENABLED,
    ),
    (
        SwitchEntityDescription(
            key="dry_run",
            name="Dry run",
            icon="mdi:test-tube",
            entity_category=EntityCategory.CONFIG,
        ),
        CONF_DRY_RUN,
        DEFAULT_DRY_RUN,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: BridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the two gating switches."""
    async_add_entities(
        OptionSwitch(entry.runtime_data.schedule, description, option, default)
        for description, option, default in SWITCHES
    )


class OptionSwitch(BridgeEntity["ScheduleCoordinator"], SwitchEntity):
    """A switch persisted as a config entry option; toggling re-runs the apply loop."""

    def __init__(
        self,
        coordinator: ScheduleCoordinator,
        description: SwitchEntityDescription,
        option: str,
        default: bool,  # noqa: FBT001
    ) -> None:
        """Bind the switch to one option key."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._option = option
        self._default = default

    @property
    def is_on(self) -> bool:
        """Read the option, falling back to the safe default."""
        return bool(
            self.coordinator.config_entry.options.get(self._option, self._default)
        )

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Persist on and apply."""
        await self._set(value=True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Persist off and apply."""
        await self._set(value=False)

    async def _set(self, *, value: bool) -> None:
        entry = self.coordinator.config_entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, self._option: value}
        )
        self.async_write_ha_state()
        await self.coordinator.async_refresh()
