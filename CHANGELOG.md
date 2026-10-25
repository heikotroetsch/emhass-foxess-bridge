# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0-beta.2] - 2026-10-25

### Changed

- Pushing a `v*` tag now creates the GitHub release with the HACS zip; no
  manual release step. `scripts/release X.Y.Z --push` does the whole cut.
- Documentation reorganised into architecture, testing and field notes.
- Purpose stated: Enpal/FoxESS systems on the Octopus Energy dynamic
  tariff with EMHASS day-ahead optimisation.
- Canonical MIT license text so GitHub and HACS identify the license.

## [0.1.0-beta.1] - 2026-10-25

### Added

- Planner that turns the EMHASS battery plan (`sensor.p_batt_forecast` with
  `sensor.p_grid_forecast`) into FoxESS `ForceCharge` scheduler groups on
  the 15-minute grid: rolling 24 h horizon, midnight split, cap at eight
  groups, scheduler V1 with `maxSoc`.
- Apply loop every quarter hour and on plan changes; writes only when the
  inverter's groups differ from the plan.
- Switches `Bridge enabled` (default off) and `Dry run` (default on).
- Sensors for planned and active schedule, apply result, last apply, and a
  `Plan stale` problem indicator.
- Options for charge threshold, minimum window, group cap and plan age.
- Services `apply_plan`, `clear_schedule` and `set_schedule`.
- Diagnostics download with serials redacted.
- Backend through the FoxESS Cloud Open API integration (ha-foxess-openapi):
  the bridge holds no API key and respects a quota reserve of 60 calls.
- Example EMHASS configuration, REST commands, automations and W template
  sensors in `examples/emhass/`.
