# Development plan

Test-driven, inside out: pure planning logic first, Home Assistant wiring
second, the real system last. Every milestone ends with a green test run and
one commit. Target: Home Assistant 2026.10.4 only.

## Decision log

- **2026-10-25, afternoon.** Own FoxESS Open API client inside the bridge,
  scheduler endpoints v0. Implemented through milestone 3, tagged
  `direct-api-backend`.
- **2026-10-25, evening.** Switch the backend to the HACS integration
  [duquedaniel/ha-foxess-openapi](https://github.com/duquedaniel/ha-foxess-openapi)
  (domain `foxess_openapi`) and to scheduler **V1** with `maxSoc`. Reasons:
  maintained client with signature auto-detection, 34 sensors including the
  kWh counters for the Energy dashboard, one API consumer per key. Costs:
  errors arrive as `HomeAssistantError` text, dependency on a young project
  (v0.1.2, one maintainer). The bridge keeps everything that makes it a
  control loop: planner, diff, dry run, settle window, stale fallback, quota
  pause.

## Architecture after the switch

- The bridge holds no API key. It needs `foxess_openapi` installed and
  configured for the same inverter.
- Backend calls, all with an explicit `device_sn`:
  - `foxess_openapi.get_schedule` (`return_response`): raw result of
    `/op/v1/device/scheduler/get`, keys `groups` and `maxGroupCount`. The
    master flag is not in this response.
  - `foxess_openapi.set_schedule` with `groups` (V1 fields `startHour`,
    `startMinute`, `endHour`, `endMinute`, `workMode`, `minSocOnGrid`,
    `fdSoc`, `fdPwr`, optional `maxSoc`, `enable` default 1) and
    `enable_scheduler` (default true, sets the master flag afterwards).
    Their validation rejects overlaps and `fdSoc < minSocOnGrid`.
  - `foxess_openapi.disable_scheduler`: master flag off, groups untouched.
- State read from their entities, found through the entity registry by unique
  id `{sn}_<key>` on the device with identifiers `(foxess_openapi, sn)`:
  schedule sensor (attributes `master_switch`, `groups`; polled every 30 min
  and refreshed after every write), number `{sn}_minSocOnGrid`, sensor
  `{sn}_api_remaining` (remaining daily quota).
- Bridge entities live on their own service device "EMHASS bridge
  <inverter>" linked to the inverter with `via_device_id`. Since HA 2026.10
  device identifiers are unique per config entry, and child devices must
  belong to the same entry, so joining the ha-foxess-openapi device is not
  possible.
- `get_schedule` does return the master flag as `enable` (recorded on the
  H3, 2026-10-25), contrary to what their code reads; the backend uses it
  and falls back to the scheduler switch and the schedule sensor.
- Setup raises `ConfigEntryNotReady` until the `foxess_openapi` services are
  registered, so start-up order does not matter.
- Their power sensors are in kW and are not converted. EMHASS reads raw
  states in W, so `examples/emhass/` provides template sensors.

## Status (2026-10-25, night)

| Milestone | State |
| --- | --- |
| 1 Planner core | done, V1 groups with `maxSoc` |
| 2 Apply loop | done on the service backend |
| 3 Options, services, diagnostics | done on the service backend |
| 7 Backend swap | done 2026-10-25 (112 tests) |
| 4 Real-system contract | recordings and `tests/contract` done; `scripts/write_cycle.py` ready, **not yet run** (needs Heiko's go) |
| 5 Field test | bridge deployed and configured at the parents' HA in dry run (2026-10-25 23:01); EMHASS config and the dynamic tariff are open |
| 6 Release 0.1.0 | after 5 |

## Test pyramid

| Tier | What | How it runs | Speed |
| --- | --- | --- | --- |
| 1 Unit | `planner.py`, `schedule.py` (no HA imports) | `pytest tests/unit` | ms |
| 2 Component | backend, coordinator, entities, services, flows | HA test harness against a fake `foxess_openapi` (services, device, entities) | < 1 s each |
| 3 Contract | recordings from the parents' HA (entity registry, schedule sensor, `get_schedule` response) | `pytest -m contract`, fixtures recorded with `scripts/record_contract.py` | ms |
| 4 Write cycle | one probe group through `foxess_openapi.set_schedule` on the real inverter | `scripts/write_cycle.py`, HA REST API, `HA_TOKEN` | manual, deliberate |
| 5 Field | parents' HA with EMHASS, dry run then live | observation over days | weeks |

CI runs tiers 1 to 3. Tier 4 is a script, run by hand, its findings go into
this file.

## TDD rules for this repo

1. Write the failing test first, name it after the behaviour, not the method.
2. Smallest change to green, then refactor with the suite green.
3. Time is injected (`now` parameter or `freezer`), never read inside logic.
   The `freezer` fixture freezes asyncio sleeps too: no real sleeps in
   component tests.
4. Every real response we rely on is recorded as a fixture and the contract
   tests assert the fake used in tier 2 still has the shape of the recording.
5. One commit per green step: `test:` for a red test, `feat:` once green,
   `refactor:` for cleanups.

## Milestone 7: backend swap (next)

Behaviours, in test order:

1. `schedule.py`: `max_soc` (default 100) in `SchedulerGroup`, V1
   `to_api`/`from_api`, `fd_soc >= min_soc_on_grid` in `validate_groups`,
   `max_soc` part of the comparison for `ForceCharge`.
2. `backend.py`: protocol `FoxBackend` with `async_read()` returning
   scheduler flag, groups, `min_soc_on_grid` and remaining quota;
   `async_write(groups, *, enable)`; `async_disable()`.
   `OpenApiServiceBackend` implements it over the three services and the
   entity registry. Rate-limit detection: `HomeAssistantError` text
   containing `40400`, plus a reserve: no writes when `api_remaining` is
   below 60.
3. `tests/component/fake_foxess_openapi.py`: registers the three services
   with their real field names (`get_schedule` with `SupportsResponse.ONLY`),
   keeps groups and flag, counts calls, can lag (accept a write without
   changing state) and fail (`HomeAssistantError("... 40400 ...")`), creates
   the device and the schedule, min SoC and quota entities. Replaces the HTTP
   fake.
4. Config flow V2: `DeviceSelector` filtered to `foxess_openapi`, serial from
   the device identifiers as unique id, the two EMHASS entities. Errors:
   `foxess_openapi_missing`, `device_invalid`. No migration from V1 entries
   (none exist outside this repo).
5. `__init__.py`: delete `api.py`, `coordinator.py` and the live sensors,
   `ConfigEntryNotReady` while the services are missing, schedule entities on
   the shared device.
6. `schedule_coordinator.py`: uses the backend; settle window, stale
   fallback, dry run, pause until midnight stay as they are and keep their
   tests, now asserting service call data instead of HTTP bodies.
7. Services `apply_plan`, `clear_schedule`, `set_schedule` unchanged in
   behaviour; `set_schedule` accepts `max_soc`.
8. Diagnostics: resolved entity ids and the last backend read.
9. Docs: README for the new architecture, `examples/emhass/` with W template
   sensors on top of the `foxess_openapi` kW sensors, `hacs.json` and
   README name the dependency.

Deliverable: suite green, direct-API code gone, hassfest and HACS validation
green, `requirements_*` unchanged.

## Milestone 4: real-system contract

- `scripts/record_contract.py`: with `HA_URL` and `HA_TOKEN` from `.env`,
  fetch the entity registry entries of the `foxess_openapi` device, the
  schedule sensor state and a `get_schedule` response, anonymise serials,
  write `tests/fixtures/foxess_openapi/*.json`.
- `tests/contract/test_fake_matches_recording.py`: the fake's service
  responses and entity attributes have the recorded key sets.
- `scripts/write_cycle.py`: snapshot, `set_schedule` with one `ForceCharge`
  probe `00:00-00:14` and `maxSoc: 100`, poll `get_schedule` until it shows
  up, restore, verify, print findings. Skips before 02:00 local.

Open questions this tier answers:

1. End minute convention under V1: is `endMinute: 14` kept as written?
2. Does V1 return `maxSoc` for groups written without it, and with which
   value?
3. How long until `get_schedule` reflects a write (settle window)?

Question "does the groups write switch the master flag on" is answered by
their code: `set_schedule` sets it when `enable_scheduler` is true. Behaviour
outside any group and the daily re-trigger on master 1.91 remain field
observations.

## Milestone 5: field test at the parents' installation

Findings 2026-10-25: ha-foxess-openapi is installed and configured there
(34 entities, live data OK, scheduler off, eight disabled placeholder
groups with `maxSoc`). The Octopus contract is still the interim tariff
`DEU-ELECTRICITY-DYNAMIC-OCTOPUS-LIGHT-12`, type `Simple`, fixed 31.5 ct/kWh
"until customer received Smart Meter installed": the price sensor has no
`rates` attribute yet. The field test with real prices waits for Octopus to
switch the contract to the dynamic tariff. The EMHASS add-on is installed
but not configured.

Prerequisites outside this repo: `foxess_openapi` configured with the API
key, `octopus_germany` for prices, EMHASS add-on with a 15-minute step and
the `examples/emhass/` configuration (17.3 kWh, 5 kW, Open-Meteo, W template
sensors, two automations), `.env` with `HA_SSH_HOST` and `HA_TOKEN`.

1. Deploy with `scripts/deploy`, bridge enabled, dry run on. Three to seven
   days of shadow operation: compare the planned groups with the cheapest
   slots of the day.
2. Dry run off. Acceptance over seven consecutive days: inverter groups equal
   the planned groups within two minutes of every plan change, no quota
   errors, SoC rises during every charge window.
3. Energy dashboard on the `foxess_openapi` kWh counters.

## Milestone 6: release 0.1.0

README with screenshots and the EMHASS example config, CHANGELOG, GitHub
release tag, repository public, note in the ha-foxess-openapi and EMHASS
communities.

## Later

- `maxSoc` per window from `sensor.soc_batt_forecast` at the window end, so
  partial charging follows the EMHASS plan instead of charging to 100 %.
- Hold mode: map steps with `p_batt ~ 0` and grid import to `Backup`.
- `ForceDischarge` for planned export, off by default.
- Direct-API backend behind the same `FoxBackend` protocol, revived from the
  tag `direct-api-backend`, for users who do not want a second integration.
- Local Modbus backend.
