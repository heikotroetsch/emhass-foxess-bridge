"""Unit tests for the FoxESS mode-scheduler group model."""

from __future__ import annotations

from datetime import time

import pytest

from custom_components.emhass_foxess_bridge.schedule import (
    MAX_GROUPS,
    ScheduleError,
    SchedulerGroup,
    WorkMode,
    groups_equal,
    validate_groups,
)

# One group exactly as /op/v0/device/scheduler/get returns it.
API_GROUP = {
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


def charge(start: time, end: time) -> SchedulerGroup:
    return SchedulerGroup(start=start, end=end, work_mode=WorkMode.FORCE_CHARGE)


def test_group_round_trips_through_the_api_shape() -> None:
    group = SchedulerGroup.from_api(API_GROUP)
    assert group == charge(time(2, 0), time(4, 59))
    assert group.to_api() == API_GROUP


def test_from_api_ignores_fields_this_version_does_not_model() -> None:
    group = SchedulerGroup.from_api({**API_GROUP, "maxSoc": 100})
    assert "maxSoc" not in group.to_api()


def test_disabled_group_is_sent_with_enable_zero() -> None:
    group = SchedulerGroup.from_api({**API_GROUP, "enable": 0})
    assert group.enabled is False
    assert group.to_api()["enable"] == 0


def test_group_describes_itself_for_logs_and_attributes() -> None:
    assert str(charge(time(2, 0), time(4, 59))) == "ForceCharge 02:00-04:59"


def test_work_modes_use_the_api_spelling() -> None:
    assert [mode.value for mode in WorkMode] == [
        "SelfUse",
        "Feedin",
        "Backup",
        "ForceCharge",
        "ForceDischarge",
    ]


def test_adjacent_groups_are_valid() -> None:
    validate_groups(
        [charge(time(2, 0), time(4, 59)), SchedulerGroup(time(5, 0), time(23, 59))]
    )


def test_groups_sharing_a_minute_overlap() -> None:
    with pytest.raises(ScheduleError, match="overlap"):
        validate_groups(
            [charge(time(2, 0), time(5, 0)), SchedulerGroup(time(5, 0), time(6, 0))]
        )


def test_overlap_is_detected_regardless_of_order() -> None:
    with pytest.raises(ScheduleError, match="overlap"):
        validate_groups(
            [SchedulerGroup(time(5, 0), time(6, 0)), charge(time(2, 0), time(5, 30))]
        )


def test_disabled_groups_do_not_count_for_overlap() -> None:
    off = SchedulerGroup(time(0, 0), time(23, 59), enabled=False)
    validate_groups([off, charge(time(2, 0), time(4, 59))])


def test_group_must_end_after_it_starts() -> None:
    with pytest.raises(ScheduleError, match="ends before"):
        validate_groups([charge(time(5, 0), time(4, 59))])


def test_one_minute_group_is_valid() -> None:
    validate_groups([charge(time(5, 0), time(5, 0))])


def test_more_than_eight_groups_are_rejected() -> None:
    groups = [charge(time(h, 0), time(h, 59)) for h in range(MAX_GROUPS + 1)]
    with pytest.raises(ScheduleError, match="at most 8"):
        validate_groups(groups)


@pytest.mark.parametrize("soc", [9, 101])
def test_min_soc_on_grid_must_be_between_10_and_100(soc: int) -> None:
    group = SchedulerGroup(
        time(2, 0), time(4, 59), WorkMode.FORCE_CHARGE, min_soc_on_grid=soc
    )
    with pytest.raises(ScheduleError, match="minSocOnGrid"):
        validate_groups([group])


def test_times_must_have_minute_resolution() -> None:
    with pytest.raises(ScheduleError, match="whole minutes"):
        validate_groups([charge(time(2, 0, 30), time(4, 59))])


def test_equal_groups_in_different_order_compare_equal() -> None:
    groups = [charge(time(2, 0), time(4, 59)), charge(time(13, 0), time(13, 59))]
    assert groups_equal(groups, list(reversed(groups)))


def test_discharge_fields_are_ignored_outside_force_discharge() -> None:
    """FoxESS may rewrite fdSoc/fdPwr; they carry no meaning for ForceCharge."""
    desired = charge(time(2, 0), time(4, 59))
    current = SchedulerGroup.from_api({**API_GROUP, "fdSoc": 100, "fdPwr": 5000})
    assert groups_equal([desired], [current])


def test_discharge_fields_matter_for_force_discharge() -> None:
    a = SchedulerGroup(
        time(17, 0), time(18, 59), WorkMode.FORCE_DISCHARGE, fd_soc=20, fd_pwr=3000
    )
    b = SchedulerGroup(
        time(17, 0), time(18, 59), WorkMode.FORCE_DISCHARGE, fd_soc=20, fd_pwr=5000
    )
    assert not groups_equal([a], [b])


def test_disabled_groups_are_ignored_when_comparing() -> None:
    off = SchedulerGroup(time(0, 0), time(23, 59), enabled=False)
    active = charge(time(2, 0), time(4, 59))
    assert groups_equal([active], [off, active])


def test_different_window_is_a_change() -> None:
    assert not groups_equal(
        [charge(time(2, 0), time(4, 59))], [charge(time(2, 0), time(5, 59))]
    )


def test_different_min_soc_is_a_change() -> None:
    a = charge(time(2, 0), time(4, 59))
    b = SchedulerGroup(
        time(2, 0), time(4, 59), WorkMode.FORCE_CHARGE, min_soc_on_grid=20
    )
    assert not groups_equal([a], [b])


def test_empty_schedules_are_equal() -> None:
    assert groups_equal([], [])
