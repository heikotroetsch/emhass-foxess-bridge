"""
Fixtures for the real-API tiers (contract and write cycle).

Everything here is skipped unless FOX_API_KEY and FOX_DEVICE_SN are set in the
environment or in the git-ignored .env file at the repository root. The write
cycle additionally needs FOX_ALLOW_WRITES=1.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import pytest

from custom_components.emhass_foxess_bridge.api import FoxOpenApiClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_env() -> dict[str, str]:
    """Environment variables, with .env filling in what is not set."""
    values = dict(os.environ)
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip().strip("'\""))
    return values


@pytest.fixture(scope="session")
def fox_env() -> dict[str, str]:
    env = _load_env()
    if not env.get("FOX_API_KEY") or not env.get("FOX_DEVICE_SN"):
        pytest.skip("FOX_API_KEY and FOX_DEVICE_SN are not set (see .env.example)")
    return env


@pytest.fixture
def device_sn(fox_env: dict[str, str]) -> str:
    return fox_env["FOX_DEVICE_SN"]


@pytest.fixture
def inverter_tz(fox_env: dict[str, str]) -> str:
    return fox_env.get("FOX_TIME_ZONE", "Europe/Berlin")


@pytest.fixture
async def real_client(fox_env: dict[str, str]) -> AsyncIterator[FoxOpenApiClient]:
    """Production client against the real FoxESS cloud, with real write pacing."""
    async with aiohttp.ClientSession() as session:
        yield FoxOpenApiClient(fox_env["FOX_API_KEY"], session)


@pytest.fixture
def allow_writes(fox_env: dict[str, str]) -> None:
    if fox_env.get("FOX_ALLOW_WRITES") != "1":
        pytest.skip(
            "set FOX_ALLOW_WRITES=1 to run the write cycle against the inverter"
        )
