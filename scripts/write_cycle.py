#!/usr/bin/env python3
"""
Tier 4: one deliberate write cycle through ha-foxess-openapi on the real inverter.

Snapshot the scheduler, write a single ForceCharge probe group lying in the
past of the current day (00:00-00:14, maxSoc 100), poll get_schedule until the
cloud reflects it, restore the snapshot and verify. Prints the findings:
end-minute convention under V1, what the cloud returns for maxSoc, and the
settle time.

Safety: refuses before 02:00 local time, so the probe can never be active, and
needs FOX_ALLOW_WRITES=1 in .env. Uses the same services the bridge uses.

    FOX_ALLOW_WRITES=1 .venv/bin/python scripts/write_cycle.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_contract import call, load_env

PROBE = {
    "enable": 1,
    "startHour": 0,
    "startMinute": 0,
    "endHour": 0,
    "endMinute": 14,
    "workMode": "ForceCharge",
    "fdSoc": 10,
    "fdPwr": 0,
    "maxSoc": 100,
}
EARLIEST_HOUR = 2
SETTLE_ATTEMPTS = 12
SETTLE_SLEEP_S = 10


def active(groups: list[dict]) -> list[dict]:
    return [g for g in groups if g.get("enable", 1) == 1]


def same(a: list[dict], b: list[dict]) -> bool:
    keys = (
        "startHour",
        "startMinute",
        "endHour",
        "endMinute",
        "workMode",
        "minSocOnGrid",
    )
    return sorted(tuple(g[k] for k in keys) for g in active(a)) == sorted(
        tuple(g[k] for k in keys) for g in active(b)
    )


def main() -> int:
    env = load_env()
    env.setdefault("FOX_ALLOW_WRITES", os.environ.get("FOX_ALLOW_WRITES", "0"))
    if os.environ.get("FOX_ALLOW_WRITES", env["FOX_ALLOW_WRITES"]) != "1":
        print("set FOX_ALLOW_WRITES=1 to run the write cycle")
        return 2
    now = datetime.now(ZoneInfo(env.get("FOX_TIME_ZONE", "Europe/Berlin")))
    if now.hour < EARLIEST_HOUR:
        print("probe 00:00-00:14 could still be active before 02:00 local, aborting")
        return 2
    serial = env["FOX_DEVICE_SN"]

    def get() -> dict:
        return call(
            env,
            "/api/services/foxess_openapi/get_schedule?return_response",
            {"device_sn": serial},
        )["service_response"]

    before = get()
    min_soc = next((g.get("minSocOnGrid") for g in before.get("groups", [])), 10) or 10
    probe = {**PROBE, "minSocOnGrid": min_soc, "fdSoc": min_soc}
    findings: dict = {"before": before}
    try:
        call(
            env,
            "/api/services/foxess_openapi/set_schedule",
            {"device_sn": serial, "groups": [probe], "enable_scheduler": True},
        )
        started = time.monotonic()
        after = get()
        for _ in range(SETTLE_ATTEMPTS):
            if same(after.get("groups", []), [probe]):
                break
            time.sleep(SETTLE_SLEEP_S)
            after = get()
        findings["probe_read_back"] = after
        findings["probe_settle_seconds"] = round(time.monotonic() - started, 1)
        shown = active(after.get("groups", []))
        findings["end_minute_kept"] = bool(shown) and shown[0].get("endMinute") == 14
        findings["max_soc_returned"] = shown[0].get("maxSoc") if shown else None
        findings["flag_after_write"] = after.get("enable")
    finally:
        groups_before = before.get("groups") or []
        if active(groups_before):
            call(
                env,
                "/api/services/foxess_openapi/set_schedule",
                {
                    "device_sn": serial,
                    "groups": groups_before,
                    "enable_scheduler": False,
                },
            )
        else:
            disabled_probe = {**probe, "enable": 0}
            call(
                env,
                "/api/services/foxess_openapi/set_schedule",
                {
                    "device_sn": serial,
                    "groups": [disabled_probe],
                    "enable_scheduler": False,
                },
            )
        if before.get("enable"):
            call(
                env,
                "/api/services/foxess_openapi/set_schedule",
                {
                    "device_sn": serial,
                    "groups": groups_before,
                    "enable_scheduler": True,
                },
            )
        else:
            call(
                env,
                "/api/services/foxess_openapi/disable_scheduler",
                {"device_sn": serial},
            )
        restored = get()
        for _ in range(SETTLE_ATTEMPTS):
            if same(restored.get("groups", []), groups_before) and bool(
                restored.get("enable")
            ) == bool(before.get("enable")):
                break
            time.sleep(SETTLE_SLEEP_S)
            restored = get()
        findings["after_restore"] = restored
        findings["restore_verified"] = same(
            restored.get("groups", []), groups_before
        ) and bool(restored.get("enable")) == bool(before.get("enable"))
        print(json.dumps(findings, indent=2))
    return 0 if findings.get("restore_verified") else 1


if __name__ == "__main__":
    sys.exit(main())
