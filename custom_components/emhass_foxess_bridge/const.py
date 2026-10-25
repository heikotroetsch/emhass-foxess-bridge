"""Constants for the EMHASS FoxESS Bridge integration."""

from __future__ import annotations

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "emhass_foxess_bridge"
NAME = "EMHASS FoxESS Bridge"
ATTRIBUTION = "Data from the FoxESS Cloud Open API"

CONF_API_KEY = "api_key"
CONF_DEVICE_SN = "device_sn"
CONF_EMHASS_ENTITY = "emhass_entity"
CONF_DRY_RUN = "dry_run"

DEFAULT_EMHASS_ENTITY = "sensor.p_batt_forecast"
DEFAULT_SCAN_INTERVAL_MINUTES = 5

# Variables requested from /op/v1/device/real/query. Keys match the Open API
# variable names; the sensor platform maps them to entities.
REALTIME_VARIABLES: tuple[str, ...] = (
    "SoC",
    "pvPower",
    "loadsPower",
    "gridConsumptionPower",
    "feedinPower",
    "batChargePower",
    "batDischargePower",
    "runningState",
)
