"""Sensoren: Wattson-advies (+plan) en verwachte besparing."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import wattson_device_info


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
        self._attr_device_info = wattson_device_info(coordinator)
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
            "verkopen": (
                "actief" if c.advies == "verkopen"
                else f"gewapend (drempel €{c.sell_threshold:.2f})" if c.sell_enabled
                else "uit"
            ),
            "reserve_kwh": round(c.reserve_kwh, 2),
            "historie": list(c.history),
            "agressiviteit": c.aggressiveness,
            "adapter": c.adapter,
            "watchdog_telemetrie": "actief" if any(c._bat_flow_entities()) else "niet geconfigureerd",
            "export_guard": "apparaat-P1-matching" if c.caps.p1_matching else "Wattson P1-guard",
            "sturing_actief": c.control_enabled,
            "laatst_gestuurd": c.last_applied,
            "fout": c.last_error,
            "getraind_tot": c.trained_at,
            "pv_bias": c.pv_bias,
        }


class WattsonBesparingSensor(SensorEntity):
    """Verwacht planvoordeel: kosten van niets-doen minus plan-kosten over de
    horizon, symmetrisch verrekend (zelfde start-SoC, zelfde eindwaarde voor
    restlading). Dit is het voordeel van het plán, geen kasstroom-garantie."""

    _attr_name = "Wattson verwacht planvoordeel"
    _attr_unique_id = "wattson_verwachte_besparing"  # ongewijzigd: entity-id blijft stabiel
    _attr_icon = "mdi:currency-eur"
    _attr_native_unit_of_measurement = "EUR"
    _attr_should_poll = False

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = wattson_device_info(coordinator)
        coordinator.sensors.append(self)

    @property
    def native_value(self):
        return self.coordinator.expected_saving
