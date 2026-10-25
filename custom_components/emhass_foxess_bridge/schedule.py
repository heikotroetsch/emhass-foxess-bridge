"""
FoxESS mode-scheduler groups: model, validation and comparison.

Pure Python on purpose: no Home Assistant imports, so the logic can be unit
tested in milliseconds and reused by other front ends.

A group is a time-of-day range that repeats every day. The end minute is
inclusive, so the FoxESS app writes a window ending at 05:00 as ``04:59``.
The field set is scheduler V1: ``maxSoc`` caps a ``ForceCharge`` group.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

MAX_GROUPS = 8
MIN_SOC = 10
MAX_SOC = 100
END_OF_DAY = time(23, 59)


class WorkMode(StrEnum):
    """Work modes accepted by the scheduler, spelled as the API expects."""

    SELF_USE = "SelfUse"
    FEED_IN = "Feedin"
    BACKUP = "Backup"
    FORCE_CHARGE = "ForceCharge"
    FORCE_DISCHARGE = "ForceDischarge"


class ScheduleError(ValueError):
    """A group, or a set of groups, violates the FoxESS scheduler rules."""


@dataclass(frozen=True, slots=True)
class SchedulerGroup:
    """One scheduler group; ``end`` is the last minute the group is active."""

    start: time
    end: time
    work_mode: WorkMode = WorkMode.SELF_USE
    min_soc_on_grid: int = MIN_SOC
    fd_soc: int = MIN_SOC
    fd_pwr: int = 0
    max_soc: int = MAX_SOC
    enabled: bool = True

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> SchedulerGroup:
        """Build a group from a ``/scheduler/get`` entry; unknown keys are ignored."""
        max_soc = data.get("maxSoc")
        return cls(
            start=time(int(data["startHour"]), int(data["startMinute"])),
            end=time(int(data["endHour"]), int(data["endMinute"])),
            work_mode=WorkMode(data["workMode"]),
            min_soc_on_grid=int(data.get("minSocOnGrid", MIN_SOC)),
            fd_soc=int(data.get("fdSoc", MIN_SOC)),
            fd_pwr=int(data.get("fdPwr", 0)),
            max_soc=MAX_SOC if max_soc is None else int(max_soc),
            enabled=bool(int(data.get("enable", 1))),
        )

    def to_api(self) -> dict[str, Any]:
        """Return the JSON object ``/scheduler/enable`` expects for this group."""
        return {
            "enable": int(self.enabled),
            "startHour": self.start.hour,
            "startMinute": self.start.minute,
            "endHour": self.end.hour,
            "endMinute": self.end.minute,
            "workMode": self.work_mode.value,
            "minSocOnGrid": self.min_soc_on_grid,
            "fdSoc": self.fd_soc,
            "fdPwr": self.fd_pwr,
            "maxSoc": self.max_soc,
        }

    def __str__(self) -> str:
        """Compact form for logs and attributes, e.g. ``ForceCharge 02:00-04:59``."""
        return f"{self.work_mode.value} {self.start:%H:%M}-{self.end:%H:%M}"


def _validate_one(group: SchedulerGroup) -> None:
    for label, value in (("start", group.start), ("end", group.end)):
        if value.second or value.microsecond:
            msg = f"{group}: {label} time must be in whole minutes"
            raise ScheduleError(msg)
    if group.end < group.start:
        msg = f"{group} ends before it starts"
        raise ScheduleError(msg)
    for label, value in (
        ("minSocOnGrid", group.min_soc_on_grid),
        ("fdSoc", group.fd_soc),
        ("maxSoc", group.max_soc),
    ):
        if not MIN_SOC <= value <= MAX_SOC:
            msg = (
                f"{group}: {label} must be between {MIN_SOC} and {MAX_SOC}, got {value}"
            )
            raise ScheduleError(msg)
    if group.fd_soc < group.min_soc_on_grid:
        msg = f"{group}: fdSoc must not be below minSocOnGrid"
        raise ScheduleError(msg)
    if group.fd_pwr < 0:
        msg = f"{group}: fdPwr must not be negative"
        raise ScheduleError(msg)


def validate_groups(groups: Sequence[SchedulerGroup]) -> None:
    """Raise ScheduleError unless the groups can be written to an inverter."""
    if len(groups) > MAX_GROUPS:
        msg = f"FoxESS accepts at most {MAX_GROUPS} groups, got {len(groups)}"
        raise ScheduleError(msg)
    for group in groups:
        _validate_one(group)
    active = sorted((g for g in groups if g.enabled), key=lambda g: g.start)
    for earlier, later in pairwise(active):
        if later.start <= earlier.end:
            msg = f"{earlier} and {later} overlap"
            raise ScheduleError(msg)


def _comparable(group: SchedulerGroup) -> tuple[Any, ...]:
    """Fields that carry meaning for this work mode; FoxESS rewrites the rest."""
    key: tuple[Any, ...] = (
        group.start,
        group.end,
        group.work_mode,
        group.min_soc_on_grid,
    )
    if group.work_mode is WorkMode.FORCE_DISCHARGE:
        key += (group.fd_soc, group.fd_pwr)
    if group.work_mode is WorkMode.FORCE_CHARGE:
        key += (group.max_soc,)
    return key


def groups_equal(
    desired: Iterable[SchedulerGroup], current: Iterable[SchedulerGroup]
) -> bool:
    """Return True when both schedules behave the same on the inverter."""
    return sorted(_comparable(g) for g in desired if g.enabled) == sorted(
        _comparable(g) for g in current if g.enabled
    )
