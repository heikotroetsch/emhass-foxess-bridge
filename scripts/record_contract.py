#!/usr/bin/env python3
"""
Record the ha-foxess-openapi contract from a real Home Assistant.

Reads HA_URL, HA_TOKEN and FOX_DEVICE_SN from .env, fetches the states of the
inverter's entities and one get_schedule response, replaces the serial and the
station name, and writes tests/fixtures/foxess_openapi/{states,get_schedule}.json.

    .venv/bin/python scripts/record_contract.py "<station name as in entity ids>"
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "foxess_openapi"
FAKE_SN = "60SH5TEST000001"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in (ROOT / ".env").read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("'\"")
    return env


def call(env: dict[str, str], path: str, body: dict | None = None) -> object:
    request = urllib.request.Request(
        f"{env['HA_URL']}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {env['HA_TOKEN']}",
            "Content-Type": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    station = sys.argv[1].lower()
    env = load_env()
    serial = env["FOX_DEVICE_SN"]

    def anonymise(obj: object) -> object:
        text = json.dumps(obj, ensure_ascii=False).replace(serial, FAKE_SN)
        text = re.sub(re.escape(station), "test_plant", text, flags=re.IGNORECASE)
        return json.loads(text)

    states = call(env, "/api/states")
    prefix = f"foxess_{station}_"
    device_states = [
        {
            "entity_id": s["entity_id"],
            "state": s["state"],
            "attributes": s["attributes"],
        }
        for s in states
        if s["entity_id"].split(".", 1)[1].startswith(prefix)
    ]
    schedule = call(
        env,
        "/api/services/foxess_openapi/get_schedule?return_response",
        {"device_sn": serial},
    )
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "states.json").write_text(
        json.dumps(anonymise(device_states), indent=1, ensure_ascii=False) + "\n"
    )
    (FIXTURES / "get_schedule.json").write_text(
        json.dumps(anonymise(schedule), indent=1, ensure_ascii=False) + "\n"
    )
    print(f"recorded {len(device_states)} entities and one get_schedule response")
    return 0


if __name__ == "__main__":
    sys.exit(main())
