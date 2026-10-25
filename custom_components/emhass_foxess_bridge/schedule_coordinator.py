"""
The apply loop: read the EMHASS plan, compare with the inverter, write on change.

Runs on every quarter hour and, debounced, whenever one of the EMHASS plan
sensors changes. Nothing is written unless the bridge is enabled and dry-run
is off. A missing or outdated plan clears the schedule once so the inverter
falls back to self-use.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import FoxApiError, FoxApiRateLimitError
from .const import (
    APPLY_MINUTES,
    APPLY_SECOND,
    ATTR_BATTERY_SCHEDULE,
    ATTR_GRID_FORECAST,
    CONF_CHARGE_THRESHOLD_W,
    CONF_DRY_RUN,
    CONF_EMHASS_ENTITY,
    CONF_EMHASS_GRID_ENTITY,
    CONF_ENABLED,
    CONF_MAX_GROUPS,
    CONF_MIN_WINDOW_MINUTES,
    CONF_PLAN_MAX_AGE_HOURS,
    DEFAULT_DRY_RUN,
    DEFAULT_ENABLED,
    DEFAULT_PLAN_MAX_AGE_HOURS,
    DOMAIN,
    ISSUE_RATE_LIMITED,
    LOGGER,
    PLAN_SETTLE_SECONDS,
    WRITE_SETTLE_SECONDS,
)
from .planner import PlanError, PlannerSettings, build_schedule, parse_plan
from .schedule import SchedulerGroup, groups_equal

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import Event, EventStateChangedData, HomeAssistant

    from .api import FoxOpenApiClient
    from .data import BridgeConfigEntry

type Groups = tuple[SchedulerGroup, ...]


class ApplyResult(StrEnum):
    """Outcome of one apply run, exposed as an enum sensor."""

    DISABLED = "disabled"
    DRY_RUN = "dry_run"
    UNCHANGED = "unchanged"
    PENDING = "pending"
    WRITTEN = "written"
    CLEARED = "cleared"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ScheduleState:
    """Everything the schedule entities show."""

    planned: Groups = ()
    active: Groups = ()
    scheduler_enabled: bool | None = None
    plan_stale: bool = True
    stale_reason: str | None = None
    plan_updated_at: datetime | None = None
    last_apply_at: datetime | None = None
    last_result: ApplyResult = ApplyResult.DISABLED
    last_detail: str | None = None
    writes_blocked_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class _PlanReading:
    groups: Groups
    stale: bool
    reason: str | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class _WriteRecord:
    at: datetime
    groups: Groups
    scheduler_enabled: bool


class ScheduleCoordinator(DataUpdateCoordinator[ScheduleState]):
    """Compute the desired schedule and keep the inverter in sync with it."""

    config_entry: BridgeConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: BridgeConfigEntry,
        client: FoxOpenApiClient,
        device_sn: str,
        min_soc_on_grid: int,
    ) -> None:
        """Bind the loop to one inverter. No interval: the timer below drives it."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} schedule",
            update_interval=None,
            request_refresh_debouncer=Debouncer(
                hass, LOGGER, cooldown=PLAN_SETTLE_SECONDS, immediate=False
            ),
        )
        self._client = client
        self._device_sn = device_sn
        self._min_soc_on_grid = min_soc_on_grid
        self._last_write: _WriteRecord | None = None

    # --- wiring ---------------------------------------------------------------

    @callback
    def async_start(self) -> Callable[[], None]:
        """Subscribe to plan changes and the quarter-hour timer; returns unsubscribe."""
        entities = [self.config_entry.data[CONF_EMHASS_ENTITY]]
        if grid := self.config_entry.data.get(CONF_EMHASS_GRID_ENTITY):
            entities.append(grid)
        unsub_state = async_track_state_change_event(
            self.hass, entities, self._handle_plan_change
        )
        unsub_time = async_track_time_change(
            self.hass,
            self._handle_tick,
            minute=list(APPLY_MINUTES),
            second=APPLY_SECOND,
        )

        def _unsubscribe() -> None:
            unsub_state()
            unsub_time()

        return _unsubscribe

    @callback
    def _handle_plan_change(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_request_refresh())

    async def _handle_tick(self, _now: datetime) -> None:
        await self.async_refresh()

    # --- manual operations (services) ---------------------------------------------

    async def async_write_groups(self, groups: Groups, *, enable: bool) -> None:
        """Write groups now. Meant for experiments while the bridge is not live."""
        await self._client.async_set_scheduler_groups(
            self._device_sn, [g.to_api() for g in groups]
        )
        await self._client.async_set_scheduler_enabled(self._device_sn, enabled=enable)
        self._last_write = _WriteRecord(
            at=dt_util.utcnow(),
            groups=groups if enable else (),
            scheduler_enabled=enable,
        )
        await self.async_refresh()

    async def async_clear(self) -> None:
        """Disable the mode scheduler and switch the bridge off so it stays off."""
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_ENABLED: False}
        )
        await self._client.async_set_scheduler_enabled(self._device_sn, enabled=False)
        self._last_write = _WriteRecord(
            at=dt_util.utcnow(), groups=(), scheduler_enabled=False
        )
        await self.async_refresh()

    # --- settings ---------------------------------------------------------------

    @property
    def bridge_enabled(self) -> bool:
        """Whether writes to the inverter are allowed at all."""
        return bool(self.config_entry.options.get(CONF_ENABLED, DEFAULT_ENABLED))

    @property
    def dry_run(self) -> bool:
        """Whether writes are only logged."""
        return bool(self.config_entry.options.get(CONF_DRY_RUN, DEFAULT_DRY_RUN))

    def _settings(self) -> PlannerSettings:
        options = self.config_entry.options
        defaults = PlannerSettings()
        return PlannerSettings(
            charge_threshold_w=float(
                options.get(CONF_CHARGE_THRESHOLD_W, defaults.charge_threshold_w)
            ),
            min_window_minutes=int(
                options.get(CONF_MIN_WINDOW_MINUTES, defaults.min_window_minutes)
            ),
            max_groups=int(options.get(CONF_MAX_GROUPS, defaults.max_groups)),
            min_soc_on_grid=self._min_soc_on_grid,
        )

    def _plan_max_age(self) -> timedelta:
        hours = self.config_entry.options.get(
            CONF_PLAN_MAX_AGE_HOURS, DEFAULT_PLAN_MAX_AGE_HOURS
        )
        return timedelta(hours=float(hours))

    # --- plan -------------------------------------------------------------------

    def _read_plan(self, now: datetime) -> _PlanReading:
        entity_id = self.config_entry.data[CONF_EMHASS_ENTITY]
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return _stale(f"{entity_id} is unavailable")
        entries = state.attributes.get(ATTR_BATTERY_SCHEDULE)
        if not entries:
            reason = f"{entity_id} has no {ATTR_BATTERY_SCHEDULE} attribute"
            return _stale(reason, state.last_updated)
        if now - state.last_updated > self._plan_max_age():
            reason = f"plan in {entity_id} is older than {self._plan_max_age()}"
            return _stale(reason, state.last_updated)

        tz = dt_util.get_default_time_zone()
        try:
            plan = parse_plan(entries, self._grid_entries(), tz=tz)
            groups = build_schedule(plan, now, self._settings(), tz=tz)
        except PlanError as exc:
            return _stale(f"{entity_id}: {exc}", state.last_updated)
        return _PlanReading(
            groups=tuple(groups),
            stale=False,
            reason=None,
            updated_at=state.last_updated,
        )

    def _grid_entries(self) -> list[dict[str, Any]] | None:
        grid_entity = self.config_entry.data.get(CONF_EMHASS_GRID_ENTITY)
        if not grid_entity:
            return None
        grid_state = self.hass.states.get(grid_entity)
        if grid_state is None or grid_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return grid_state.attributes.get(ATTR_GRID_FORECAST)

    # --- apply ------------------------------------------------------------------

    async def _async_update_data(self) -> ScheduleState:
        now = dt_util.utcnow()
        previous = self.data or ScheduleState()
        reading = self._read_plan(now)
        state = replace(
            previous,
            planned=reading.groups,
            plan_stale=reading.stale,
            stale_reason=reading.reason,
            plan_updated_at=reading.updated_at,
        )

        if previous.writes_blocked_until is not None:
            if now < previous.writes_blocked_until:
                return state  # quota exhausted: do not spend a single call
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_RATE_LIMITED)
            state = replace(state, writes_blocked_until=None)

        desired: Groups = () if reading.stale else reading.groups
        try:
            return await self._sync(state, desired, now)
        except FoxApiRateLimitError:
            return self._rate_limited(state, now)
        except FoxApiError as exc:
            LOGGER.warning("FoxESS scheduler call failed: %s", exc)
            return _finish(state, now, ApplyResult.ERROR, str(exc))

    async def _sync(
        self, state: ScheduleState, desired: Groups, now: datetime
    ) -> ScheduleState:
        """Read the inverter, compare, and write when allowed and needed."""
        flag = await self._client.async_get_scheduler_flag(self._device_sn)
        raw_groups = await self._client.async_get_scheduler(self._device_sn)
        scheduler_enabled = bool(flag.get("enable"))
        current = _parse_groups(raw_groups)
        effective: Groups = current if scheduler_enabled else ()

        pending = False
        write = self._last_write
        if write is not None and self._is_settling(
            write, now, effective, scheduler_enabled
        ):
            # FoxESS has not caught up with our last write yet; trust the write.
            effective, scheduler_enabled, pending = (
                write.groups,
                write.scheduler_enabled,
                True,
            )

        state = replace(state, active=effective, scheduler_enabled=scheduler_enabled)
        if groups_equal(desired, effective):
            result = ApplyResult.PENDING if pending else ApplyResult.UNCHANGED
            return _finish(state, now, result)
        if not self.bridge_enabled:
            return _finish(state, now, ApplyResult.DISABLED, _describe(desired))
        if self.dry_run:
            LOGGER.info("Dry run: would write %s", _describe(desired))
            return _finish(state, now, ApplyResult.DRY_RUN, _describe(desired))

        result = await self._write(desired, scheduler_enabled=scheduler_enabled)
        self._last_write = _WriteRecord(now, desired, bool(desired))
        LOGGER.info("Wrote schedule to %s: %s", self._device_sn, _describe(desired))
        state = replace(state, active=desired, scheduler_enabled=bool(desired))
        return _finish(state, now, result, _describe(desired))

    @staticmethod
    def _is_settling(
        write: _WriteRecord,
        now: datetime,
        effective: Groups,
        scheduler_enabled: bool,  # noqa: FBT001
    ) -> bool:
        if now - write.at >= timedelta(seconds=WRITE_SETTLE_SECONDS):
            return False
        return scheduler_enabled != write.scheduler_enabled or not groups_equal(
            effective, write.groups
        )

    async def _write(self, desired: Groups, *, scheduler_enabled: bool) -> ApplyResult:
        if not desired:
            await self._client.async_set_scheduler_enabled(
                self._device_sn, enabled=False
            )
            return ApplyResult.CLEARED
        await self._client.async_set_scheduler_groups(
            self._device_sn, [g.to_api() for g in desired]
        )
        if not scheduler_enabled:
            await self._client.async_set_scheduler_enabled(
                self._device_sn, enabled=True
            )
        return ApplyResult.WRITTEN

    def _rate_limited(self, state: ScheduleState, now: datetime) -> ScheduleState:
        blocked_until = dt_util.as_utc(
            dt_util.start_of_local_day(dt_util.as_local(now) + timedelta(days=1))
        )
        LOGGER.warning(
            "FoxESS API quota exhausted; pausing until %s", blocked_until.isoformat()
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_RATE_LIMITED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_RATE_LIMITED,
            translation_placeholders={
                "resume_at": dt_util.as_local(blocked_until).strftime("%H:%M")
            },
        )
        state = replace(state, writes_blocked_until=blocked_until)
        return _finish(
            state,
            now,
            ApplyResult.RATE_LIMITED,
            "FoxESS Open API request limit exceeded",
        )


def _stale(reason: str, updated_at: datetime | None = None) -> _PlanReading:
    return _PlanReading(groups=(), stale=True, reason=reason, updated_at=updated_at)


def _finish(
    state: ScheduleState,
    now: datetime,
    result: ApplyResult,
    detail: str | None = None,
) -> ScheduleState:
    return replace(state, last_apply_at=now, last_result=result, last_detail=detail)


def _parse_groups(raw_groups: list[dict[str, Any]]) -> Groups:
    groups: list[SchedulerGroup] = []
    for raw in raw_groups:
        try:
            groups.append(SchedulerGroup.from_api(raw))
        except (KeyError, ValueError, TypeError) as exc:
            LOGGER.warning("Ignoring scheduler group %r: %s", raw, exc)
    return tuple(groups)


def _describe(groups: Groups) -> str:
    return ", ".join(str(g) for g in groups) or "none"
