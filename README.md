# EMHASS FoxESS Bridge

Home Assistant custom integration that applies the battery plan computed by
[EMHASS](https://github.com/davidusb-geek/emhass) to a FoxESS hybrid inverter
through the FoxESS Cloud Open API mode scheduler. No Modbus wiring required.

**Status: work in progress.** The API client, config flow and live sensors
exist and are tested against recorded responses from an H3-5.0-E. The planner
that turns `sensor.p_batt_forecast` into scheduler groups is the next step.

Not affiliated with FoxESS or EMHASS.

## How it will work

1. EMHASS publishes `sensor.p_batt_forecast` with the attribute
   `battery_scheduled_power` (negative = charge, positive = discharge).
2. The bridge merges consecutive charge steps into windows on the 15-minute
   grid, splits at midnight, caps at the 8 groups FoxESS allows and keeps a
   SelfUse base group.
3. Windows are written as `ForceCharge` scheduler groups, only when the plan
   actually changed, respecting the API write pacing.
4. A dry-run mode logs what would be written. If EMHASS stops delivering a
   plan, the inverter falls back to SelfUse.

## Requirements

- FoxESS inverter with mode scheduler support (`function.scheduler` in the
  Open API device detail). Verified: H3-5.0-E, master 1.91, manager 1.81.
- FoxESS Cloud API key: User Profile > API Management on foxesscloud.com.
  The API allows 1,440 calls per inverter per day.
- Home Assistant 2026.10 or newer.

## Development

```bash
scripts/setup      # uv venv (Python 3.14) + Home Assistant + test tooling
scripts/test       # pytest
scripts/lint       # ruff format + check
scripts/develop    # local Home Assistant on http://localhost:8123
scripts/deploy     # rsync to a HA OS host, see .env.example
```

Recorded API responses live in `tests/fixtures/` (serial numbers anonymised).
To capture new ones, run the Open API calls from a host that has the key and
store the raw JSON there.

## License

MIT, see `LICENSE`.
