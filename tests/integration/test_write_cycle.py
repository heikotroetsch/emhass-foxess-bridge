"""
Tier 4: one deliberate write cycle against the real inverter.

Snapshot the scheduler, write a single ForceCharge probe group that lies in
the past of the current day, read back until the cloud reflects it, restore
the snapshot and verify the restore. Needs FOX_ALLOW_WRITES=1 and skips
before 02:00 local time so the probe can never be active.

Answers from the plan's open questions that this cycle can settle:
  1. end minute convention: the probe is written with endMinute 14; the
     read-back shows whether FoxESS keeps it (inclusive) or rewrites it.
  3. whether /scheduler/enable also switches the master flag on.
Questions 2 and 4 (behaviour outside groups, daily re-trigger) need the
field test.
"""

from __future__ import annotations

import asyncio
import json
import time as time_module
from datetime import datetime, time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.emhass_foxess_bridge.api import FoxApiError
from custom_components.emhass_foxess_bridge.schedule import (
    MIN_SOC,
    SchedulerGroup,
    WorkMode,
    groups_equal,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from custom_components.emhass_foxess_bridge.api import FoxOpenApiClient

pytestmark = [pytest.mark.contract, pytest.mark.writes]

SETTLE_ATTEMPTS = 12
SETTLE_SLEEP_S = 10
EARLIEST_HOUR = 2


async def read_until(
    client: FoxOpenApiClient,
    device_sn: str,
    done: Callable[[dict[str, Any], list[dict[str, Any]]], bool],
) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    """Poll flag and groups until ``done`` or the settle budget is spent."""
    started = time_module.monotonic()
    flag: dict[str, Any] = {}
    groups: list[dict[str, Any]] = []
    for attempt in range(SETTLE_ATTEMPTS):
        flag = await client.async_get_scheduler_flag(device_sn)
        groups = await client.async_get_scheduler(device_sn)
        if done(flag, groups):
            break
        if attempt < SETTLE_ATTEMPTS - 1:
            await asyncio.sleep(SETTLE_SLEEP_S)
    return flag, groups, time_module.monotonic() - started


def parsed(raw_groups: list[dict[str, Any]]) -> list[SchedulerGroup]:
    return [SchedulerGroup.from_api(raw) for raw in raw_groups]


async def test_probe_group_round_trips_and_the_snapshot_is_restored(
    real_client: FoxOpenApiClient,
    device_sn: str,
    inverter_tz: str,
    allow_writes: None,
) -> None:
    now = datetime.now(ZoneInfo(inverter_tz))
    if now.hour < EARLIEST_HOUR:
        pytest.skip("probe 00:00-00:14 could still be active before 02:00 local")

    soc = await real_client.async_get_battery_soc(device_sn)
    min_soc = int(soc.get("minSocOnGrid", MIN_SOC))
    flag_before = await real_client.async_get_scheduler_flag(device_sn)
    groups_before = await real_client.async_get_scheduler(device_sn)
    probe = SchedulerGroup(
        time(0, 0), time(0, 14), WorkMode.FORCE_CHARGE, min_soc_on_grid=min_soc
    )
    findings: dict[str, Any] = {
        "before": {"enable": flag_before.get("enable"), "groups": groups_before}
    }

    try:
        await real_client.async_set_scheduler_groups(device_sn, [probe.to_api()])
        flag, groups, seconds = await read_until(
            real_client,
            device_sn,
            lambda _flag, raw: groups_equal(parsed(raw), [probe]),
        )
        findings["probe_read_back"] = groups
        findings["probe_settle_seconds"] = round(seconds, 1)
        findings["flag_after_groups_write"] = flag.get("enable")
        assert groups_equal(parsed(groups), [probe]), "probe never showed up"
        assert groups[0]["endMinute"] == probe.end.minute, "end minute rewritten"
    finally:
        findings["restore"] = await _restore(
            real_client, device_sn, flag_before, groups_before, probe
        )
        print("\nWRITE CYCLE FINDINGS\n" + json.dumps(findings, indent=2))

    assert findings["restore"]["verified"] is True


async def _restore(
    client: FoxOpenApiClient,
    device_sn: str,
    flag_before: dict[str, Any],
    groups_before: list[dict[str, Any]],
    probe: SchedulerGroup,
) -> dict[str, Any]:
    """Put the inverter back; if it had no groups, try an empty list first."""
    outcome: dict[str, Any] = {}
    if groups_before:
        await client.async_set_scheduler_groups(device_sn, groups_before)
        outcome["groups"] = "restored"
    else:
        try:
            await client.async_set_scheduler_groups(device_sn, [])
            outcome["empty_groups_write"] = "accepted"
        except FoxApiError as exc:
            outcome["empty_groups_write"] = f"rejected: {exc}"
            disabled = SchedulerGroup(
                probe.start,
                probe.end,
                probe.work_mode,
                probe.min_soc_on_grid,
                enabled=False,
            )
            await client.async_set_scheduler_groups(device_sn, [disabled.to_api()])
            outcome["groups"] = "probe left in place but disabled"
    enable_before = bool(flag_before.get("enable"))
    await client.async_set_scheduler_enabled(device_sn, enabled=enable_before)

    def restored(flag: dict[str, Any], raw: list[dict[str, Any]]) -> bool:
        groups_ok = groups_equal(parsed(raw), parsed(groups_before))
        return bool(flag.get("enable")) == enable_before and groups_ok

    flag, groups, seconds = await read_until(client, device_sn, restored)
    outcome["after"] = {"enable": flag.get("enable"), "groups": groups}
    outcome["settle_seconds"] = round(seconds, 1)
    outcome["verified"] = restored(flag, groups)
    return outcome
