"""Config flow: API key, inverter serial and the EMHASS plan entity."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    FoxApiAuthError,
    FoxApiCommunicationError,
    FoxApiDeviceNotFoundError,
    FoxApiError,
    FoxApiRateLimitError,
    FoxOpenApiClient,
)
from .const import (
    CONF_API_KEY,
    CONF_DEVICE_SN,
    CONF_EMHASS_ENTITY,
    DEFAULT_EMHASS_ENTITY,
    DOMAIN,
    LOGGER,
)


class NoSchedulerSupportError(Exception):
    """The inverter does not report mode scheduler support."""


class BridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the UI setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for credentials, validate them against the Open API."""
        errors: dict[str, str] = {}
        if user_input is not None:
            device_sn = user_input[CONF_DEVICE_SN].strip()
            try:
                detail = await self._fetch_detail(user_input[CONF_API_KEY], device_sn)
            except FoxApiAuthError:
                errors["base"] = "invalid_auth"
            except FoxApiDeviceNotFoundError:
                errors["base"] = "device_not_found"
            except FoxApiRateLimitError:
                errors["base"] = "rate_limited"
            except FoxApiCommunicationError:
                errors["base"] = "cannot_connect"
            except FoxApiError as exc:
                LOGGER.warning("FoxESS API rejected the request: %s", exc)
                errors["base"] = "invalid_response"
            except NoSchedulerSupportError:
                errors["base"] = "no_scheduler"
            else:
                await self.async_set_unique_id(device_sn)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"FoxESS {detail.get('deviceType', '')} {device_sn}".strip(),
                    data={**user_input, CONF_DEVICE_SN: device_sn},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Required(
                        CONF_DEVICE_SN,
                        default=(user_input or {}).get(CONF_DEVICE_SN, vol.UNDEFINED),
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_EMHASS_ENTITY,
                        default=(user_input or {}).get(
                            CONF_EMHASS_ENTITY, DEFAULT_EMHASS_ENTITY
                        ),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
            errors=errors,
        )

    async def _fetch_detail(self, api_key: str, device_sn: str) -> dict[str, Any]:
        client = FoxOpenApiClient(api_key, async_get_clientsession(self.hass))
        detail = await client.async_get_device_detail(device_sn)
        if not (detail.get("function") or {}).get("scheduler"):
            raise NoSchedulerSupportError
        return detail
