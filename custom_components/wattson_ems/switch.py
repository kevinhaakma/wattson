"""Master-switch: Wattson-sturing aan/uit (uit = schaduwmodus, alleen advies)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .entity import wattson_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WattsonControlSwitch(coordinator),
        WattsonAssistSwitch(coordinator),
        WattsonSellSwitch(coordinator),
    ])


class WattsonControlSwitch(SwitchEntity, RestoreEntity):
    _attr_name = "Wattson sturing"
    _attr_unique_id = "wattson_sturing"
    _attr_icon = "mdi:robot"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = wattson_device_info(coordinator)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.control_enabled = bool(last and last.state == "on")
        self._attr_is_on = self.coordinator.control_enabled
        if self.coordinator.control_enabled:
            # opstart-race dicht: de eerste plan-tick kan vóór deze restore
            # gedraaid hebben en dan draait Wattson tot de volgende tick
            # (10 min) ongewild in schaduwmodus — direct opnieuw plannen
            self.hass.async_create_task(self.coordinator._tick(None))

    async def async_turn_on(self, **kwargs):
        self.coordinator.control_enabled = True
        self._attr_is_on = True
        self.async_write_ha_state()
        await self.coordinator._tick(None)

    async def async_turn_off(self, **kwargs):
        was_assisting = self.coordinator.assist_active is not None
        self.coordinator.assist_active = None
        self.coordinator.control_enabled = False
        self._attr_is_on = False
        self.async_write_ha_state()
        # bij uitzetten: accu in veilige ruststand
        await self.coordinator._set_battery("rust", 0.0)
        if was_assisting:
            self.coordinator.advies = "rust"
            self.coordinator.setpoint_w = 0.0
            self.coordinator.reden = "sturing uitgezet — assist gestopt"


class WattsonAssistSwitch(SwitchEntity, RestoreEntity):
    """Dynamisch bijspringen: realtime piek-assist en overschot-opslag
    bovenop het uurplan, bewaakt door de plan-reserve."""

    _attr_name = "Wattson bijspringen"
    _attr_unique_id = "wattson_bijspringen"
    _attr_icon = "mdi:flash-auto"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = wattson_device_info(coordinator)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.assist_enabled = bool(last and last.state == "on")
        self._attr_is_on = self.coordinator.assist_enabled

    async def async_turn_on(self, **kwargs):
        self.coordinator.assist_enabled = True
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.coordinator.assist_enabled = False
        self._attr_is_on = False
        self.async_write_ha_state()
        if self.coordinator.assist_active:
            self.coordinator.assist_active = None
            await self.coordinator._set_battery("rust", 0.0)
            self.coordinator.advies = "rust"
            self.coordinator.setpoint_w = 0.0
            self.coordinator.reden = "bijspringen uitgezet"


class WattsonSellSwitch(SwitchEntity, RestoreEntity):
    """Verkopen: met deze switch aan mag de planner ontladen vóórbij de
    huisvraag (= exporteren); óf dat loont beslist de DP per uur zelf."""

    _attr_name = "Wattson verkopen"
    _attr_unique_id = "wattson_verkopen"
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = wattson_device_info(coordinator)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.sell_enabled = bool(last and last.state == "on")
        self._attr_is_on = self.coordinator.sell_enabled

    async def async_turn_on(self, **kwargs):
        self.coordinator.sell_enabled = True
        self._attr_is_on = True
        self.async_write_ha_state()
        await self.coordinator._tick(None)  # direct herplannen met verkoop-optie

    async def async_turn_off(self, **kwargs):
        self.coordinator.sell_enabled = False
        self._attr_is_on = False
        self.async_write_ha_state()
        if self.coordinator.advies == "verkopen":
            await self.coordinator._tick(None)  # herplannen zonder verkoop
