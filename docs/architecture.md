# Architecture

## Purpose

Built for Enpal solar systems, which ship a FoxESS H3 hybrid inverter with a
FoxESS battery, combined with the Octopus Energy Germany dynamic tariff:
[EMHASS](https://github.com/davidusb-geek/emhass) computes a day-ahead plan
(and a rolling re-plan every quarter hour) inside Home Assistant, and this
integration applies the grid-charging part of that plan to the inverter's
mode scheduler. It works with any FoxESS inverter that exposes the scheduler
through the FoxESS Cloud Open API and with any price source EMHASS can read.

## Components

```
price sensor ──► EMHASS add-on ──► sensor.p_batt_forecast ──┐
PV + load   ──┘   (optimiser)      sensor.p_grid_forecast   │
                                                            ▼
                                   emhass_foxess_bridge  (this integration)
                                   planner.py  schedule.py  schedule_coordinator.py  backend.py
                                                            │ services get_schedule / set_schedule / disable_scheduler
                                                            ▼
                                   foxess_openapi (ha-foxess-openapi) ──► FoxESS cloud ──► inverter
```

| Module | Role |
| --- | --- |
| `planner.py` | Pure Python. Parses the EMHASS lists, selects the rolling horizon, finds charge windows, builds groups. |
| `schedule.py` | Pure Python. Model of a FoxESS scheduler group (V1 fields), validation, order-insensitive comparison. |
| `schedule_coordinator.py` | The apply loop: triggers, gating, diff, write, settle window, stale fallback, quota pause. |
| `backend.py` | `FoxBackend` protocol and its ha-foxess-openapi implementation over services and entities. |
| `config_flow.py` | Device picker (filtered to `foxess_openapi`) plus the two EMHASS sensors; options flow for the tunables. |
| `sensor.py`, `binary_sensor.py`, `switch.py` | Planned and active schedule, apply result, last apply, plan stale, the two gating switches. |
| `services.py` | `apply_plan`, `clear_schedule`, `set_schedule`. |
| `diagnostics.py` | Redacted download for issue reports. |

## Planner

EMHASS publishes `battery_scheduled_power` (list of `{date, p_batt_forecast}`,
positive = discharge) on `sensor.p_batt_forecast` and `forecasts` (list of
`{date, p_grid_forecast}`, positive = import) on `sensor.p_grid_forecast`.
Dates are `str(pandas.Timestamp)` and values are strings; the parser accepts
numbers too and any value key (EMHASS prefixes it when `publish_prefix` is
set).

1. **Horizon.** FoxESS groups are time-of-day ranges that repeat daily, so
   only steps whose end lies in `(now, now + 24 h]` are used. The running
   step is kept so the current window is not cut short; no time of day
   occurs twice.
2. **Grid charging.** A step counts when `p_batt <= -charge_threshold_w`
   *and* `p_grid >= grid_import_threshold_w`. Without the second condition,
   PV surplus charging at noon would be turned into forced charging from
   the grid. If no grid forecast is configured, `p_batt` alone decides.
3. **Windows.** Adjacent charge steps merge, windows snap outward to the
   15-minute grid, windows shorter than `min_window_minutes` are dropped,
   windows crossing midnight become two groups.
4. **Cap.** FoxESS accepts eight groups. When more are needed, the windows
   with the least planned energy are dropped first.
5. **Groups.** `ForceCharge` with the inverter's current `minSocOnGrid`,
   `fdSoc = minSocOnGrid`, `fdPwr 0`, `maxSoc` from the settings (100 %).
   End minute inclusive (`04:59` for a window ending at 05:00).
6. **Comparison.** Two schedules are equal when their enabled groups match
   on start, end, work mode and `minSocOnGrid` (plus `maxSoc` for
   `ForceCharge`, `fdSoc`/`fdPwr` for `ForceDischarge`), regardless of
   order. Fields the cloud rewrites for other modes are ignored, so an
   unchanged plan never causes a write.

## Apply loop

- **Triggers.** A timer at minute 0, 15, 30, 45 (second 20) and, debounced
  by 30 s, every state change of the two EMHASS sensors.
- **Gating.** Nothing is written unless `Bridge enabled` is on and
  `Dry run` is off. Both are config entry options, so toggling them needs
  no reload and survives restarts.
- **Run.** Read the plan from the entities, read the inverter through the
  backend, compare the desired groups with the effective ones (the current
  groups if the master flag is on, otherwise none), write only on change.
- **Settle window.** For 60 s after a write, a read that disagrees with
  what was written is treated as cloud lag (`pending`), not as a reason to
  write again. Measured on an H3: groups appear after about 1 s, the master
  flag after about 11 s.
- **Stale plan.** When the plan entity is missing, unavailable, unreadable
  or older than `plan_max_age_hours`, the desired schedule is empty: the
  loop calls `disable_scheduler` once and the inverter runs its normal mode.
  This also happens after a Home Assistant restart until EMHASS publishes
  again, which costs at most one 15-minute gap.
- **Quota.** The FoxESS Open API allows 1,440 calls per inverter and day.
  An error text containing `40400` or `limit exceeded`, or fewer than 60
  remaining calls on the ha-foxess-openapi quota sensor, pauses all backend
  calls until local midnight and raises a repair issue.
- **Results.** `disabled`, `dry_run`, `unchanged`, `pending`, `written`,
  `cleared`, `rate_limited`, `error`, exposed as an enum sensor.

## Backend contract (ha-foxess-openapi)

The bridge holds no API key. It drives the
[FoxESS Cloud Open API integration](https://github.com/duquedaniel/ha-foxess-openapi)
(domain `foxess_openapi`):

| Need | Call |
| --- | --- |
| Current groups and master flag | `foxess_openapi.get_schedule` (`return_response`): raw `/op/v1/device/scheduler/get` result with `enable` and `groups` |
| Write groups and switch the scheduler on | `foxess_openapi.set_schedule` with `groups` (V1 fields incl. `maxSoc`) and `enable_scheduler: true` |
| Switch the scheduler off | `foxess_openapi.disable_scheduler` |
| `minSocOnGrid`, remaining quota, flag fallback | entities with unique ids `{sn}_minSocOnGrid`, `{sn}_api_remaining`, `{sn}_scheduler_enable`, `{sn}_schedule` on the device with identifier `(foxess_openapi, sn)` |

Every call carries `device_sn`. Errors arrive as `HomeAssistantError` text
and are translated to `FoxBackendError` or `FoxBackendRateLimitError`.
Setup raises `ConfigEntryNotReady` until the services are registered, so
start-up order does not matter.

The bridge's entities live on their own service device "EMHASS bridge
<inverter>" linked to the inverter with `via_device_id`. Since Home
Assistant 2026.10 device identifiers are unique per config entry and child
devices must belong to the same entry, so joining the ha-foxess-openapi
device is not possible.

## Design decisions

- **Grid import as the second criterion** for charge windows, see Planner.
- **"Clear" means master flag off**, not an empty group list. Whether the
  API accepts an empty list is unknown; the flag is documented and tested.
- **`ForceCharge` only.** Everything else stays in the inverter's self-use
  logic. Holding the battery (`Backup`) and planned export
  (`ForceDischarge`) are roadmap items.
- **No own API client.** An earlier version talked to the FoxESS cloud
  directly (git tag `direct-api-backend`). ha-foxess-openapi maintains the
  client, the signature scheme and the 34 sensors; one API consumer per key
  keeps the quota predictable. The price: error details arrive as text.
- **Power in watts for EMHASS.** ha-foxess-openapi reports kW and EMHASS
  reads raw states, so `examples/emhass/template_sensors.yaml` provides W
  sensors.
- **Safe defaults.** Bridge off, dry run on, writes only on change, pause
  before the quota is exhausted, emergency stop through `clear_schedule`.

## Roadmap

- `maxSoc` per window from `sensor.soc_batt_forecast`, so partial charging
  follows the plan instead of charging to 100 %.
- Hold mode: steps with `p_batt ~ 0` and grid import mapped to `Backup`.
- `ForceDischarge` for planned export, off by default.
- Alternative backends behind `FoxBackend`: direct Open API client, local
  Modbus.
