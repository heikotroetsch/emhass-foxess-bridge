"""Fixtures for the component tier: tests that run inside the HA test harness."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .fake_foxess_openapi import FakeFoxessOpenApi

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the Home Assistant test harness load custom_components."""


@pytest.fixture
async def fox(hass: HomeAssistant) -> FakeFoxessOpenApi:
    """A loaded fake ha-foxess-openapi with device, entities and services."""
    fake = FakeFoxessOpenApi(hass)
    await fake.async_setup()
    return fake
