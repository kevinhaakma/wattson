"""Master-switch: Wattson-sturing aan/uit (uit = schaduwmodus, alleen advies)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([WattsonControlSwitch(hass.data[DOMAIN][entry.entry_id])])


class WattsonControlSwitch(SwitchEntity, RestoreEntity):
    _attr_name = "Wattson sturing"
    _attr_unique_id = "wattson_sturing"
    _attr_icon = "mdi:robot"

    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.control_enabled = bool(last and last.state == "on")
        self._attr_is_on = self.coordinator.control_enabled

    async def async_turn_on(self, **kwargs):
        self.coordinator.control_enabled = True
        self._attr_is_on = True
        self.async_write_ha_state()
        await self.coordinator._tick(None)

    async def async_turn_off(self, **kwargs):
        self.coordinator.control_enabled = False
        self._attr_is_on = False
        self.async_write_ha_state()
        # bij uitzetten: accu in veilige ruststand
        await self.coordinator._set_zendure("off", 0.0)
