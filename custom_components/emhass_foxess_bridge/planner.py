"""
Turn an EMHASS battery plan into FoxESS scheduler groups.

Pure Python: no Home Assistant imports. The flow is

    parse_plan -> select_horizon -> find_charge_windows -> windows_to_groups

and :func:`build_schedule` runs all four. Only grid charging is mapped, to
``ForceCharge`` groups; everything else stays in the inverter's own self-use
logic. FoxESS groups are time-of-day ranges that repeat daily, which is why the
horizon is capped at 24 h: a step 25 h ahead would otherwise fire today.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from .schedule import MAX_GROUPS, MIN_SOC, SchedulerGroup, WorkMode, validate_groups

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

_LOGGER = logging.getLogger(__name__)

DEFAULT_STEP = timedelta(minutes=15)
DEFAULT_HORIZON = timedelta(hours=24)
ONE_MINUTE = timedelta(minutes=1)
# EMHASS puts the timestamp under "date" and the value under the entity's
# object id, e.g. "p_batt_forecast" or "<publish_prefix>p_batt_forecast".
DATE_KEY = "date"


class PlanError(ValueError):
    """The EMHASS plan cannot be interpreted."""


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One optimisation step. EMHASS signs: battery > 0 discharges, grid > 0 imports."""

    start: datetime
    end: datetime
    battery_w: float
    grid_w: float | None = None

    @property
    def duration(self) -> timedelta:
        """Length of the step."""
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class Plan:
    """A parsed plan: sorted, uniformly spaced, timezone-aware steps."""

    steps: tuple[PlanStep, ...]
    step: timedelta


@dataclass(frozen=True, slots=True)
class ChargeWindow:
    """A continuous grid-charging period; ``end`` is exclusive."""

    start: datetime
    end: datetime
    energy_wh: float

    @property
    def duration(self) -> timedelta:
        """Length of the window."""
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class PlannerSettings:
    """Tunables; the defaults suit a 15-minute EMHASS plan."""

    charge_threshold_w: float = 100.0
    grid_import_threshold_w: float = 100.0
    min_window_minutes: int = 30
    grid_minutes: int = 15
    max_groups: int = MAX_GROUPS
    horizon: timedelta = DEFAULT_HORIZON
    min_soc_on_grid: int = MIN_SOC


# --- parsing -----------------------------------------------------------------


def _parse_entry(entry: Mapping[str, Any], tz: tzinfo | None) -> tuple[datetime, float]:
    if DATE_KEY not in entry:
        msg = f"plan entry has no '{DATE_KEY}': {entry!r}"
        raise PlanError(msg)
    try:
        when = datetime.fromisoformat(str(entry[DATE_KEY]))
    except ValueError as exc:
        msg = f"plan entry has an unreadable date: {entry[DATE_KEY]!r}"
        raise PlanError(msg) from exc
    if when.tzinfo is None:
        if tz is None:
            msg = f"plan timestamp {when} has no time zone and none was given"
            raise PlanError(msg)
        when = when.replace(tzinfo=tz)

    value_keys = [key for key in entry if key != DATE_KEY]
    if not value_keys:
        msg = f"plan entry has no value: {entry!r}"
        raise PlanError(msg)
    raw = entry[value_keys[0]]
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        msg = f"plan entry has an unreadable value: {raw!r}"
        raise PlanError(msg) from exc
    if not math.isfinite(value):
        msg = f"plan entry has a non-finite value: {raw!r}"
        raise PlanError(msg)
    return when, value


def _parse_series(
    entries: Iterable[Mapping[str, Any]], tz: tzinfo | None
) -> dict[datetime, float]:
    series: dict[datetime, float] = {}
    for entry in entries:
        when, value = _parse_entry(entry, tz)
        if when in series:
            msg = f"plan contains a duplicate timestamp: {when}"
            raise PlanError(msg)
        series[when] = value
    return series


def parse_plan(
    battery: Iterable[Mapping[str, Any]],
    grid: Iterable[Mapping[str, Any]] | None = None,
    *,
    tz: tzinfo | None = None,
    default_step: timedelta = DEFAULT_STEP,
) -> Plan:
    """
    Parse the ``battery_scheduled_power`` list, optionally joined with ``forecasts``.

    ``battery`` comes from ``sensor.p_batt_forecast``, ``grid`` from
    ``sensor.p_grid_forecast``. Both lists are matched on their timestamps;
    steps without a grid value keep ``grid_w=None``. Naive timestamps are
    localised to ``tz``; without a zone they are rejected.
    """
    battery_series = _parse_series(battery, tz)
    if not battery_series:
        msg = "plan is empty"
        raise PlanError(msg)
    grid_series = _parse_series(grid, tz) if grid is not None else {}

    starts = sorted(battery_series)
    step = default_step
    if len(starts) > 1:
        deltas = {later - earlier for earlier, later in pairwise(starts)}
        if len(deltas) != 1:
            msg = f"plan steps are not uniform: {sorted(deltas)}"
            raise PlanError(msg)
        step = deltas.pop()

    steps = tuple(
        PlanStep(
            start=start,
            end=start + step,
            battery_w=battery_series[start],
            grid_w=grid_series.get(start),
        )
        for start in starts
    )
    return Plan(steps=steps, step=step)


