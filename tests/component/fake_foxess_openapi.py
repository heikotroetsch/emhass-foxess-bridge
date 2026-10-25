"""
A fake of the ha-foxess-openapi integration, plus helpers shared by the tests.

It registers the three services the bridge calls (`get_schedule`,
`set_schedule`, `disable_scheduler`) with the field names of the real
integration, creates the inverter device with its `(foxess_openapi, sn)`
identifier and the entities the bridge reads (min SoC on grid, schedule,
API calls remaining, scheduler switch), and keeps state like the cloud would.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.emhass_foxess_bridge.const import (
    CONF_DEVICE_ID,
    CONF_DRY_RUN,
    CONF_EMHASS_ENTITY,
    CONF_EMHASS_GRID_ENTITY,
    CONF_ENABLED,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant, ServiceCall, State

FOX_DOMAIN = "foxess_openapi"
SN = "60SH5TEST000001"
DEVICE_NAME = "FoxESS Test Plant"
# 17:10 Europe/Berlin on the fixture's first day.
PLAN_TIME = "2026-01-15T16:10:00+00:00"
NIGHT_GROUP = {
    "enable": 1,
    "startHour": 2,
    "startMinute": 0,
    "endHour": 4,
    "endMinute": 59,
    "workMode": "ForceCharge",
    "minSocOnGrid": 10,
    "fdSoc": 10,
    "fdPwr": 0,
    "maxSoc": 100,
}
LIVE = {CONF_ENABLED: True, CONF_DRY_RUN: False}
RATE_LIMIT_MESSAGE = "FoxESS API error 40400: Request limit exceeded"
REQUIRED_GROUP_KEYS = (
    "startHour",
    "startMinute",
    "endHour",
    "endMinute",
    "workMode",
    "minSocOnGrid",
    "fdSoc",
    "fdPwr",
)
# (platform, unique id suffix, suggested object id) as the real integration creates them
ENTITIES = (
    ("number", "minSocOnGrid", "foxess_test_plant_battery_min_soc_on_grid"),
    ("sensor", "schedule", "foxess_test_plant_schedule"),
    ("sensor", "api_remaining", "foxess_test_plant_api_calls_remaining"),
    ("switch", "scheduler_enable", "foxess_test_plant_scheduler_mode_scheduler"),
)


class FakeFoxessOpenApi:
    """Stateful stand-in for ha-foxess-openapi inside the test Home Assistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.groups: list[dict[str, Any]] = []
        self.flag_enabled = False
        self.min_soc_on_grid = 10
        self.api_remaining = 1200
        self.apply_writes = True  # False: accept writes but keep the old state
        self.fail_reads_with: str | None = None
        self.fail_writes_with: str | None = None
        self.response_has_enable = True  # the real V1 response carries "enable"
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.device_id = ""
        self.entity_ids: dict[str, str] = {}

    async def async_setup(self, *, register_services: bool = True) -> None:
        """Create entry, device and entities; register the services."""
        entry = MockConfigEntry(
            domain=FOX_DOMAIN,
            title=f"FoxESS {SN}",
            unique_id=SN,
            data={"api_key": "fox-key", "device_sn": SN},
        )
        entry.add_to_hass(self.hass)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(FOX_DOMAIN, SN)},
            manufacturer="FoxESS",
            model="H3-5.0-E",
            name=DEVICE_NAME,
            serial_number=SN,
        )
        self.device_id = device.id
        registry = er.async_get(self.hass)
        for platform, key, object_id in ENTITIES:
            entity = registry.async_get_or_create(
                platform,
                FOX_DOMAIN,
                f"{SN}_{key}",
                config_entry=entry,
                device_id=device.id,
                suggested_object_id=object_id,
            )
            self.entity_ids[key] = entity.entity_id
        self.publish()
        if register_services:
            self._register_services()

    def _register_services(self) -> None:
        self.hass.services.async_register(
            FOX_DOMAIN,
            "get_schedule",
            self._get_schedule,
            schema=vol.Schema({vol.Optional("device_sn"): cv.string}),
            supports_response=SupportsResponse.ONLY,
        )
        self.hass.services.async_register(
            FOX_DOMAIN,
            "set_schedule",
            self._set_schedule,
            schema=vol.Schema(
                {
                    vol.Optional("device_sn"): cv.string,
                    vol.Required("groups"): vol.All(cv.ensure_list, [dict]),
                    vol.Optional("enable_scheduler", default=True): cv.boolean,
                }
            ),
        )
        self.hass.services.async_register(
            FOX_DOMAIN,
            "disable_scheduler",
            self._disable_scheduler,
            schema=vol.Schema({vol.Optional("device_sn"): cv.string}),
        )

    def publish(self) -> None:
        """Write the entity states the way the real coordinator would."""
        active = len([g for g in self.groups if g.get("enable", 1) == 1])
        self.hass.states.async_set(
            self.entity_ids["minSocOnGrid"],
            str(self.min_soc_on_grid),
            {"unit_of_measurement": "%"},
        )
        self.hass.states.async_set(
            self.entity_ids["schedule"],
            f"{active} active group(s)",
            {
                "master_switch": self.flag_enabled,
                "groups": [dict(g) for g in self.groups],
            },
        )
        self.hass.states.async_set(
            self.entity_ids["api_remaining"],
            str(self.api_remaining),
            {"total": "1440"},
        )
        self.hass.states.async_set(
            self.entity_ids["scheduler_enable"], "on" if self.flag_enabled else "off"
        )

    def service_calls(self, service: str) -> list[dict[str, Any]]:
        """Data of every call to one service, in order."""
        return [data for name, data in self.calls if name == service]

    def _check(self, call: ServiceCall, failure: str | None) -> None:
        sn = call.data.get("device_sn")
        if sn not in (None, "", SN):
            msg = f"Unknown inverter SN {sn}"
            raise HomeAssistantError(msg)
        if failure:
            raise HomeAssistantError(failure)

    async def _get_schedule(self, call: ServiceCall) -> ServiceResponse:
        self.calls.append(("get_schedule", dict(call.data)))
        self._check(call, self.fail_reads_with)
        self.api_remaining -= 1
        response: dict[str, Any] = {
            "groups": [dict(g) for g in self.groups],
            "maxGroupCount": 8,
        }
        if self.response_has_enable:
            response["enable"] = int(self.flag_enabled)
        return response

    async def _set_schedule(self, call: ServiceCall) -> None:
        groups = [dict(g) for g in call.data["groups"]]
        self.calls.append(("set_schedule", {**dict(call.data), "groups": groups}))
        self._check(call, self.fail_writes_with)
        for group in groups:
            missing = [key for key in REQUIRED_GROUP_KEYS if key not in group]
            if missing:
                msg = f"group is missing {missing}"
                raise HomeAssistantError(msg)
        self.api_remaining -= 2
        if self.apply_writes:
            self.groups = groups
            if call.data["enable_scheduler"]:
                self.flag_enabled = True
        self.publish()

    async def _disable_scheduler(self, call: ServiceCall) -> None:
        self.calls.append(("disable_scheduler", dict(call.data)))
        self._check(call, self.fail_writes_with)
        self.api_remaining -= 1
        if self.apply_writes:
            self.flag_enabled = False
        self.publish()


