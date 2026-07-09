"""Sensoren: Wattson-advies (+plan) en verwachte besparing."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WattsonAdviesSensor(coordinator), WattsonBesparingSensor(coordinator)])


class WattsonAdviesSensor(SensorEntity):
    _attr_name = "Wattson advies"
    _attr_unique_id = "wattson_advies"
    _attr_icon = "mdi:battery-clock"
    _attr_should_poll = False

    def __init__(self, coordinator):
        self.coordinator = coordinator
        coordinator.sensors.append(self)

    @property
    def native_value(self):
        return self.coordinator.advies

    @property
    def extra_state_attributes(self):
        c = self.coordinator
        return {
            "setpoint_w": c.setpoint_w,
            "plan": c.plan_hours,
            "berekend_met": c.inputs,
            "reden": c.reden,
            "volgende_actie": c.volgende_actie,
            "bijspringen": c.assist_active or ("stand-by" if c.assist_enabled else "uit"),
            "reserve_kwh": round(c.reserve_kwh, 2),
            "historie": list(c.history),
            "agressiviteit": c.aggressiveness,
            "sturing_actief": c.control_enabled,
            "laatst_gestuurd": c.last_applied,
            "fout": c.last_error,
            "getraind_tot": c.trained_at,
            "pv_bias": c.pv_bias,
        }


class WattsonBesparingSensor(SensorEntity):
    _attr_name = "Wattson verwachte besparing"
    _attr_unique_id = "wattson_verwachte_besparing"
    _attr_icon = "mdi:currency-eur"
    _attr_native_unit_of_measurement = "EUR"
    _attr_should_poll = False

    def __init__(self, coordinator):
        self.coordinator = coordinator
        coordinator.sensors.append(self)

    @property
    def native_value(self):
        return self.coordinator.expected_saving
