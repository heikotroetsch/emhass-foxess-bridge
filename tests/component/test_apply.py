"""Component tests for the apply loop: EMHASS plan in, scheduler services out."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.emhass_foxess_bridge.const import (
    CONF_DRY_RUN,
    CONF_ENABLED,
    CONF_MIN_WINDOW_MINUTES,
    DOMAIN,
    ISSUE_RATE_LIMITED,
)

from .fake_foxess_openapi import (
    LIVE,
    NIGHT_GROUP,
    PLAN_TIME,
    RATE_LIMIT_MESSAGE,
    SN,
    FakeFoxessOpenApi,
    next_quarter,
    publish_plan,
    settle,
    setup_bridge,
    state,
    turn,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

WRITE = {"device_sn": SN, "groups": [NIGHT_GROUP], "enable_scheduler": True}


async def test_plan_update_is_turned_into_a_planned_schedule(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    await setup_bridge(hass, fox)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    planned = state(hass, "sensor", "planned_schedule")
    assert planned.state == "ForceCharge 02:00-04:59"
    assert planned.attributes["groups"] == [NIGHT_GROUP]
    assert state(hass, "binary_sensor", "plan_stale").state == "off"
    assert state(hass, "sensor", "apply_result").state == "disabled"
    assert fox.service_calls("set_schedule") == []
    assert fox.service_calls("disable_scheduler") == []


async def test_switch_defaults_are_safe(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    await setup_bridge(hass, fox)
    assert state(hass, "switch", "bridge_enabled").state == "off"
    assert state(hass, "switch", "dry_run").state == "on"


async def test_nothing_is_written_until_enabled_and_dry_run_is_off(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    await setup_bridge(hass, fox)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    await turn(hass, "bridge_enabled", on=True)
    assert state(hass, "sensor", "apply_result").state == "dry_run"
    assert fox.service_calls("set_schedule") == []

    await turn(hass, "dry_run", on=False)
    assert fox.service_calls("set_schedule") == [WRITE]
    assert state(hass, "sensor", "apply_result").state == "written"
    active = state(hass, "sensor", "active_schedule")
    assert active.state == "ForceCharge 02:00-04:59"
    assert active.attributes["scheduler_enabled"] is True
    assert state(hass, "sensor", "last_apply").state == dt_util.utcnow().isoformat()


async def test_switches_persist_in_the_entry_options(
    hass: HomeAssistant, fox: FakeFoxessOpenApi
) -> None:
    entry = await setup_bridge(hass, fox)
    await turn(hass, "bridge_enabled", on=True)
    await turn(hass, "dry_run", on=False)
    assert entry.options[CONF_ENABLED] is True
    assert entry.options[CONF_DRY_RUN] is False
    assert state(hass, "switch", "bridge_enabled").state == "on"
    assert state(hass, "switch", "dry_run").state == "off"


async def test_unchanged_schedule_is_not_rewritten(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.groups = [NIGHT_GROUP]
    fox.flag_enabled = True
    fox.publish()
    publish_plan(hass, load_fixture)
    await setup_bridge(hass, fox, LIVE)
    await settle(hass, freezer)

    assert state(hass, "sensor", "apply_result").state == "unchanged"
    assert fox.service_calls("set_schedule") == []
    assert fox.service_calls("disable_scheduler") == []


async def test_inverter_min_soc_on_grid_is_used_for_the_groups(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.min_soc_on_grid = 20
    fox.publish()
    await setup_bridge(hass, fox)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)
    group = state(hass, "sensor", "planned_schedule").attributes["groups"][0]
    assert group["minSocOnGrid"] == 20
    assert group["fdSoc"] == 20


async def test_quarter_hour_tick_reapplies_the_plan(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    await setup_bridge(hass, fox)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)
    reads = len(fox.service_calls("get_schedule"))

    await next_quarter(hass, freezer)
    assert len(fox.service_calls("get_schedule")) == reads + 1


async def test_missing_plan_disables_the_scheduler_once(
    hass: HomeAssistant, fox: FakeFoxessOpenApi, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.groups = [NIGHT_GROUP]
    fox.flag_enabled = True
    fox.publish()
    await setup_bridge(hass, fox, LIVE)

    stale = state(hass, "binary_sensor", "plan_stale")
    assert stale.state == "on"
    assert "sensor.p_batt_forecast" in stale.attributes["reason"]
    assert state(hass, "sensor", "apply_result").state == "cleared"
    assert fox.service_calls("disable_scheduler") == [{"device_sn": SN}]
    assert fox.service_calls("set_schedule") == []
    assert state(hass, "sensor", "active_schedule").state == "none"

    await next_quarter(hass, freezer)
    await next_quarter(hass, freezer)
    assert len(fox.service_calls("disable_scheduler")) == 1
    assert state(hass, "sensor", "apply_result").state == "unchanged"


async def test_plan_older_than_the_limit_is_stale(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    await setup_bridge(hass, fox)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)
    assert state(hass, "binary_sensor", "plan_stale").state == "off"

    freezer.tick(timedelta(hours=3, minutes=1))
    await next_quarter(hass, freezer)
    stale = state(hass, "binary_sensor", "plan_stale")
    assert stale.state == "on"
    assert "older" in stale.attributes["reason"]
    assert state(hass, "sensor", "planned_schedule").state == "none"


async def test_rate_limit_blocks_writes_until_midnight_and_opens_an_issue(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.fail_writes_with = RATE_LIMIT_MESSAGE
    await setup_bridge(hass, fox, LIVE)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    assert state(hass, "sensor", "apply_result").state == "rate_limited"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RATE_LIMITED) is not None
    blocked_until = state(hass, "sensor", "apply_result").attributes[
        "writes_blocked_until"
    ]
    assert blocked_until == "2026-01-15T23:00:00+00:00"  # local midnight

    calls = len(fox.calls)
    await next_quarter(hass, freezer)
    assert len(fox.calls) == calls, "no service calls while the quota is exhausted"

    fox.fail_writes_with = None
    freezer.move_to("2026-01-15T23:00:30+00:00")
    publish_plan(hass, load_fixture)  # EMHASS kept publishing meanwhile
    await settle(hass, freezer)
    # The jump past midnight also fires the pending quarter-hour tick, so one
    # or two runs happen here; what matters is the effect on the inverter.
    assert fox.groups == [NIGHT_GROUP]
    assert fox.flag_enabled is True
    assert state(hass, "sensor", "apply_result").state in ("written", "unchanged")
    assert (
        state(hass, "sensor", "apply_result").attributes["writes_blocked_until"] is None
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RATE_LIMITED) is None


async def test_low_remaining_quota_pauses_writes_before_the_limit_hits(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.api_remaining = 30
    fox.publish()
    await setup_bridge(hass, fox, LIVE)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    assert state(hass, "sensor", "apply_result").state == "rate_limited"
    assert "remaining" in state(hass, "sensor", "last_apply").attributes["detail"]
    assert fox.service_calls("set_schedule") == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RATE_LIMITED) is not None


async def test_lagging_readback_does_not_trigger_a_second_write(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.apply_writes = False
    await setup_bridge(hass, fox, LIVE)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)
    assert len(fox.service_calls("set_schedule")) == 1

    publish_plan(hass, load_fixture)
    await settle(hass, freezer)  # about 30 s after the write: cloud still lags
    assert len(fox.service_calls("set_schedule")) == 1
    assert state(hass, "sensor", "apply_result").state == "pending"
    assert state(hass, "sensor", "active_schedule").state == "ForceCharge 02:00-04:59"

    publish_plan(hass, load_fixture)
    await settle(hass, freezer, seconds=61)  # past the settle window: rewrite
    assert len(fox.service_calls("set_schedule")) == 2
    assert state(hass, "sensor", "apply_result").state == "written"


async def test_backend_errors_are_reported_not_raised(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.fail_reads_with = "FoxESS API error 41200: bad request"
    entry = await setup_bridge(hass, fox, LIVE)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor", "apply_result").state == "error"
    assert "41200" in state(hass, "sensor", "last_apply").attributes["detail"]
    assert state(hass, "sensor", "planned_schedule").state == "ForceCharge 02:00-04:59"


async def test_planner_options_change_the_plan(
    hass: HomeAssistant,
    fox: FakeFoxessOpenApi,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    """A 15-minute minimum window lets the single cheap 13:00 slot through."""
    freezer.move_to(PLAN_TIME)
    publish_plan(hass, load_fixture)
    await setup_bridge(hass, fox, {CONF_MIN_WINDOW_MINUTES: 15})
    await settle(hass, freezer)
    assert state(hass, "sensor", "planned_schedule").state == (
        "ForceCharge 02:00-04:59, ForceCharge 13:00-13:14"
    )
