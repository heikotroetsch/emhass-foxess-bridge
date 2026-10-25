"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Return a loader for the recorded API responses and EMHASS plans."""

    def _load(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text())

    return _load
