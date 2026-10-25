# EMHASS FoxESS Bridge

Home Assistant custom integration that applies the battery plan computed by
[EMHASS](https://github.com/davidusb-geek/emhass) to a FoxESS hybrid inverter
through the FoxESS Cloud Open API mode scheduler. No Modbus wiring required.

**Status: pre-release, in field testing.** Planner, apply loop, switches,
services and diagnostics are implemented and covered by unit and component
tests. The real-API tiers exist and are run by hand. Expect changes before
0.1.0.

Not affiliated with FoxESS or EMHASS.

## How it works

1. EMHASS publishes `sensor.p_batt_forecast` (attribute
   `battery_scheduled_power`, negative = charge) and `sensor.p_grid_forecast`
   (attribute `forecasts`, positive = import).
2. Every quarter hour, and 30 s after a plan update, the bridge takes the
   next 24 h of the plan. Steps where EMHASS charges the battery *and*
   imports from the grid become charge windows. PV surplus charging is left
   to the inverter's own self-use logic.
3. Windows are merged on the 15-minute grid, split at midnight and capped at
   the 8 groups FoxESS allows (least energy dropped first). Each window is
   written as a `ForceCharge` scheduler group with the inverter's own
   `minSocOnGrid`. The schedule is only written when it differs from what the
   inverter reports.
4. Outside the groups the inverter runs its normal work mode. When the plan
   is missing or older than the limit, the bridge disables the mode scheduler
   once so the inverter returns to self-use.

FoxESS groups are time-of-day ranges that repeat daily. That is why the
bridge never looks further than 24 h ahead and rewrites the groups every 15
minutes.

## Requirements

- FoxESS inverter with mode scheduler support (`function.scheduler` in the
  Open API device detail). Verified: H3-5.0-E, master 1.91, manager 1.81.
- FoxESS Cloud API key: User Profile > API Management on foxesscloud.com.
  The API allows 1,440 calls per inverter per day; the bridge uses roughly
  500 to 700 per day with the default settings.
- EMHASS with `optimization_time_step: 15` (30 also works), see
  `examples/emhass/`.
- Home Assistant 2026.10 or newer.

## Installation

HACS: add this repository as a custom repository of type *Integration*,
install, restart. Manual: copy `custom_components/emhass_foxess_bridge` into
your `config/custom_components/` and restart.

Then *Settings > Devices & services > Add integration > EMHASS FoxESS
Bridge*. Enter the API key, the inverter serial number and the two EMHASS
sensors.

## Entities

| Entity | Meaning |
| --- | --- |
| `switch.<inverter>_bridge_enabled` | Master switch for writes. Default **off**. |
| `switch.<inverter>_dry_run` | Log what would be written instead of writing. Default **on**. |
| `sensor.<inverter>_planned_schedule` | Groups derived from the EMHASS plan (attribute `groups`). |
| `sensor.<inverter>_active_schedule` | Groups and master flag as read from the inverter. |
| `sensor.<inverter>_apply_result` | `disabled`, `dry_run`, `unchanged`, `pending`, `written`, `cleared`, `rate_limited`, `error`. |
| `sensor.<inverter>_last_apply` | Timestamp of the last run, with `result` and `detail`. |
| `binary_sensor.<inverter>_plan_stale` | On when there is no usable plan; `reason` says why. |
| `sensor.<inverter>_battery_soc`, `..._pv_power`, `..._load_power`, `..._grid_import_power`, `..._grid_export_power`, `..._battery_charge_power`, `..._battery_discharge_power` | Live values, power in W, polled every 5 minutes. Suitable as EMHASS inputs. |

Nothing is written to the inverter until you switch *Bridge enabled* on
**and** *Dry run* off. Run a few days in dry run first and compare
`planned_schedule` with what you would have chosen by hand.

## Options

*Settings > Devices & services > EMHASS FoxESS Bridge > Configure*:

| Option | Default | Meaning |
| --- | --- | --- |
| Charge threshold | 100 W | Minimum planned charge power and grid import for a step to count as grid charging. |
| Minimum charge window | 30 min | Shorter windows are dropped. |
| Maximum scheduler groups | 8 | FoxESS hard limit. |
| Maximum plan age | 3 h | Older plans are treated as missing. |

## Services

- `emhass_foxess_bridge.apply_plan`: run the loop now.
- `emhass_foxess_bridge.clear_schedule`: disable the inverter's mode scheduler
  and switch the bridge off so it stays disabled.
- `emhass_foxess_bridge.set_schedule`: write groups by hand (experiments).
  Refused while the bridge is live, because the next run would overwrite them.

All services take an optional `config_entry_id`; without it they act on every
loaded inverter.

## Limitations

- `ForceCharge` charges at full inverter power. When EMHASS plans a partial
  power step, the inverter charges more than planned during that window.
- Only grid charging is controlled. Holding the battery for expensive hours
  (`Backup`) and planned export (`ForceDischarge`) are not implemented yet.
- Cloud control: a FoxESS Cloud outage means the schedule stays as last
  written. The quarter-hour rewrite recovers as soon as the cloud is back.
- Group times use Home Assistant's time zone; the inverter must be set to the
  same zone in FoxESS Cloud.

## Development

```bash
scripts/setup      # uv venv (Python 3.14) + Home Assistant + test tooling
scripts/test       # pytest: unit and component tiers
scripts/lint       # ruff format + check
scripts/develop    # local Home Assistant on http://localhost:8123
scripts/deploy     # rsync to a HA OS host, see .env.example
```

Test tiers, see `docs/development-plan.md`:

- `tests/unit`: planner and scheduler model, pure Python.
- `tests/component`: the integration inside the Home Assistant test harness
  against a fake FoxESS cloud.
- `tests/integration`: read-only contract tests against the real API
  (`pytest -m contract`, needs `FOX_API_KEY` and `FOX_DEVICE_SN` in `.env`)
  and one deliberate write cycle (`FOX_ALLOW_WRITES=1`).

Recorded API responses live in `tests/fixtures/` with anonymised serials.

## License

MIT, see `LICENSE`.