# --- helpers ---------------------------------------------------------------


async def setup_bridge(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Create and load a bridge entry bound to the fake inverter, in Europe/Berlin."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=SN,
        title=f"EMHASS bridge {DEVICE_NAME}",
        data={
            CONF_DEVICE_ID: fox.device_id,
            CONF_EMHASS_ENTITY: "sensor.p_batt_forecast",
            CONF_EMHASS_GRID_ENTITY: "sensor.p_grid_forecast",
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def publish_plan(hass: HomeAssistant, load_fixture: Callable) -> None:
    """Set the two EMHASS sensors exactly as the add-on would."""
    fixture = load_fixture("emhass_plan_winter_day.json")
    for entity_id, payload in fixture.items():
        # force_update: a real EMHASS publish always shifts the list, so it
        # always fires a state_changed event; mirror that for repeated publishes.
        hass.states.async_set(
            entity_id, payload["state"], payload["attributes"], force_update=True
        )


async def settle(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float = 31
) -> None:
    """Let the plan-change debounce elapse and run the apply loop."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def next_quarter(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move to the next quarter-hour tick of the apply timer."""
    now = dt_util.utcnow()
    target = now.replace(second=0, microsecond=0) + timedelta(minutes=15)
    target -= timedelta(minutes=target.minute % 15)
    freezer.move_to(target + timedelta(seconds=25))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def eid(hass: HomeAssistant, platform: str, key: str) -> str:
    """Entity id of one of the bridge's entities, by unique id suffix."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SN}_{key}")
    assert entity_id is not None, f"no {platform} entity for {key}"
    return entity_id


def state(hass: HomeAssistant, platform: str, key: str) -> State:
    """Current state object of one of the bridge's entities."""
    found = hass.states.get(eid(hass, platform, key))
    assert found is not None
    return found


async def turn(hass: HomeAssistant, key: str, *, on: bool) -> None:
    """Toggle one of the bridge's switches through the service API."""
    await hass.services.async_call(
        "switch",
        "turn_on" if on else "turn_off",
        {"entity_id": eid(hass, "switch", key)},
        blocking=True,
    )
    await hass.async_block_till_done()
