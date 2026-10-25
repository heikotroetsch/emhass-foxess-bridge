# EMHASS FoxESS Bridge

[![Tests](https://github.com/heikotroetsch/emhass-foxess-bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/heikotroetsch/emhass-foxess-bridge/actions/workflows/tests.yml)
[![Validate](https://github.com/heikotroetsch/emhass-foxess-bridge/actions/workflows/validate.yml/badge.svg)](https://github.com/heikotroetsch/emhass-foxess-bridge/actions/workflows/validate.yml)
[![HACS custom repository](https://img.shields.io/badge/HACS-custom%20repository-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/heikotroetsch/emhass-foxess-bridge?include_prereleases)](https://github.com/heikotroetsch/emhass-foxess-bridge/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Home Assistant integration that charges a FoxESS battery from the grid in the
cheapest hours, as planned by [EMHASS](https://github.com/davidusb-geek/emhass).
It turns the EMHASS battery plan into FoxESS mode-scheduler groups and keeps
the inverter in sync with the plan, through the FoxESS cloud. No Modbus
wiring, no extra hardware.

Built for **Enpal** solar systems, which ship a FoxESS H3 hybrid inverter and
battery, together with the **Octopus Energy Germany dynamic tariff**: EMHASS
does the day-ahead optimisation inside Home Assistant, the bridge applies it
to the inverter. Any FoxESS inverter with the mode scheduler and any price
source EMHASS can read will do.

**Status: beta, in field testing** on an H3-5.0-E with a 17.3 kWh battery and
a dynamic 15-minute tariff. The code is covered by unit, component and
contract tests. Expect changes before 1.0.

Not affiliated with FoxESS, EMHASS, Octopus Energy or ha-foxess-openapi.

## Contents

- [How it works](#how-it-works)
- [What you need](#what-you-need)
- [Installation](#installation)
- [Setup, step by step](#setup-step-by-step)
- [Entities](#entities)
- [Options](#options)
- [Services](#services)
- [Dashboard card](#dashboard-card)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Development](#development)

## How it works

```
Octopus prices ──► EMHASS (optimiser) ──► sensor.p_batt_forecast ──► Bridge ──► ha-foxess-openapi ──► FoxESS cloud ──► inverter
PV + load data ─┘                         sensor.p_grid_forecast        │
                                                                       └── planned / active schedule, dry run, switches
```

1. EMHASS publishes its plan as two sensors: `sensor.p_batt_forecast`
   (attribute `battery_scheduled_power`, negative = charge) and
   `sensor.p_grid_forecast` (attribute `forecasts`, positive = import).
2. Every quarter hour, and 30 s after a plan update, the bridge takes the
   next 24 h of the plan. Steps where EMHASS charges the battery *and*
   imports from the grid become charge windows. PV surplus charging is left
   to the inverter's own self-use logic.
3. Windows are merged on the 15-minute grid, split at midnight and capped at
   the 8 groups FoxESS allows. Each window becomes a `ForceCharge` group
   (scheduler V1, `maxSoc` 100 %) that carries the inverter's own
   `minSocOnGrid`.
4. The groups are written only when they differ from what the inverter
   reports. When the plan is missing or older than the limit, the bridge
   switches the mode scheduler off once, so the inverter returns to self-use.

FoxESS groups are time-of-day ranges that repeat every day. That is why the
bridge never looks further than 24 h ahead and refreshes every 15 minutes.

## What you need

| Component | Role | Notes |
| --- | --- | --- |
| FoxESS hybrid inverter with mode scheduler | the device | Verified: H3-5.0-E, master 1.91, manager 1.81. Other models with scheduler V1 should work; please report. |
| [FoxESS Cloud Open API integration](https://github.com/duquedaniel/ha-foxess-openapi) (ha-foxess-openapi) | cloud connection, live sensors, scheduler services | HACS custom repository. Needs an API key from foxesscloud.com (User Profile > API Management). Verified with version 0.1.2. |
| [EMHASS add-on](https://github.com/davidusb-geek/emhass-add-on) | the optimiser | `optimization_time_step` 15 (or 30). Verified with EMHASS 0.18.7. |
| A price source | 15-minute or hourly prices as a list | The examples use [octopus_germany](https://github.com/thecem/octopus_germany) for Dynamic Octopus. Any sensor whose attributes hold a price list works with a small template change. |
| Home Assistant 2026.10 or newer | | |

API budget: ha-foxess-openapi polls about 480 times a day by default, the
bridge adds one read per run (100 to 200 a day) plus the writes. The limit is
1,440 calls per inverter and day. The bridge pauses when fewer than 60 calls
remain.

## Installation

### HACS (recommended)

1. HACS > three dots > *Custom repositories*. Add
   `https://github.com/duquedaniel/ha-foxess-openapi` (type *Integration*)
   if you have not already, then add
   `https://github.com/heikotroetsch/emhass-foxess-bridge` (type *Integration*).
2. Install **FoxESS Cloud Open API** and **EMHASS FoxESS Bridge**, restart
   Home Assistant.

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=heikotroetsch&repository=emhass-foxess-bridge&category=integration)

### Manual

Download `emhass_foxess_bridge.zip` from the latest
[release](https://github.com/heikotroetsch/emhass-foxess-bridge/releases),
unpack it into `config/custom_components/emhass_foxess_bridge/`, restart.

## Setup, step by step

1. **FoxESS Cloud Open API.** *Settings > Devices & services > Add
   integration > FoxESS Cloud Open API*, enter the API key, pick the
   inverter. Check that its sensors show live values and that
   `sensor.<station>_schedule` exists.
2. **EMHASS.** Install the add-on, start it, and load the parameters from
   [`examples/emhass/config.json`](examples/emhass/config.json) (web UI or
   `curl -X POST http://<ha>:5000/set-config --data-binary @config.json`).
   Adjust battery capacity, charge and discharge power, PV modules and the
   two sensor names. ha-foxess-openapi reports power in kW while EMHASS
   expects W, so add the W template sensors from
   [`examples/emhass/template_sensors.yaml`](examples/emhass/template_sensors.yaml).
3. **Prices and automations.** Add the REST commands from
   [`examples/emhass/rest_commands.yaml`](examples/emhass/rest_commands.yaml)
   and the two automations from
   [`examples/emhass/automations.yaml`](examples/emhass/automations.yaml):
   a day-ahead run after the price publication and an MPC run every 15
   minutes, each followed by `publish-data`. Details in
   [`examples/emhass/README.md`](examples/emhass/README.md).
4. **The bridge.** *Add integration > EMHASS FoxESS Bridge*. Pick the
   inverter device created by the FoxESS Cloud Open API integration and the
   two EMHASS sensors. The bridge starts with *Bridge enabled* **off** and
   *Dry run* **on**: it computes and shows, but writes nothing.
5. **Dry run for a few days.** Compare `sensor.<bridge>_planned_schedule`
   with the cheapest slots of the day. `binary_sensor.<bridge>_plan_stale`
   must be off; its `reason` attribute tells you why it is on.
6. **Go live.** Switch *Bridge enabled* on and *Dry run* off. From now on the
   bridge writes the groups and `sensor.<bridge>_active_schedule` shows what
   the inverter carries. To stop everything, call
   `emhass_foxess_bridge.clear_schedule` or switch *Bridge enabled* off.

## Entities

The bridge creates a service device "EMHASS bridge <inverter>" linked to
the inverter device of the FoxESS Cloud Open API integration.

| Entity | Meaning |
| --- | --- |
| `switch.<bridge>_bridge_enabled` | Master switch for writes. Default **off**. |
| `switch.<bridge>_dry_run` | Log and show what would be written instead of writing. Default **on**. |
| `sensor.<bridge>_planned_schedule` | Groups derived from the EMHASS plan, as text; attribute `groups` holds them in API form. |
| `sensor.<bridge>_active_schedule` | Groups and master flag (`scheduler_enabled`) as read from the inverter. |
| `sensor.<bridge>_apply_result` | Outcome of the last run: `disabled`, `dry_run`, `unchanged`, `pending`, `written`, `cleared`, `rate_limited`, `error`. Attributes: `detail`, `api_remaining`, `writes_blocked_until`. |
| `sensor.<bridge>_last_apply` | Timestamp of the last run with `result` and `detail`. |
| `binary_sensor.<bridge>_plan_stale` | On when there is no usable plan; `reason` explains. |

Live values (SoC, power, energy counters for the Energy dashboard) come from
the FoxESS Cloud Open API integration.

## Options

*Settings > Devices & services > EMHASS FoxESS Bridge > Configure*:

| Option | Default | Meaning |
| --- | --- | --- |
| Charge threshold | 100 W | Minimum planned charge power and grid import for a step to count as grid charging. |
| Minimum charge window | 30 min | Shorter windows are dropped. 15 lets single quarter hours through. |
| Maximum scheduler groups | 8 | FoxESS hard limit. With more windows, the ones with the least energy are dropped. |
| Maximum plan age | 3 h | Older plans are treated as missing and the scheduler is switched off. |

## Services

| Service | What it does |
| --- | --- |
| `emhass_foxess_bridge.apply_plan` | Run the loop now instead of waiting for the next quarter hour. |
| `emhass_foxess_bridge.clear_schedule` | Switch the inverter's mode scheduler off **and** switch the bridge off so it stays off. The emergency stop. |
| `emhass_foxess_bridge.set_schedule` | Write groups by hand (`start`, `end`, `work_mode`, `min_soc_on_grid`, `fd_soc`, `fd_pwr`, `max_soc`), for experiments. Refused while the bridge is live. |

All services take an optional `config_entry_id`; without it they act on every
configured inverter.

## Dashboard card

```yaml
type: entities
title: Battery charging plan
entities:
  - entity: switch.emhass_bridge_foxess_station_bridge_enabled
  - entity: switch.emhass_bridge_foxess_station_dry_run
  - entity: sensor.emhass_bridge_foxess_station_planned_schedule
  - entity: sensor.emhass_bridge_foxess_station_active_schedule
  - entity: sensor.emhass_bridge_foxess_station_apply_result
  - entity: binary_sensor.emhass_bridge_foxess_station_plan_stale
  - entity: sensor.foxess_station_battery_soc
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Config flow aborts with *Install and set up the FoxESS Cloud Open API integration first* | ha-foxess-openapi is not loaded. Install it, set it up for the inverter, then add the bridge. |
| `plan_stale` on, reason *sensor.p_batt_forecast is unavailable* | EMHASS has not published yet. Check the EMHASS add-on log and run `rest_command.emhass_publish_data`. |
| `plan_stale` on, reason *older than 3:00:00* | EMHASS stopped publishing. Check the automations and the add-on. |
| EMHASS log shows `freq ... NaTType` or an empty dataframe | Not enough sensor history yet (`historic_days_to_retrieve`). Wait a day or two after creating the W template sensors. |
| `apply_result` is `rate_limited` | The FoxESS daily quota is used up or below the reserve. Writes resume after midnight. Lengthen the polling intervals of ha-foxess-openapi if this repeats. |
| `apply_result` is `error` | `detail` carries the message ha-foxess-openapi returned. Check its log and the FoxESS cloud status. |
| `apply_result` is `pending` | A write happened within the last 60 s and the cloud has not caught up. Normal. |
| Groups look right but the inverter does not charge | Check `scheduler_enabled` on the active schedule sensor. The FoxESS app or another tool may have switched the scheduler off. |

Debug logging: three dots on the integration > *Enable debug logging*.
Include the diagnostics download in bug reports; it redacts serials and keys.

## FAQ

**Why go through the cloud instead of Modbus?** The H3's LAN port speaks no
Modbus; RS485 wiring would be needed. For multi-hour charging windows the
cloud round trip of a few seconds does not matter, and every FoxESS owner
already has the cloud.

**Does the bridge ever discharge the battery into the grid?** No. Version 0.x
writes only `ForceCharge` groups. Everything else stays in the inverter's
self-use mode. `ForceDischarge` and a `Backup` hold mode are on the roadmap.

**What does `maxSoc` do?** FoxESS stops the forced charge at that SoC. The
bridge writes 100 % for now; a per-window target from the EMHASS SoC plan is
on the roadmap.

**Several inverters?** One bridge entry per inverter device.

**Can I use another price source than Octopus?** Yes. The REST commands only
need a list of prices per step; adapt the Jinja in
`examples/emhass/rest_commands.yaml`.

## Development

```bash
scripts/setup      # uv venv (Python 3.14) + Home Assistant + test tooling
scripts/test       # pytest: unit, component and contract tiers
scripts/lint       # ruff format + check
scripts/develop    # local Home Assistant on http://localhost:8123
scripts/deploy     # rsync to a HA OS host and restart, see .env.example
scripts/release    # bump version, roll the changelog, tag
```

Architecture and design decisions: [`docs/architecture.md`](docs/architecture.md).
Test tiers and scripts: [`docs/testing.md`](docs/testing.md).
Measured FoxESS, EMHASS and Octopus behaviour: [`docs/foxess-scheduler-notes.md`](docs/foxess-scheduler-notes.md).
Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md). Changes: [`CHANGELOG.md`](CHANGELOG.md).

## License

MIT, see [`LICENSE`](LICENSE). The repository scaffold derives from
[ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint),
Copyright (c) 2019-2025 Joakim Sørensen, MIT License. Thanks also to
[duquedaniel/ha-foxess-openapi](https://github.com/duquedaniel/ha-foxess-openapi)
for the FoxESS connection and to the [EMHASS](https://github.com/davidusb-geek/emhass) project.
