"""
Tier 3: read-only contract tests against the real FoxESS Open API.

They assert two things: the production client still understands the live
responses, and the recorded fixtures in tests/fixtures still have the shape
of reality. Costs about six of the 1440 daily requests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from custom_components.emhass_foxess_bridge.const import REALTIME_VARIABLES
from custom_components.emhass_foxess_bridge.schedule import (
    MAX_SOC,
    MIN_SOC,
    SchedulerGroup,
    validate_groups,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from custom_components.emhass_foxess_bridge.api import FoxOpenApiClient

pytestmark = pytest.mark.contract


async def test_device_detail_has_what_the_integration_reads(
    real_client: FoxOpenApiClient, device_sn: str, load_fixture: Callable
) -> None:
    detail = await real_client.async_get_device_detail(device_sn)
    recorded = load_fixture("device_detail.json")["result"]
    missing = set(recorded) - set(detail)
    assert not missing, f"live device detail lost keys: {missing}"
    assert detail["function"]["scheduler"] is True
    assert detail["hasBattery"] is True
    print(
        f"\n{detail['deviceType']} master {detail['masterVersion']} "
        f"manager {detail['managerVersion']} scheduler={detail['function']}"
    )


async def test_real_time_query_returns_every_variable(
    real_client: FoxOpenApiClient, device_sn: str
) -> None:
    values = await real_client.async_get_real_time(device_sn, REALTIME_VARIABLES)
    missing = [name for name in REALTIME_VARIABLES if name not in values]
    assert not missing, f"real-time query did not return {missing}"
    assert 0 <= float(values["SoC"]) <= 100
    print(f"\nSoC {values['SoC']} % at {values.get('time')}")


async def test_scheduler_flag_and_groups_parse_with_the_production_client(
    real_client: FoxOpenApiClient, device_sn: str, load_fixture: Callable
) -> None:
    flag = await real_client.async_get_scheduler_flag(device_sn)
    recorded = load_fixture("scheduler_flag.json")["result"]
    assert set(recorded) <= set(flag)
    assert isinstance(flag["enable"], bool)
    assert flag["support"] is True

    raw_groups = await real_client.async_get_scheduler(device_sn)
    groups = [SchedulerGroup.from_api(raw) for raw in raw_groups]
    validate_groups(groups)
    print(f"\nscheduler enabled={flag['enable']} groups={[str(g) for g in groups]}")


async def test_battery_soc_setting_is_readable(
    real_client: FoxOpenApiClient, device_sn: str, load_fixture: Callable
) -> None:
    soc = await real_client.async_get_battery_soc(device_sn)
    recorded = load_fixture("soc_get.json")["result"]
    assert set(recorded) <= set(soc)
    assert MIN_SOC <= int(soc["minSocOnGrid"]) <= MAX_SOC
    print(f"\nminSoc={soc['minSoc']} minSocOnGrid={soc['minSocOnGrid']}")


async def test_access_count_shows_the_remaining_quota(
    real_client: FoxOpenApiClient, load_fixture: Callable
) -> None:
    count = await real_client.async_get_access_count()
    recorded = load_fixture("access_count.json")["result"]
    assert set(recorded) <= set(count)
    print(f"\nFoxESS quota: {count['remaining']} of {count['total']} requests left")
