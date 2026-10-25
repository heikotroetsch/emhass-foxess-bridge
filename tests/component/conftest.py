"""Fixtures for the component tier: tests that run inside the HA test harness."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the Home Assistant test harness load custom_components."""
