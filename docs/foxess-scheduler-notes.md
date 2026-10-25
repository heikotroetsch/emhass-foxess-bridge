# FoxESS scheduler, EMHASS and Octopus: field notes

Measured on an H3-5.0-E (master 1.91, slave 1.03, manager 1.81, L3
datalogger, 6 × ECS2900-2 = 17.3 kWh) through ha-foxess-openapi 0.1.2 and
the FoxESS Cloud Open API, scheduler V1.

## Scheduler V1

- Endpoints: `/op/v1/device/scheduler/get/flag`, `/set/flag`, `/get`,
  `/enable`. The group fields are `enable`, `startHour`, `startMinute`,
  `endHour`, `endMinute`, `workMode` (`SelfUse`, `Feedin`, `Backup`,
  `ForceCharge`, `ForceDischarge`), `minSocOnGrid`, `fdSoc`, `fdPwr` and
  `maxSoc`. V2 and V3 nest the extra parameters and add import, export and
  PV limits; V3 drops the per-group `enable`.
- `get` returns `enable` (the master flag) and always eight group slots.
  Unused slots are disabled `SelfUse 00:00-00:00` groups with `maxSoc 100`.
- **End minute is inclusive.** A group written as `10:00-10:14` is returned
  as `endMinute 14`. The FoxESS app writes `04:59` for a window ending at
  05:00.
- **`maxSoc` round-trips** (written 100, read 100).
- **Settle times.** After `set_schedule` the groups are visible in `get`
  after about 1 s, the master flag after about 11 s. A read in between
  shows the new groups with `enable 0`.
- `set_schedule` of ha-foxess-openapi writes the groups and then sets the
  flag through `set/flag` when `enable_scheduler` is true;
  `disable_scheduler` only clears the flag and leaves the groups in place.
- Overlapping enabled groups are rejected; groups that merely share a
  minute overlap. `fdSoc` must not be below `minSocOnGrid`.
- Open: behaviour outside any group while the flag is on (the inverter's
  base work mode is assumed), and whether a `ForceCharge` group re-triggers
  on consecutive days on this firmware. Community reports mention single
  firing on some H3 firmware; the quarter-hour rewrite is the mitigation.

## Open API

- 1,440 requests per inverter and day; the quota resets at midnight. The
  remaining count is readable (`/op/v0/user/getAccessCount`, exposed by
  ha-foxess-openapi as a sensor).
- Error codes seen: HTTP 401 for an invalid key or signature, errno 41930
  for an unknown serial, errno 40400 when the daily limit is exceeded.
- Pacing: one query per second, one write every two seconds per endpoint.

## EMHASS

- `publish-data` writes `battery_scheduled_power` on `sensor.p_batt_forecast`
  and `forecasts` on `sensor.p_grid_forecast`; entries are
  `{"date": "2026-01-15 17:00:00+01:00", "<entity object id>": "-5000.0"}`,
  from the current step onwards.
- EMHASS reads raw sensor states and assumes W; FoxESS power sensors are kW.
- EMHASS needs `historic_days_to_retrieve` days of history for the load
  sensor. Right after a sensor is created every run fails with a pandas
  `freq ... NaTType` error until enough samples exist.
- Verified with EMHASS 0.18.7, `optimization_time_step 15`, web API on port
  5000 (`/get-config`, `/set-config`, `/action/<name>`).

## Octopus Energy Germany

- `octopus_germany` exposes `sensor.octopus_<account>_electricity_price`.
  On dynamic tariffs its `rates` attribute is a sorted list of
  `{start, end, value_inc_vat}` entries in EUR/kWh, 15 minutes each, today
  and (from about 17:00) tomorrow; `unit_rate_forecast` carries the raw API
  data.
- Before the smart meter is commissioned Octopus assigns an interim tariff of
  type `Simple` (fixed price). The sensor then has no `rates` attribute. The
  example REST commands fall back to a flat price list in that case so the
  pipeline runs end to end.
