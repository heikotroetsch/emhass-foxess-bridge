# Development plan

Test-driven, inside out: pure planning logic first, Home Assistant wiring
second, real FoxESS API last. Every milestone ends with a green test run and
one commit. Target: Home Assistant 2026.10.4 only.

## Test pyramid

| Tier | What | How it runs | Speed |
| --- | --- | --- | --- |
| 1 Unit | `planner.py`, `schedule.py` (no HA imports) | `pytest tests/unit` | ms |
| 2 Component | API client, coordinator, entities, services, flows | HA test harness (`hass`, `aioclient_mock`, `freezer`) | < 1 s each |
| 3 Contract | Real FoxESS Open API, read-only | `pytest -m contract`, needs `.env` | seconds, manual |
| 4 Write cycle | Real API, write a schedule and restore | `pytest -m writes` with `FOX_ALLOW_WRITES=1` | manual, deliberate |
| 5 Field | Parents' HA with EMHASS, dry-run then live | observation over days | weeks |

CI runs tiers 1 and 2. Tiers 3 and 4 are skipped unless the environment
variables are set.

## TDD rules for this repo

1. Write the failing test first, name it after the behaviour, not the method.
2. Smallest change to green, then refactor with the suite green.
3. Time is injected (`now` parameter or `freezer`), never read inside logic.
4. Every real API response we rely on is recorded as a fixture and the
   contract tests assert the fixture still matches reality.
5. One commit per green step: `test: ...` for a red test, `feat: ...` once
   green, `refactor: ...` for cleanups.

## Milestone 1: planner core (pure Python)

Module `planner.py` turns the EMHASS plan into charge windows; `schedule.py`
models FoxESS scheduler groups and validates them.

Behaviours, in test order:

- Parse `battery_scheduled_power` (list of `{date, p_batt_forecast}`) into
  sorted, timezone-aware steps; reject unsorted or non-uniform steps.
- Rolling 24 h horizon: FoxESS groups are time-of-day based and repeat
  daily, so only steps in `[now, now + 24 h)` are considered. Each
  time-of-day appears at most once. Steps before `now` are dropped.
- Classify: `p_batt <= -charge_threshold_w` is a charge step. Everything
  else is self-use in v1.
- Merge consecutive charge steps into windows; snap to the 15-minute grid.
- Drop windows shorter than `min_window_minutes`.
- Split windows that cross midnight into two groups.
- Cap at `max_groups` (8). When over the cap, keep the windows with the
  most planned energy and log what was dropped.
- Build groups: `ForceCharge`, `minSocOnGrid` from the inverter setting,
  end minute inclusive (`02:00-03:59` style, to be confirmed in tier 4).
- Validate groups: no overlap, within one day, on the grid.
- Diff: `desired == current` is order-insensitive and ignores fields FoxESS
  rewrites, so unchanged plans produce no write.

Fixture: `tests/fixtures/emhass_plan_winter_day.json`, a realistic 24 h plan
with a 02:00-05:00 charge block and a short 13:00 block.

Deliverable: `tests/unit/test_planner.py`, `tests/unit/test_schedule.py`,
no Home Assistant imports in either module.

## Milestone 2: apply loop inside Home Assistant

- Coordinator subscribes to the EMHASS entity and to a 15-minute aligned
  timer. Either event recomputes the plan for the rolling horizon.
- `switch.bridge_enabled` (default off) and `switch.dry_run` (default on).
  Nothing is written until both are set deliberately.
- Idempotent apply: read current groups, diff, write only on change. After a
  write, reads that disagree within 60 s are treated as stale, not as truth
  (observed eventual consistency on FoxESS).
- Stale plan fallback: if the EMHASS entity is unavailable or older than
  `plan_max_age_hours`, clear the groups once so the inverter returns to
  self-use, and raise `binary_sensor.plan_stale`.
- Sensors: planned schedule (attributes carry the groups), active schedule
  as read from the API, last apply time and result.
- Rate-limit handling: on errno 40400 stop writing for the day and surface a
  repair issue.

Tests use `hass.states.async_set(...)` to publish fake EMHASS plans, the
`freezer` fixture to move time, and `aioclient_mock` to assert the exact
JSON written to `/op/v0/device/scheduler/enable`.

## Milestone 3: configuration and operations

- Options flow: `charge_threshold_w`, `min_window_minutes`, `max_groups`,
  `plan_max_age_hours`.
- Services: `apply_plan` (force a run), `clear_schedule`, `set_schedule`
  (raw groups, for experiments).
- Diagnostics platform: redacted config, last plan, last groups written,
  last API error.
- Translations for every new string; `hassfest` and HACS validation green.

## Milestone 4: real API tests

`tests/integration/`, skipped unless `FOX_API_KEY` and `FOX_DEVICE_SN` are
set (loaded from `.env`).

Contract (read-only):

- Device detail has the keys the integration reads and
  `function.scheduler` is true.
- Real-time query returns every variable in `REALTIME_VARIABLES`.
- Scheduler flag and groups parse with the production client.
- Access count is readable; the test prints remaining quota.

Write cycle (`FOX_ALLOW_WRITES=1`):

- Snapshot current groups and master flag.
- Write a single `ForceCharge` group lying entirely in the past of the
  current day (for example `00:00-00:14`), so it cannot trigger before the
  restore.
- Read back and settle the open questions below.
- Restore the snapshot and verify the restore.

Open questions this tier must answer:

1. End minute convention: is `endMinute: 59` inclusive, as the FoxESS app
   writes it?
2. Behaviour outside any group while the scheduler is enabled: does the
   inverter fall back to its base work mode, or is a full-day SelfUse base
   group required?
3. Does writing groups with `/scheduler/enable` switch the master flag on,
   or is a separate `/scheduler/set` call needed?
4. Does a ForceCharge group re-trigger on consecutive days on master 1.91 /
   manager 1.81? Community reports say it fired once only on some H3
   firmware; the rolling rewrite every 15 minutes is the mitigation.

## Milestone 5: field test at the parents' installation

Prerequisites outside this repo: `octopus_germany` for prices, EMHASS
add-on with a 15-minute step, 17.3 kWh battery, 5 kW inverter limits,
Open-Meteo PV forecast, and the two automations (`dayahead-optim` after the
17:00 price publication, `naive-mpc-optim` every 15 minutes, `publish-data`).
Example files live in `examples/emhass/`.

1. Deploy with `scripts/deploy`, bridge enabled, dry-run on. Three to seven
   days of shadow operation: compare the planned groups with what we would
   have wanted by hand.
2. Dry-run off. Acceptance over seven consecutive days: inverter groups
   equal the planned groups within two minutes of every plan change, no
   quota errors, SoC rises during every charge window.
3. Energy dashboard rewired to the bridge sensors.

## Milestone 6: release 0.1.0

README with screenshots and the EMHASS example config, CHANGELOG, GitHub
release tag, repository public, post in the EMHASS community thread.

## Later

- Hold mode: map steps with `p_batt ~ 0` and grid import to `Backup`
  (keep the battery for expensive hours).
- `ForceDischarge` for planned export, off by default.
- Second backend behind the same planner (local Modbus).