# --- horizon -----------------------------------------------------------------


def select_horizon(
    plan: Plan, now: datetime, horizon: timedelta = DEFAULT_HORIZON
) -> list[PlanStep]:
    """
    Keep the steps whose end lies in ``(now, now + horizon]``.

    The running step is kept so the current window is not cut short, and the
    window of step ends is exactly one horizon long, so with a 24 h horizon no
    time of day occurs twice.
    """
    limit = now + horizon
    return [step for step in plan.steps if now < step.end <= limit]


# --- windows -----------------------------------------------------------------


def _is_grid_charge(step: PlanStep, settings: PlannerSettings) -> bool:
    if step.battery_w > -settings.charge_threshold_w:
        return False
    if step.grid_w is None:
        return True
    return step.grid_w >= settings.grid_import_threshold_w


def _floor_to_grid(when: datetime, grid_minutes: int) -> datetime:
    local = when.replace(second=0, microsecond=0)
    return local.replace(minute=local.minute - local.minute % grid_minutes)


def _ceil_to_grid(when: datetime, grid_minutes: int) -> datetime:
    floored = _floor_to_grid(when, grid_minutes)
    if floored == when:
        return when
    return floored + timedelta(minutes=grid_minutes)


def _snap(window: ChargeWindow, grid_minutes: int) -> ChargeWindow:
    return ChargeWindow(
        start=_floor_to_grid(window.start, grid_minutes),
        end=_ceil_to_grid(window.end, grid_minutes),
        energy_wh=window.energy_wh,
    )


def find_charge_windows(
    steps: Sequence[PlanStep], settings: PlannerSettings
) -> list[ChargeWindow]:
    """Merge adjacent grid-charging steps into windows on the quarter-hour grid."""
    windows: list[ChargeWindow] = []
    current: ChargeWindow | None = None
    for step in steps:
        if not _is_grid_charge(step, settings):
            if current is not None:
                windows.append(current)
                current = None
            continue
        energy = -step.battery_w * step.duration.total_seconds() / 3600
        if current is not None and current.end == step.start:
            current = ChargeWindow(current.start, step.end, current.energy_wh + energy)
        else:
            if current is not None:
                windows.append(current)
            current = ChargeWindow(step.start, step.end, energy)
    if current is not None:
        windows.append(current)

    minimum = timedelta(minutes=settings.min_window_minutes)
    result: list[ChargeWindow] = []
    for window in windows:
        snapped = _snap(window, settings.grid_minutes)
        if snapped.duration < minimum:
            _LOGGER.debug(
                "Dropping %s-%s: shorter than %s",
                snapped.start,
                snapped.end,
                minimum,
            )
            continue
        result.append(snapped)
    return result


# --- groups ------------------------------------------------------------------


def _groups_for_window(
    window: ChargeWindow, settings: PlannerSettings, tz: tzinfo
) -> list[SchedulerGroup]:
    """Map one window to one or, across midnight, two ForceCharge groups."""
    start = window.start.astimezone(tz)
    last_minute = (window.end - ONE_MINUTE).astimezone(tz)

    def group(begin: time, end: time) -> SchedulerGroup:
        return SchedulerGroup(
            start=begin,
            end=end,
            work_mode=WorkMode.FORCE_CHARGE,
            min_soc_on_grid=settings.min_soc_on_grid,
            fd_soc=settings.min_soc_on_grid,
        )

    if start.date() == last_minute.date():
        return [group(start.time(), last_minute.time())]
    return [
        group(start.time(), time(23, 59)),
        group(time(0, 0), last_minute.time()),
    ]


def windows_to_groups(
    windows: Sequence[ChargeWindow],
    settings: PlannerSettings,
    tz: tzinfo | None = None,
) -> list[SchedulerGroup]:
    """
    Build ForceCharge groups in the inverter's local time, capped at ``max_groups``.

    When the cap would be exceeded the windows with the least planned energy
    are dropped first. ``tz`` defaults to the zone of the first window.
    """
    if not windows:
        return []
    zone = tz or windows[0].start.tzinfo
    if zone is None:
        msg = "windows must be timezone-aware"
        raise PlanError(msg)

    candidates = [(w, _groups_for_window(w, settings, zone)) for w in windows]
    kept: list[SchedulerGroup] = []
    for window, groups in sorted(
        candidates, key=lambda c: c[0].energy_wh, reverse=True
    ):
        if len(kept) + len(groups) > settings.max_groups:
            _LOGGER.warning(
                "Dropping charge window %s-%s (%.0f Wh): more than %d groups planned",
                window.start,
                window.end,
                window.energy_wh,
                settings.max_groups,
            )
            continue
        kept.extend(groups)
    kept.sort(key=lambda g: g.start)
    validate_groups(kept)
    return kept


def build_schedule(
    plan: Plan,
    now: datetime,
    settings: PlannerSettings,
    tz: tzinfo | None = None,
) -> list[SchedulerGroup]:
    """Return the groups the inverter should carry for the next ``settings.horizon``."""
    steps = select_horizon(plan, now, settings.horizon)
    windows = find_charge_windows(steps, settings)
    return windows_to_groups(windows, settings, tz or now.tzinfo)
