"""
Tier 3: the fake ha-foxess-openapi used in the component tests must keep the
shape of what the real integration produced at a real installation.

The recordings in tests/fixtures/foxess_openapi were taken with
scripts/record_contract.py (anonymised serial and station). If
ha-foxess-openapi changes its service response or entity layout, re-record
and these tests show what the fake has to follow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.emhass_foxess_bridge.backend import (
    KEY_API_REMAINING,
    KEY_MIN_SOC_ON_GRID,
    KEY_SCHEDULE,
    KEY_SCHEDULER_SWITCH,
)
from custom_components.emhass_foxess_bridge.schedule import SchedulerGroup
from tests.component.fake_foxess_openapi import ENTITIES, NIGHT_GROUP

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.contract


def test_get_schedule_response_has_flag_and_v1_groups(load_fixture: Callable) -> None:
    response = load_fixture("foxess_openapi/get_schedule.json")["service_response"]
    assert {"enable", "groups"} <= set(response)
    assert response["groups"], "the H3 reports its eight group slots"
    recorded_keys = set(response["groups"][0])
    assert recorded_keys == set(NIGHT_GROUP), "fake group shape drifted from reality"
    for raw in response["groups"]:
        SchedulerGroup.from_api(raw)  # every recorded group parses


def test_recorded_entities_cover_what_the_backend_reads(load_fixture: Callable) -> None:
    states = {s["entity_id"]: s for s in load_fixture("foxess_openapi/states.json")}
    for _platform, key, object_id in ENTITIES:
        matches = [
            eid
            for eid in states
            if eid.endswith(object_id.split("foxess_test_plant_", 1)[1])
        ]
        assert matches, f"no recorded entity for {key} ({object_id})"
    assert {
        KEY_MIN_SOC_ON_GRID,
        KEY_SCHEDULE,
        KEY_API_REMAINING,
        KEY_SCHEDULER_SWITCH,
    } <= {key for _platform, key, _object_id in ENTITIES}


def test_recorded_schedule_sensor_carries_master_switch_and_groups(
    load_fixture: Callable,
) -> None:
    states = {s["entity_id"]: s for s in load_fixture("foxess_openapi/states.json")}
    schedule = states["sensor.foxess_test_plant_schedule"]
    assert set(schedule["attributes"]) >= {"master_switch", "groups"}
    assert set(schedule["attributes"]["groups"][0]) == set(NIGHT_GROUP)
    assert schedule["state"].endswith("active group(s)")


def test_recorded_power_sensors_are_in_kilowatts(load_fixture: Callable) -> None:
    """Documents why examples/emhass ships W template sensors."""
    states = {s["entity_id"]: s for s in load_fixture("foxess_openapi/states.json")}
    for suffix in ("pv_power", "load_power", "battery_charge_power"):
        unit = states[f"sensor.foxess_test_plant_{suffix}"]["attributes"][
            "unit_of_measurement"
        ]
        assert unit == "kW"
