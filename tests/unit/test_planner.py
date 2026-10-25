"""Unit tests for turning an EMHASS battery plan into FoxESS charge windows."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from custom_components.emhass_foxess_bridge.planner import (
    ChargeWindow,
    PlanError,
    PlannerSettings,
    PlanStep,
    build_schedule,
    find_charge_windows,
    parse_plan,
    select_horizon,
    windows_to_groups,
)
from custom_components.emhass_foxess_bridge.schedule import SchedulerGroup, WorkMode

if TYPE_CHECKING:
    from collections.abc import Callable

BERLIN = ZoneInfo("Europe/Berlin")
STEP = timedelta(minutes=15)
NIGHT = datetime(2026, 1, 16, 2, 0, tzinfo=BERLIN)


def at(text: str) -> datetime:
    """Berlin local timestamp from '2026-01-16 02:00'."""
    return datetime.fromisoformat(text).replace(tzinfo=BERLIN)


def emhass_entries(
    start: datetime,
    values: list[float],
    key: str = "p_batt_forecast",
    step: timedelta = STEP,
) -> list[dict[str, str]]:
    """Build the list EMHASS publishes: str(pd.Timestamp) dates, string values."""
    return [
        {"date": (start + i * step).isoformat(sep=" "), key: str(float(value))}
        for i, value in enumerate(values)
    ]


def window(start: str, end: str, energy_wh: float = 1000.0) -> ChargeWindow:
    return ChargeWindow(start=at(start), end=at(end), energy_wh=energy_wh)


# --- parse_plan -------------------------------------------------------------


def test_parse_reads_the_emhass_attribute_format() -> None:
    entries = emhass_entries(at("2026-01-15 17:00"), [900, 850, -5000])
    plan = parse_plan(entries)
    assert plan.step == STEP
    assert [s.start for s in plan.steps] == [
        at("2026-01-15 17:00"),
        at("2026-01-15 17:15"),
        at("2026-01-15 17:30"),
    ]
    assert plan.steps[0].end == at("2026-01-15 17:15")
    assert [s.battery_w for s in plan.steps] == [900.0, 850.0, -5000.0]
    assert plan.steps[0].grid_w is None


def test_parse_accepts_numbers_and_prefixed_value_keys() -> None:
    entries = [
        {"date": "2026-01-15 17:00:00+01:00", "winter_p_batt_forecast": -2500},
        {"date": "2026-01-15 17:15:00+01:00", "winter_p_batt_forecast": 0},
    ]
    plan = parse_plan(entries)
    assert [s.battery_w for s in plan.steps] == [-2500.0, 0.0]


def test_parse_sorts_entries_by_time() -> None:
    entries = emhass_entries(at("2026-01-15 17:00"), [1, 2, 3])
    plan = parse_plan(list(reversed(entries)))
    assert [s.battery_w for s in plan.steps] == [1.0, 2.0, 3.0]


def test_parse_joins_the_grid_forecast_by_timestamp() -> None:
    battery = emhass_entries(NIGHT, [-5000, -900])
    grid = emhass_entries(NIGHT, [5500, 0], key="p_grid_forecast")
    plan = parse_plan(battery, grid)
    assert [s.grid_w for s in plan.steps] == [5500.0, 0.0]


def test_parse_leaves_grid_unknown_for_steps_without_a_grid_value() -> None:
    battery = emhass_entries(NIGHT, [-5000, -900])
    grid = emhass_entries(NIGHT, [5500], key="p_grid_forecast")
    plan = parse_plan(battery, grid)
    assert [s.grid_w for s in plan.steps] == [5500.0, None]


def test_parse_rejects_non_uniform_steps() -> None:
    entries = emhass_entries(at("2026-01-15 17:00"), [1, 2])
    entries.append({"date": "2026-01-15 17:40:00+01:00", "p_batt_forecast": "3.0"})
    with pytest.raises(PlanError, match="uniform"):
        parse_plan(entries)


def test_parse_rejects_duplicate_timestamps() -> None:
    entries = emhass_entries(at("2026-01-15 17:00"), [1, 2])
    entries.append(dict(entries[0]))
    with pytest.raises(PlanError, match="duplicate"):
        parse_plan(entries)


def test_parse_rejects_naive_timestamps_without_a_zone() -> None:
    with pytest.raises(PlanError, match="time zone"):
        parse_plan([{"date": "2026-01-15 17:00:00", "p_batt_forecast": "1.0"}])


def test_parse_localises_naive_timestamps_with_the_given_zone() -> None:
    plan = parse_plan(
        [{"date": "2026-01-15 17:00:00", "p_batt_forecast": "1.0"}], tz=BERLIN
    )
    assert plan.steps[0].start == at("2026-01-15 17:00")


def test_parse_rejects_an_empty_plan() -> None:
    with pytest.raises(PlanError, match="empty"):
        parse_plan([])


def test_single_entry_uses_the_default_step() -> None:
    plan = parse_plan(emhass_entries(NIGHT, [1]))
    assert plan.step == STEP
    assert plan.steps[0].end == NIGHT + STEP


@pytest.mark.parametrize(
    "entry",
    [
        {"date": "2026-01-15 17:00:00+01:00", "p_batt_forecast": "n/a"},
        {"date": "2026-01-15 17:00:00+01:00"},
        {"p_batt_forecast": "1.0"},
        {"date": "yesterday", "p_batt_forecast": "1.0"},
    ],
)
def test_parse_rejects_unreadable_entries(entry: dict[str, str]) -> None:
    with pytest.raises(PlanError):
        parse_plan([entry])


# --- select_horizon ---------------------------------------------------------


def test_horizon_keeps_the_running_step_and_drops_finished_ones() -> None:
    plan = parse_plan(emhass_entries(at("2026-01-15 16:45"), [1, 2, 3]))
    kept = select_horizon(plan, now=at("2026-01-15 17:10"))
    assert [s.start for s in kept] == [at("2026-01-15 17:00"), at("2026-01-15 17:15")]


def test_horizon_covers_one_day_so_no_time_of_day_repeats() -> None:
    plan = parse_plan(emhass_entries(at("2026-01-15 17:00"), [1] * 120))  # 30 h
    kept = select_horizon(plan, now=at("2026-01-15 17:10"))
    assert len(kept) == 96
    assert kept[0].start == at("2026-01-15 17:00")
    assert kept[-1].start == at("2026-01-16 16:45")
    assert len({s.start.time() for s in kept}) == len(kept)


def test_horizon_is_empty_when_the_plan_lies_in_the_past() -> None:
    plan = parse_plan(emhass_entries(at("2026-01-15 10:00"), [1, 2]))
    assert select_horizon(plan, now=at("2026-01-15 17:10")) == []


def test_horizon_can_be_shortened() -> None:
    plan = parse_plan(emhass_entries(at("2026-01-15 17:00"), [1] * 8))
    kept = select_horizon(plan, now=at("2026-01-15 17:00"), horizon=timedelta(hours=1))
    assert len(kept) == 4


# --- find_charge_windows ----------------------------------------------------


def test_consecutive_charge_steps_merge_into_one_window() -> None:
    plan = parse_plan(
        emhass_entries(
            at("2026-01-16 01:30"), [400, 400, -5000, -5000, -5000, -5000, 600]
        )
    )
    windows = find_charge_windows(plan.steps, PlannerSettings())
    assert windows == [
        ChargeWindow(start=NIGHT, end=at("2026-01-16 03:00"), energy_wh=5000.0)
    ]


def test_charging_below_the_threshold_is_self_use() -> None:
    plan = parse_plan(emhass_entries(NIGHT, [-50, -99, -100]))
    settings = PlannerSettings(charge_threshold_w=100, min_window_minutes=15)
    windows = find_charge_windows(plan.steps, settings)
    assert [w.start for w in windows] == [NIGHT + 2 * STEP]


def test_pv_surplus_charging_without_grid_import_is_self_use() -> None:
    battery = emhass_entries(NIGHT, [-900, -900, -5000])
    grid = emhass_entries(NIGHT, [0, -200, 4200], key="p_grid_forecast")
    plan = parse_plan(battery, grid)
    windows = find_charge_windows(plan.steps, PlannerSettings(min_window_minutes=15))
    assert [w.start for w in windows] == [NIGHT + 2 * STEP]


def test_a_gap_between_steps_splits_windows() -> None:
    steps = [
        PlanStep(NIGHT, NIGHT + STEP, -5000.0),
        PlanStep(NIGHT + STEP, NIGHT + 2 * STEP, -5000.0),
        PlanStep(NIGHT + 4 * STEP, NIGHT + 5 * STEP, -5000.0),
    ]
    windows = find_charge_windows(steps, PlannerSettings(min_window_minutes=15))
    assert [(w.start, w.end) for w in windows] == [
        (NIGHT, NIGHT + 2 * STEP),
        (NIGHT + 4 * STEP, NIGHT + 5 * STEP),
    ]


def test_windows_snap_outward_to_the_quarter_hour_grid() -> None:
    five = timedelta(minutes=5)
    plan = parse_plan(emhass_entries(at("2026-01-16 02:05"), [-5000] * 6, step=five))
    windows = find_charge_windows(plan.steps, PlannerSettings())
    assert (windows[0].start, windows[0].end) == (NIGHT, at("2026-01-16 02:45"))


def test_windows_shorter_than_the_minimum_are_dropped() -> None:
    plan = parse_plan(emhass_entries(at("2026-01-16 13:00"), [-5000, 300, 300]))
    assert find_charge_windows(plan.steps, PlannerSettings(min_window_minutes=30)) == []
    kept = find_charge_windows(plan.steps, PlannerSettings(min_window_minutes=15))
    assert len(kept) == 1


def test_window_energy_sums_the_charged_watt_hours() -> None:
    plan = parse_plan(emhass_entries(NIGHT, [-5000, -2000]))
    windows = find_charge_windows(plan.steps, PlannerSettings())
    assert windows[0].energy_wh == pytest.approx(1750.0)


# --- windows_to_groups ------------------------------------------------------


def test_window_becomes_a_force_charge_group_with_inclusive_end_minute() -> None:
    groups = windows_to_groups(
        [window("2026-01-16 02:00", "2026-01-16 05:00")],
        PlannerSettings(min_soc_on_grid=15),
    )
    assert groups == [
        SchedulerGroup(
            time(2, 0),
            time(4, 59),
            WorkMode.FORCE_CHARGE,
            min_soc_on_grid=15,
            fd_soc=15,
        )
    ]


def test_window_crossing_midnight_is_split_in_two() -> None:
    groups = windows_to_groups(
        [window("2026-01-15 23:00", "2026-01-16 01:00")], PlannerSettings()
    )
    assert [(g.start, g.end) for g in groups] == [
        (time(0, 0), time(0, 59)),
        (time(23, 0), time(23, 59)),
    ]


def test_window_ending_at_midnight_is_one_group() -> None:
    groups = windows_to_groups(
        [window("2026-01-15 22:00", "2026-01-16 00:00")], PlannerSettings()
    )
    assert [(g.start, g.end) for g in groups] == [(time(22, 0), time(23, 59))]


def test_groups_are_capped_by_keeping_the_most_energy() -> None:
    windows = [
        window(f"2026-01-16 {h:02d}:00", f"2026-01-16 {h:02d}:30", energy_wh=100.0 * h)
        for h in range(1, 11)
    ]
    groups = windows_to_groups(windows, PlannerSettings(max_groups=8))
    assert [g.start.hour for g in groups] == list(range(3, 11))


def test_cap_counts_a_split_window_as_two_groups() -> None:
    windows = [
        window("2026-01-15 23:30", "2026-01-16 00:30", energy_wh=5000.0),
        *(
            window(
                f"2026-01-16 {h:02d}:00", f"2026-01-16 {h:02d}:30", energy_wh=1000.0 * h
            )
            for h in range(1, 9)
        ),
    ]
    groups = windows_to_groups(windows, PlannerSettings(max_groups=8))
    starts = [g.start for g in groups]
    assert len(groups) == 8
    assert time(1, 0) not in starts
    assert time(0, 0) in starts
    assert time(23, 30) in starts


def test_groups_use_the_inverter_time_zone() -> None:
    utc_window = ChargeWindow(
        start=datetime(2026, 1, 16, 1, 0, tzinfo=UTC),
        end=datetime(2026, 1, 16, 4, 0, tzinfo=UTC),
        energy_wh=15000.0,
    )
    groups = windows_to_groups([utc_window], PlannerSettings(), tz=BERLIN)
    assert (groups[0].start, groups[0].end) == (time(2, 0), time(4, 59))


# --- build_schedule ---------------------------------------------------------


def test_winter_day_plan_yields_the_night_charge_block(
    load_fixture: Callable,
) -> None:
    fixture = load_fixture("emhass_plan_winter_day.json")
    plan = parse_plan(
        fixture["sensor.p_batt_forecast"]["attributes"]["battery_scheduled_power"],
        fixture["sensor.p_grid_forecast"]["attributes"]["forecasts"],
    )
    groups = build_schedule(
        plan, now=at("2026-01-15 17:10"), settings=PlannerSettings()
    )
    assert [str(g) for g in groups] == ["ForceCharge 02:00-04:59"]
    assert groups[0].min_soc_on_grid == 10


def test_without_a_grid_forecast_midday_pv_charging_would_be_forced(
    load_fixture: Callable,
) -> None:
    """Why the grid forecast matters: p_batt alone cannot tell PV from grid."""
    fixture = load_fixture("emhass_plan_winter_day.json")
    plan = parse_plan(
        fixture["sensor.p_batt_forecast"]["attributes"]["battery_scheduled_power"]
    )
    groups = build_schedule(
        plan, now=at("2026-01-15 17:10"), settings=PlannerSettings()
    )
    assert [str(g) for g in groups] == [
        "ForceCharge 02:00-04:59",
        "ForceCharge 11:00-14:59",
    ]


def test_a_plan_without_grid_charging_yields_no_groups() -> None:
    plan = parse_plan(emhass_entries(NIGHT, [500] * 8))
    assert build_schedule(plan, now=NIGHT, settings=PlannerSettings()) == []


def test_build_schedule_only_looks_one_day_ahead() -> None:
    """A charge block 25 h away must not be written yet (it would fire today)."""
    values = [500] * 100 + [-5000] * 4
    plan = parse_plan(emhass_entries(at("2026-01-15 17:00"), values))
    assert (
        build_schedule(plan, now=at("2026-01-15 17:00"), settings=PlannerSettings())
        == []
    )
