# EMHASS setup for the bridge

Files in this folder are a starting point for the field test: a 17.3 kWh
FoxESS battery behind a 5 kW H3 inverter on a dynamic 15-minute tariff
(Dynamic Octopus via the `octopus_germany` HACS integration). Adapt the
entity ids and the battery figures to your installation.

| File | Purpose |
| --- | --- |
| `config.json` | EMHASS parameters (add-on *Configuration* page or `config.json`). |
| `rest_commands.yaml` | REST commands that call the EMHASS add-on with prices from the Octopus sensor. |
| `automations.yaml` | Day-ahead run after the 17:00 price publication, MPC every 15 minutes, publish. |

## Flow

1. `octopus_germany` exposes `sensor.octopus_<account>_electricity_price`.
   For dynamic tariffs its `rates` attribute is a sorted list of
   `{start, end, value_inc_vat}` entries in EUR/kWh, one per 15 minutes,
   today and (after about 17:00) tomorrow.
2. The REST commands turn that list into `load_cost_forecast` for EMHASS and
   pass the live battery SoC from the bridge as `soc_init`.
3. EMHASS publishes `sensor.p_batt_forecast` and `sensor.p_grid_forecast`.
4. The bridge turns the next 24 h of those two sensors into FoxESS
   `ForceCharge` groups.

## Notes

- `optimization_time_step` must match the tariff resolution: 15.
- The bridge's live sensors are in W, which is what EMHASS expects.
- `set_nodischarge_to_grid: true` keeps EMHASS from planning export. The
  bridge does not implement `ForceDischarge` yet, so a plan with export would
  be ignored anyway.
- `battery_target_state_of_charge` is the SoC EMHASS aims for at the end of
  the horizon. 0.3 is a compromise: enough for the morning, no pointless
  charging at the end of the horizon.
- Parameter names follow EMHASS 0.13. Compare with the `config_defaults.json`
  of your EMHASS version before copying.
- Keep the bridge in dry run for a few days and compare
  `sensor.<inverter>_planned_schedule` with the cheapest slots of the day.
