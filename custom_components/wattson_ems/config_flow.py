"""Config- en options-flow voor Wattson.

Single-instance integratie: de gebruiker hoeft bij het toevoegen niets in te
vullen (er is maar één accu-planner nodig). De bron-entiteiten worden na het
aanmaken ingesteld/aangepast via de Opties-knop op de integratie.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_ENT_P1,
    CONF_ENT_PRICE,
    CONF_ENT_PV_NOW,
    CONF_ENT_PV_REMAIN,
    CONF_ENT_PV_TOMORROW,
    CONF_ENT_SOC,
    CONF_ENT_WALLBOX_1,
    CONF_ENT_WALLBOX_2,
    CONF_ENT_ZD_CHG,
    CONF_ENT_ZD_DIS,
    CONF_ENT_ZD_HEMS,
    CONF_ENT_ZD_MANUAL,
    CONF_ENT_ZD_OPERATION,
    DEFAULT_OPTIONS,
    DOMAIN,
)


def _schema(options: dict) -> vol.Schema:
    def d(key: str) -> str:
        return options.get(key, DEFAULT_OPTIONS[key])

    return vol.Schema(
        {
            vol.Required(CONF_ENT_PRICE, default=d(CONF_ENT_PRICE)): str,
            vol.Required(CONF_ENT_SOC, default=d(CONF_ENT_SOC)): str,
            vol.Required(CONF_ENT_P1, default=d(CONF_ENT_P1)): str,
            vol.Optional(CONF_ENT_WALLBOX_1, default=d(CONF_ENT_WALLBOX_1)): str,
            vol.Optional(CONF_ENT_WALLBOX_2, default=d(CONF_ENT_WALLBOX_2)): str,
            vol.Required(CONF_ENT_PV_NOW, default=d(CONF_ENT_PV_NOW)): str,
            vol.Required(CONF_ENT_PV_REMAIN, default=d(CONF_ENT_PV_REMAIN)): str,
            vol.Required(CONF_ENT_PV_TOMORROW, default=d(CONF_ENT_PV_TOMORROW)): str,
            vol.Required(CONF_ENT_ZD_OPERATION, default=d(CONF_ENT_ZD_OPERATION)): str,
            vol.Required(CONF_ENT_ZD_MANUAL, default=d(CONF_ENT_ZD_MANUAL)): str,
            vol.Required(CONF_ENT_ZD_HEMS, default=d(CONF_ENT_ZD_HEMS)): str,
            vol.Optional(CONF_ENT_ZD_CHG, default=d(CONF_ENT_ZD_CHG)): str,
            vol.Optional(CONF_ENT_ZD_DIS, default=d(CONF_ENT_ZD_DIS)): str,
        }
    )


class WattsonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: één instantie, meteen aanmaken met standaard-opties."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Wattson", data={}, options=dict(DEFAULT_OPTIONS))
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "WattsonOptionsFlow":
        return WattsonOptionsFlow(config_entry)


class WattsonOptionsFlow(config_entries.OptionsFlow):
    """Bron-entiteiten achteraf aanpasbaar maken."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(dict(self.config_entry.options)),
        )
