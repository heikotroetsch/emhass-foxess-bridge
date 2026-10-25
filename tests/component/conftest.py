"""Fixtures for the component tier: tests that run inside the HA test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from .fake_fox import FakeFoxCloud

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the Home Assistant test harness load custom_components."""


@pytest.fixture(autouse=True)
def _no_write_pacing() -> Generator[None]:
    """
    Disable the 2 s pause between API writes.

    The freezer fixture freezes the event loop clock, so a real asyncio.sleep
    would never return and back-to-back writes would hang the test.
    """
    with patch(
        "custom_components.emhass_foxess_bridge.api.MIN_SECONDS_BETWEEN_WRITES", 0
    ):
        yield


@pytest.fixture
def fox(aioclient_mock: AiohttpClientMocker, load_fixture: Callable) -> FakeFoxCloud:
    """Fake FoxESS cloud answering every endpoint the bridge calls."""
    return FakeFoxCloud(aioclient_mock, load_fixture)
