"""A stateful fake of the FoxESS cloud plus helpers shared by the component tests."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.emhass_foxess_bridge.api import (
    BASE_URL,
    PATH_BATTERY_SOC,
    PATH_DEVICE_DETAIL,
    PATH_REAL_QUERY,
    PATH_SCHEDULER_ENABLE,
    PATH_SCHEDULER_FLAG,
    PATH_SCHEDULER_GET,
    PATH_SCHEDULER_SET,
)
from custom_components.emhass_foxess_bridge.const import (
    CONF_API_KEY,
    CONF_DEVICE_SN,
    CONF_DRY_RUN,
    CONF_EMHASS_ENTITY,
    CONF_EMHASS_GRID_ENTITY,
    CONF_ENABLED,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant, State
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

SN = "60SH5TEST000001"
API_KEY = "secret-api-key-0123456789"
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
}
LIVE = {CONF_ENABLED: True, CONF_DRY_RUN: False}


def ok(result: Any = None) -> dict[str, Any]:
    """Successful Open API envelope."""
    return {"errno": 0, "msg": "Operation successful", "result": result}


class FakeFoxCloud:
    """Stateful stand-in for the scheduler endpoints of the FoxESS cloud."""

    def __init__(
        self, aioclient_mock: AiohttpClientMocker, load_fixture: Callable
    ) -> None:
        self.groups: list[dict[str, Any]] = []
        self.flag_enabled = False
        self.apply_writes = True  # False: accept writes but keep the old state
        self.write_errno = 0  # e.g. 40400 to simulate an exhausted quota
        self.read_errno = 0
        self.calls: list[tuple[str, Any]] = []
        mock = aioclient_mock
        mock.get(
            f"{BASE_URL}{PATH_DEVICE_DETAIL}?sn={SN}",
            json=load_fixture("device_detail.json"),
        )
        mock.get(
            f"{BASE_URL}{PATH_BATTERY_SOC}?sn={SN}", json=load_fixture("soc_get.json")
        )
        mock.post(f"{BASE_URL}{PATH_REAL_QUERY}", json=load_fixture("real_query.json"))
        mock.post(f"{BASE_URL}{PATH_SCHEDULER_FLAG}", side_effect=self._flag)
        mock.post(f"{BASE_URL}{PATH_SCHEDULER_GET}", side_effect=self._get)
        mock.post(f"{BASE_URL}{PATH_SCHEDULER_ENABLE}", side_effect=self._enable)
        mock.post(f"{BASE_URL}{PATH_SCHEDULER_SET}", side_effect=self._set)

    def writes(self, path: str) -> list[Any]:
        """Payloads sent to one write endpoint, in order."""
        return [data for called, data in self.calls if called == path]

    def reads(self) -> int:
        """How often the scheduler groups were read."""
        return len([1 for called, _ in self.calls if called == PATH_SCHEDULER_GET])

    @staticmethod
    def _respond(method: str, url: Any, body: dict) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, json=body)

    async def _flag(
        self, method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        self.calls.append((PATH_SCHEDULER_FLAG, data))
        if self.read_errno:
            return self._respond(
                method, url, {"errno": self.read_errno, "result": None}
            )
        return self._respond(
            method, url, ok({"enable": self.flag_enabled, "support": True})
        )

    async def _get(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        self.calls.append((PATH_SCHEDULER_GET, data))
        if self.read_errno:
            return self._respond(
                method, url, {"errno": self.read_errno, "result": None}
            )
        result = (
            {"enable": self.flag_enabled, "groups": self.groups}
            if self.groups
            else None
        )
        return self._respond(method, url, ok(result))

    async def _enable(
        self, method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        self.calls.append((PATH_SCHEDULER_ENABLE, data))
        if self.write_errno:
            return self._respond(
                method, url, {"errno": self.write_errno, "result": None}
            )
        if self.apply_writes:
            self.groups = list(data["groups"])
        return self._respond(method, url, ok())

    async def _set(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        self.calls.append((PATH_SCHEDULER_SET, data))
        if self.write_errno:
            return self._respond(
                method, url, {"errno": self.write_errno, "result": None}
            )
        if self.apply_writes:
            self.flag_enabled = bool(data["enable"])
        return self._respond(method, url, ok())


async def setup_bridge(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Create and load a config entry for the test inverter in Europe/Berlin."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SN,
        data={
            CONF_API_KEY: API_KEY,
            CONF_DEVICE_SN: SN,
            CONF_EMHASS_ENTITY: "sensor.p_batt_forecast",
            CONF_EMHASS_GRID_ENTITY: "sensor.p_grid_forecast",
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
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
