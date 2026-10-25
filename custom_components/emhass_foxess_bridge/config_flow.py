"""Config flow: pick the inverter from ha-foxess-openapi, name the EMHASS sensors."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .backend import async_resolve_device, async_services_available
from .const import (
    CONF_CHARGE_THRESHOLD_W,
    CONF_DEVICE_ID,
    CONF_EMHASS_ENTITY,
    CONF_EMHASS_GRID_ENTITY,
    CONF_MAX_GROUPS,
    CONF_MIN_WINDOW_MINUTES,
    CONF_PLAN_MAX_AGE_HOURS,
    DEFAULT_EMHASS_ENTITY,
    DEFAULT_EMHASS_GRID_ENTITY,
    DEFAULT_PLAN_MAX_AGE_HOURS,
    DOMAIN,
    FOX_DOMAIN,
)
from .planner import PlannerSettings
from .schedule import MAX_GROUPS


class BridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the UI setup."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004
    ) -> BridgeOptionsFlow:
        """Expose the planner tunables."""
        return BridgeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for the inverter device and the EMHASS plan sensors."""
        if not async_services_available(self.hass):
            return self.async_abort(reason="foxess_openapi_missing")

        errors: dict[str, str] = {}
        if user_input is not None:
            device = async_resolve_device(self.hass, user_input[CONF_DEVICE_ID])
            if device is None:
                errors["base"] = "device_invalid"
            else:
                await self.async_set_unique_id(device.serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"EMHASS bridge {device.name}", data=user_input
                )

        current = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): selector.DeviceSelector(
                        selector.DeviceSelectorConfig(
                            filter=selector.DeviceFilterSelectorConfig(
                                integration=FOX_DOMAIN
                            )
                        )
                    ),
                    vol.Required(
                        CONF_EMHASS_ENTITY,
                        default=current.get(CONF_EMHASS_ENTITY, DEFAULT_EMHASS_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_EMHASS_GRID_ENTITY,
                        default=current.get(
                            CONF_EMHASS_GRID_ENTITY, DEFAULT_EMHASS_GRID_ENTITY
                        ),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
            errors=errors,
        )


def _number(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(
        min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


class BridgeOptionsFlow(config_entries.OptionsFlow):
    """Planner tunables. The switches live in the same options dict and are kept."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show and save the tunables."""
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )

        options = self.config_entry.options
        defaults = PlannerSettings()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CHARGE_THRESHOLD_W,
                        default=options.get(
                            CONF_CHARGE_THRESHOLD_W, defaults.charge_threshold_w
                        ),
                    ): _number(0, 20000, 50, "W"),
                    vol.Required(
                        CONF_MIN_WINDOW_MINUTES,
                        default=options.get(
                            CONF_MIN_WINDOW_MINUTES, defaults.min_window_minutes
                        ),
                    ): _number(15, 240, 15, "min"),
                    vol.Required(
                        CONF_MAX_GROUPS,
                        default=options.get(CONF_MAX_GROUPS, defaults.max_groups),
                    ): _number(1, MAX_GROUPS, 1),
                    vol.Required(
                        CONF_PLAN_MAX_AGE_HOURS,
                        default=options.get(
                            CONF_PLAN_MAX_AGE_HOURS, DEFAULT_PLAN_MAX_AGE_HOURS
                        ),
                    ): _number(1, 48, 1, "h"),
                }
            ),
        )
