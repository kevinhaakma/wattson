"""Select: agressiviteit van de Wattson-planner (rustig / gebalanceerd / agressief)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import AGGRO_DEFAULT, AGGRO_LEVELS, DOMAIN
from .entity import wattson_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([WattsonAggroSelect(hass.data[DOMAIN][entry.entry_id])])


class WattsonAggroSelect(SelectEntity, RestoreEntity):
    _attr_name = "Wattson agressiviteit"
    _attr_unique_id = "wattson_agressiviteit"
    _attr_icon = "mdi:speedometer"
    _attr_options = list(AGGRO_LEVELS)

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = wattson_device_info(coordinator)
        self._attr_current_option = AGGRO_DEFAULT

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in AGGRO_LEVELS:
            self._attr_current_option = last.state
        self._apply()

    def _apply(self):
        level = AGGRO_LEVELS[self._attr_current_option]
        # de doelfunctie-knop: zelfvoorzienings-voorkeur + slijtagegewicht
        self.coordinator.params.alpha = level["pref"]
        self.coordinator.params.beta = level["pref"]
        self.coordinator.params.deg_cost = level["deg"]
        self.coordinator.aggressiveness = self._attr_current_option

    async def async_select_option(self, option: str):
        self._attr_current_option = option
        self._apply()
        self.async_write_ha_state()
        await self.coordinator._tick(None)  # direct herplannen met nieuw gewicht
