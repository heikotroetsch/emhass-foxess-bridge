#!/usr/bin/env python3
"""
Tier 4: one deliberate write cycle through ha-foxess-openapi on the real inverter.

Snapshot the scheduler, write a single ForceCharge probe group, poll
get_schedule until the cloud reflects it, restore the snapshot and verify.
Prints the findings: end-minute convention under V1, what the cloud returns
for maxSoc, the master flag after a groups write, and the settle time.

Safety: the probe window starts twelve hours away from now (half a day in
both directions), so it cannot become active during the cycle even if the
restore takes minutes. Needs FOX_ALLOW_WRITES=1. Uses the same services the
bridge uses.

    FOX_ALLOW_WRITES=1 .venv/bin/python scripts/write_cycle.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_contract import call, load_env

PROBE_MINUTES = 14  # written as endMinute; V1 keeps it or rewrites it
SETTLE_ATTEMPTS = 12
SETTLE_SLEEP_S = 10
KEYS = ("startHour", "startMinute", "endHour", "endMinute", "workMode", "minSocOnGrid")


def active(groups: list[dict]) -> list[dict]:
    return [g for g in groups if g.get("enable", 1) == 1]


def same(a: list[dict], b: list[dict]) -> bool:
    return sorted(tuple(g[k] for k in KEYS) for g in active(a)) == sorted(
        tuple(g[k] for k in KEYS) for g in active(b)
    )


def main() -> int:
    env = load_env()
    allow = os.environ.get("FOX_ALLOW_WRITES", env.get("FOX_ALLOW_WRITES", "0"))
    if allow != "1":
        print("set FOX_ALLOW_WRITES=1 to run the write cycle")
        return 2
    serial = env["FOX_DEVICE_SN"]
    now = datetime.now(ZoneInfo(env.get("FOX_TIME_ZONE", "Europe/Berlin")))
    probe_hour = (now.hour + 12) % 24

    def get() -> dict:
        return call(
            env,
            "/api/services/foxess_openapi/get_schedule?return_response",
            {"device_sn": serial},
        )["service_response"]

    def write(groups: list[dict], *, enable: bool) -> None:
        call(
            env,
            "/api/services/foxess_openapi/set_schedule",
            {"device_sn": serial, "groups": groups, "enable_scheduler": enable},
        )

    before = get()
    groups_before = before.get("groups") or []
    min_soc = next((g.get("minSocOnGrid") for g in groups_before), None) or 10
    probe = {
        "enable": 1,
        "startHour": probe_hour,
        "startMinute": 0,
        "endHour": probe_hour,
        "endMinute": PROBE_MINUTES,
        "workMode": "ForceCharge",
        "minSocOnGrid": min_soc,
        "fdSoc": min_soc,
        "fdPwr": 0,
        "maxSoc": 100,
    }
    findings: dict = {
        "now_local": now.isoformat(timespec="minutes"),
        "probe": probe,
        "before": {
            "enable": before.get("enable"),
            "active_groups": active(groups_before),
        },
    }
    print(f"writing probe {probe_hour:02d}:00-{probe_hour:02d}:{PROBE_MINUTES} ...")
    try:
        write([probe], enable=True)
        started = time.monotonic()
        after = get()
        for _ in range(SETTLE_ATTEMPTS):
            if same(after.get("groups", []), [probe]):
                break
            time.sleep(SETTLE_SLEEP_S)
            after = get()
        shown = active(after.get("groups", []))
        findings["probe_read_back"] = shown
        findings["probe_settle_seconds"] = round(time.monotonic() - started, 1)
        findings["end_minute_kept"] = (
            bool(shown) and shown[0].get("endMinute") == PROBE_MINUTES
        )
        findings["max_soc_returned"] = shown[0].get("maxSoc") if shown else None
        findings["flag_after_groups_write"] = after.get("enable")
        findings["group_slots_after_write"] = len(after.get("groups", []))
    except urllib.error.HTTPError as exc:
        findings["probe_error"] = (
            f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}"
        )
    finally:
        print("restoring ...")
        try:
            if groups_before:
                write(groups_before, enable=False)
                findings["restore_mode"] = "snapshot groups written back"
            else:
                write([{**probe, "enable": 0}], enable=False)
                findings["restore_mode"] = "no groups before; probe left disabled"
        except urllib.error.HTTPError as exc:
            findings["restore_error"] = (
                f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}"
            )
            write([{**probe, "enable": 0}], enable=False)
            findings["restore_mode"] = "snapshot rejected; probe left disabled"
        if before.get("enable"):
            write(groups_before, enable=True)
        else:
            call(
                env,
                "/api/services/foxess_openapi/disable_scheduler",
                {"device_sn": serial},
            )

        def restored(state: dict) -> bool:
            return same(state.get("groups", []), groups_before) and bool(
                state.get("enable")
            ) == bool(before.get("enable"))

        current = get()
        for _ in range(SETTLE_ATTEMPTS):
            if restored(current):
                break
            time.sleep(SETTLE_SLEEP_S)
            current = get()
        findings["after_restore"] = {
            "enable": current.get("enable"),
            "active_groups": active(current.get("groups", [])),
            "group_slots": len(current.get("groups", [])),
        }
        findings["restore_verified"] = restored(current)
        print(json.dumps(findings, indent=2))
    return 0 if findings.get("restore_verified") else 1


if __name__ == "__main__":
    sys.exit(main())
