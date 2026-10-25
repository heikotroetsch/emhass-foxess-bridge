# Testing

The repository is test-driven: the failing test comes first, then the
smallest change that makes it pass, then a refactor with the suite green.
Commits follow `test:` (red), `feat:` (green), `refactor:`.

## Tiers

| Tier | What | How | Speed |
| --- | --- | --- | --- |
| Unit | `planner.py`, `schedule.py`, no Home Assistant imports | `pytest tests/unit` | ms |
| Component | the integration inside the Home Assistant test harness against a fake ha-foxess-openapi | `pytest tests/component` | < 1 s per test |
| Contract | the fake must keep the shape of recordings from a real installation | `pytest tests/contract` | ms |
| Write cycle | one probe group through the real services on a real inverter, snapshot restored | `scripts/write_cycle.py`, by hand | minutes |
| Field | dry run for days, then live, acceptance over a week | observation | weeks |

CI runs the first three tiers on every push and pull request
(`.github/workflows/tests.yml`), ruff (`lint.yml`) and hassfest plus the HACS
action (`validate.yml`).

## The fake

`tests/component/fake_foxess_openapi.py` registers `get_schedule`,
`set_schedule` and `disable_scheduler` with the field names of the real
integration, creates the inverter device with its `(foxess_openapi, sn)`
identifier and the entities the bridge reads, and keeps state like the
cloud: writes change the groups and the flag, `apply_writes = False`
simulates cloud lag, `fail_writes_with` and `fail_reads_with` inject error
text such as a quota error. The same module holds the helpers the component
tests share (`setup_bridge`, `publish_plan`, `settle`, `next_quarter`).

## Recordings

`tests/fixtures/foxess_openapi/` holds the entity states and one
`get_schedule` response recorded from a real installation with
`scripts/record_contract.py`. Serial number and station name are replaced.
`tests/contract` checks that the fake's responses and entities still have
the recorded shape; when ha-foxess-openapi changes, re-record and the
contract tests show what the fake has to follow.

`tests/fixtures/emhass_plan_winter_day.json` is a 24 h EMHASS plan in the
exact shape EMHASS publishes (string dates, string values, both sensors).

## Write cycle

`FOX_ALLOW_WRITES=1 .venv/bin/python scripts/write_cycle.py` snapshots the
scheduler, writes one `ForceCharge` probe group whose window starts twelve
hours away from now, polls `get_schedule` until groups and master flag show
up, restores the snapshot and verifies it. It prints the findings as JSON.
It needs `HA_URL`, `HA_TOKEN` and `FOX_DEVICE_SN` in `.env`.

## Gotchas

- The `freezer` fixture freezes the event loop clock, so an `asyncio.sleep`
  in a code path a component test exercises never returns. Integration code
  must not sleep; tests advance time with `freezer.tick()` and
  `async_fire_time_changed()`.
- A jump across a quarter hour fires the pending timer as well as the
  debounced plan refresh. Assert on the effect (groups on the fake), not on
  which of the two runs came last.
- On macOS there is no `timeout` command; `perl -e 'alarm 110; exec @ARGV' --`
  guards a hanging test run.
