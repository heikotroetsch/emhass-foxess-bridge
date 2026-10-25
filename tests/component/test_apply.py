"""Component tests for the apply loop: EMHASS plan in, scheduler groups out."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.emhass_foxess_bridge.api import (
    PATH_SCHEDULER_ENABLE,
    PATH_SCHEDULER_SET,
)
from custom_components.emhass_foxess_bridge.const import (
    CONF_DRY_RUN,
    CONF_ENABLED,
    CONF_MIN_WINDOW_MINUTES,
    DOMAIN,
    ISSUE_RATE_LIMITED,
)

from .fake_fox import (
    LIVE,
    NIGHT_GROUP,
    PLAN_TIME,
    SN,
    FakeFoxCloud,
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


async def test_plan_update_is_turned_into_a_planned_schedule(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    await setup_bridge(hass)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    planned = state(hass, "sensor", "planned_schedule")
    assert planned.state == "ForceCharge 02:00-04:59"
    assert planned.attributes["groups"] == [NIGHT_GROUP]
    assert state(hass, "binary_sensor", "plan_stale").state == "off"
    assert state(hass, "sensor", "apply_result").state == "disabled"
    assert fox.writes(PATH_SCHEDULER_ENABLE) == []
    assert fox.writes(PATH_SCHEDULER_SET) == []


async def test_switch_defaults_are_safe(hass: HomeAssistant, fox: FakeFoxCloud) -> None:
    await setup_bridge(hass)
    assert state(hass, "switch", "bridge_enabled").state == "off"
    assert state(hass, "switch", "dry_run").state == "on"


async def test_nothing_is_written_until_enabled_and_dry_run_is_off(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    await setup_bridge(hass)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    await turn(hass, "bridge_enabled", on=True)
    assert state(hass, "sensor", "apply_result").state == "dry_run"
    assert fox.writes(PATH_SCHEDULER_ENABLE) == []

    await turn(hass, "dry_run", on=False)
    assert fox.writes(PATH_SCHEDULER_ENABLE) == [
        {"deviceSN": SN, "groups": [NIGHT_GROUP]}
    ]
    assert fox.writes(PATH_SCHEDULER_SET) == [{"deviceSN": SN, "enable": 1}]
    assert state(hass, "sensor", "apply_result").state == "written"
    active = state(hass, "sensor", "active_schedule")
    assert active.state == "ForceCharge 02:00-04:59"
    assert active.attributes["scheduler_enabled"] is True
    last_apply = state(hass, "sensor", "last_apply")
    assert last_apply.state == dt_util.utcnow().isoformat()


async def test_switches_persist_in_the_entry_options(
    hass: HomeAssistant, fox: FakeFoxCloud
) -> None:
    entry = await setup_bridge(hass)
    await turn(hass, "bridge_enabled", on=True)
    await turn(hass, "dry_run", on=False)
    assert entry.options[CONF_ENABLED] is True
    assert entry.options[CONF_DRY_RUN] is False
    assert state(hass, "switch", "bridge_enabled").state == "on"
    assert state(hass, "switch", "dry_run").state == "off"


async def test_unchanged_schedule_is_not_rewritten(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.groups = [NIGHT_GROUP]
    fox.flag_enabled = True
    publish_plan(hass, load_fixture)
    await setup_bridge(hass, LIVE)
    await settle(hass, freezer)

    assert state(hass, "sensor", "apply_result").state == "unchanged"
    assert fox.writes(PATH_SCHEDULER_ENABLE) == []
    assert fox.writes(PATH_SCHEDULER_SET) == []


async def test_scheduler_flag_is_left_alone_when_already_on(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.flag_enabled = True
    await setup_bridge(hass, LIVE)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    assert fox.writes(PATH_SCHEDULER_ENABLE) == [
        {"deviceSN": SN, "groups": [NIGHT_GROUP]}
    ]
    assert fox.writes(PATH_SCHEDULER_SET) == []


async def test_quarter_hour_tick_reapplies_the_plan(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    await setup_bridge(hass)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)
    reads = fox.reads()

    await next_quarter(hass, freezer)
    assert fox.reads() == reads + 1


async def test_missing_plan_disables_the_scheduler_once(
    hass: HomeAssistant, fox: FakeFoxCloud, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.groups = [NIGHT_GROUP]
    fox.flag_enabled = True
    await setup_bridge(hass, LIVE)

    stale = state(hass, "binary_sensor", "plan_stale")
    assert stale.state == "on"
    assert "sensor.p_batt_forecast" in stale.attributes["reason"]
    assert state(hass, "sensor", "apply_result").state == "cleared"
    assert fox.writes(PATH_SCHEDULER_SET) == [{"deviceSN": SN, "enable": 0}]
    assert fox.writes(PATH_SCHEDULER_ENABLE) == []
    assert state(hass, "sensor", "active_schedule").state == "none"

    await next_quarter(hass, freezer)
    await next_quarter(hass, freezer)
    assert len(fox.writes(PATH_SCHEDULER_SET)) == 1
    assert state(hass, "sensor", "apply_result").state == "unchanged"


async def test_plan_older_than_the_limit_is_stale(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    await setup_bridge(hass)
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
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.write_errno = 40400
    await setup_bridge(hass, LIVE)
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
    assert len(fox.calls) == calls, "no API calls while the quota is exhausted"

    fox.write_errno = 0
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


async def test_lagging_readback_does_not_trigger_a_second_write(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.apply_writes = False
    await setup_bridge(hass, LIVE)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)
    assert len(fox.writes(PATH_SCHEDULER_ENABLE)) == 1

    publish_plan(hass, load_fixture)
    await settle(hass, freezer)  # about 30 s after the write: cloud still lags
    assert len(fox.writes(PATH_SCHEDULER_ENABLE)) == 1
    assert state(hass, "sensor", "apply_result").state == "pending"
    assert state(hass, "sensor", "active_schedule").state == "ForceCharge 02:00-04:59"

    publish_plan(hass, load_fixture)
    await settle(hass, freezer, seconds=61)  # past the settle window: rewrite
    assert len(fox.writes(PATH_SCHEDULER_ENABLE)) == 2
    assert state(hass, "sensor", "apply_result").state == "written"


async def test_api_errors_are_reported_not_raised(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    freezer.move_to(PLAN_TIME)
    fox.read_errno = 41200
    entry = await setup_bridge(hass, LIVE)
    publish_plan(hass, load_fixture)
    await settle(hass, freezer)

    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, "sensor", "apply_result").state == "error"
    assert "41200" in state(hass, "sensor", "last_apply").attributes["detail"]
    assert state(hass, "sensor", "planned_schedule").state == "ForceCharge 02:00-04:59"


async def test_planner_options_change_the_plan(
    hass: HomeAssistant,
    fox: FakeFoxCloud,
    freezer: FrozenDateTimeFactory,
    load_fixture: Callable,
) -> None:
    """A 15-minute minimum window lets the single cheap 13:00 slot through."""
    freezer.move_to(PLAN_TIME)
    publish_plan(hass, load_fixture)
    await setup_bridge(hass, {CONF_MIN_WINDOW_MINUTES: 15})
    await settle(hass, freezer)
    assert state(hass, "sensor", "planned_schedule").state == (
        "ForceCharge 02:00-04:59, ForceCharge 13:00-13:14"
    )
