"""Constants for the EMHASS FoxESS Bridge integration."""

from __future__ import annotations

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "emhass_foxess_bridge"
NAME = "EMHASS FoxESS Bridge"
ATTRIBUTION = "Schedule written through the FoxESS Cloud Open API integration"

# The integration that owns the API key and talks to the FoxESS cloud.
FOX_DOMAIN = "foxess_openapi"

# Config entry data
CONF_DEVICE_ID = "device_id"
CONF_EMHASS_ENTITY = "emhass_entity"
CONF_EMHASS_GRID_ENTITY = "emhass_grid_entity"

# Config entry options (switches and planner tunables)
CONF_ENABLED = "enabled"
CONF_DRY_RUN = "dry_run"
CONF_CHARGE_THRESHOLD_W = "charge_threshold_w"
CONF_MIN_WINDOW_MINUTES = "min_window_minutes"
CONF_MAX_GROUPS = "max_groups"
CONF_PLAN_MAX_AGE_HOURS = "plan_max_age_hours"

DEFAULT_EMHASS_ENTITY = "sensor.p_batt_forecast"
DEFAULT_EMHASS_GRID_ENTITY = "sensor.p_grid_forecast"
DEFAULT_ENABLED = False
DEFAULT_DRY_RUN = True
DEFAULT_PLAN_MAX_AGE_HOURS = 3

# EMHASS attribute names (RetrieveHass.get_attr_data_dict)
ATTR_BATTERY_SCHEDULE = "battery_scheduled_power"
ATTR_GRID_FORECAST = "forecasts"

# Apply loop timing
APPLY_MINUTES: tuple[int, ...] = (0, 15, 30, 45)
APPLY_SECOND = 20
PLAN_SETTLE_SECONDS = 30  # coalesce the two EMHASS sensors into one run
WRITE_SETTLE_SECONDS = 60  # FoxESS reads may lag behind a write this long
# Stop writing when fewer daily API calls than this remain, so the other
# integration keeps polling and the quota error never actually hits.
QUOTA_RESERVE = 60

ISSUE_RATE_LIMITED = "rate_limited"

# Services
SERVICE_APPLY_PLAN = "apply_plan"
SERVICE_CLEAR_SCHEDULE = "clear_schedule"
SERVICE_SET_SCHEDULE = "set_schedule"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_GROUPS = "groups"
ATTR_ENABLE = "enable"
