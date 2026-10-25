"""
Backend: how the bridge reads and writes the inverter's mode scheduler.

The bridge itself holds no API key. It drives the HACS integration
ha-foxess-openapi (domain ``foxess_openapi``) through its services and reads
the master flag, the inverter's minSocOnGrid and the remaining daily quota
from that integration's entities. Everything the loop needs from the
inverter goes through :class:`FoxBackend`, so another backend (direct API,
Modbus) can be slotted in later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import FOX_DOMAIN, LOGGER
from .schedule import MIN_SOC, SchedulerGroup

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import State

SERVICE_GET_SCHEDULE = "get_schedule"
SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_DISABLE_SCHEDULER = "disable_scheduler"
REQUIRED_SERVICES = (
    SERVICE_GET_SCHEDULE,
    SERVICE_SET_SCHEDULE,
    SERVICE_DISABLE_SCHEDULER,
)

# Unique id suffixes of the ha-foxess-openapi entities the bridge reads.
KEY_MIN_SOC_ON_GRID = "minSocOnGrid"
KEY_SCHEDULE = "schedule"
KEY_API_REMAINING = "api_remaining"
KEY_SCHEDULER_SWITCH = "scheduler_enable"

# ha-foxess-openapi forwards FoxESS errors as HomeAssistantError text.
RATE_LIMIT_MARKERS = ("40400", "limit exceeded", "too frequent")

type Groups = tuple[SchedulerGroup, ...]


class FoxBackendError(Exception):
    """The backend could not read or write the scheduler."""


class FoxBackendRateLimitError(FoxBackendError):
    """The FoxESS daily request quota is exhausted."""


@dataclass(frozen=True, slots=True)
class FoxDevice:
    """The inverter as ha-foxess-openapi registered it."""

    device_id: str
    serial: str
    name: str
    entity_ids: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class BackendReading:
    """One snapshot of the scheduler side of the inverter."""

    scheduler_enabled: bool | None
    groups: Groups
    min_soc_on_grid: int
    api_remaining: int | None


class FoxBackend(Protocol):
    """What the apply loop needs from an inverter."""

    async def async_read(self) -> BackendReading:
        """Return the current scheduler state."""

    async def async_write(self, groups: Groups, *, enable: bool) -> None:
        """Replace all groups and set the master flag."""

    async def async_disable(self) -> None:
        """Switch the master flag off, leaving the groups in place."""


@callback
def async_services_available(hass: HomeAssistant) -> bool:
    """Return True once ha-foxess-openapi has registered the services we call."""
    return all(
        hass.services.has_service(FOX_DOMAIN, service) for service in REQUIRED_SERVICES
    )


@callback
def async_resolve_device(hass: HomeAssistant, device_id: str) -> FoxDevice | None:
    """Find the serial and the entities behind a ha-foxess-openapi device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    serial = next(
        (value for domain, value in device.identifiers if domain == FOX_DOMAIN), None
    )
    if serial is None:
        return None
    prefix = f"{serial}_"
    entity_ids = {
        entry.unique_id[len(prefix) :]: entry.entity_id
        for entry in er.async_entries_for_device(
            er.async_get(hass), device_id, include_disabled_entities=True
        )
        if entry.platform == FOX_DOMAIN and entry.unique_id.startswith(prefix)
    }
    return FoxDevice(
        device_id=device_id,
        serial=serial,
        name=device.name_by_user or device.name or serial,
        entity_ids=entity_ids,
    )


def _translate(exc: HomeAssistantError) -> FoxBackendError:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if any(marker in lowered for marker in RATE_LIMIT_MARKERS):
        return FoxBackendRateLimitError(text)
    return FoxBackendError(text)


class OpenApiServiceBackend:
    """FoxBackend over the ha-foxess-openapi services and entities."""

    def __init__(self, hass: HomeAssistant, device: FoxDevice) -> None:
        """Bind to one inverter."""
        self._hass = hass
        self._device = device

    async def _call(
        self, service: str, data: dict[str, Any], *, response: bool = False
    ) -> Any:
        try:
            return await self._hass.services.async_call(
                FOX_DOMAIN,
                service,
                {"device_sn": self._device.serial, **data},
                blocking=True,
                return_response=response,
            )
        except HomeAssistantError as exc:
            raise _translate(exc) from exc

    def _state(self, key: str) -> State | None:
        entity_id = self._device.entity_ids.get(key)
        if entity_id is None:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state

    def _int_state(self, key: str) -> int | None:
        state = self._state(key)
        if state is None:
            return None
        try:
            return int(float(state.state))
        except ValueError:
            return None

    def _scheduler_enabled(self, response: Mapping[str, Any]) -> bool | None:
        flag = response.get("enable")
        if flag is not None:
            return bool(flag)
        switch = self._state(KEY_SCHEDULER_SWITCH)
        if switch is not None and switch.state in (STATE_ON, STATE_OFF):
            return switch.state == STATE_ON
        schedule = self._state(KEY_SCHEDULE)
        if schedule is not None:
            flag = schedule.attributes.get("master_switch")
            if flag is not None:
                return bool(flag)
        return None

    async def async_read(self) -> BackendReading:
        """Read the groups through the service, the rest from the entities."""
        response = await self._call(SERVICE_GET_SCHEDULE, {}, response=True) or {}
        raw_groups = response.get("groups") or []
        groups: list[SchedulerGroup] = []
        for raw in raw_groups:
            try:
                groups.append(SchedulerGroup.from_api(raw))
            except (KeyError, ValueError, TypeError) as exc:
                LOGGER.warning("Ignoring scheduler group %r: %s", raw, exc)
        min_soc = self._int_state(KEY_MIN_SOC_ON_GRID)
        return BackendReading(
            scheduler_enabled=self._scheduler_enabled(response),
            groups=tuple(groups),
            min_soc_on_grid=MIN_SOC if min_soc is None else min_soc,
            api_remaining=self._int_state(KEY_API_REMAINING),
        )

    async def async_write(self, groups: Groups, *, enable: bool) -> None:
        """Replace the groups; ha-foxess-openapi sets the flag when asked."""
        await self._call(
            SERVICE_SET_SCHEDULE,
            {"groups": [g.to_api() for g in groups], "enable_scheduler": enable},
        )

    async def async_disable(self) -> None:
        """Switch the mode scheduler off."""
        await self._call(SERVICE_DISABLE_SCHEDULER, {})
