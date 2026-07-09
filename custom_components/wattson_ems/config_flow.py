"""Config- en options-flow voor Wattson.

Single-instance integratie: bij het toevoegen kies je alleen het accumerk
(adapter); alle bron-entiteiten en accu-eigenschappen zijn daarna aanpasbaar
via de Opties-knop op de integratie.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    ADAPTER_GENERIC,
    ADAPTER_ZENDURE,
    ADAPTERS,
    CONF_ADAPTER,
    CONF_CAPACITY,
    CONF_ENT_GEN_CHARGE,
    CONF_ENT_GEN_DISCHARGE,
    CONF_ENT_GEN_POWER,
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
    CONF_MIN_SOC_PCT,
    CONF_P_CHARGE,
    CONF_P_DISCHARGE,
    DEFAULT_OPTIONS,
    DOMAIN,
)


def _schema(options: dict) -> vol.Schema:
    def d(key: str):
        return options.get(key, DEFAULT_OPTIONS[key])

    adapter = d(CONF_ADAPTER)
    fields = {
        vol.Required(CONF_ADAPTER, default=adapter): vol.In(ADAPTERS),
        vol.Required(CONF_ENT_PRICE, default=d(CONF_ENT_PRICE)): str,
        vol.Required(CONF_ENT_SOC, default=d(CONF_ENT_SOC)): str,
        vol.Required(CONF_ENT_P1, default=d(CONF_ENT_P1)): str,
        vol.Optional(CONF_ENT_WALLBOX_1, default=d(CONF_ENT_WALLBOX_1)): str,
        vol.Optional(CONF_ENT_WALLBOX_2, default=d(CONF_ENT_WALLBOX_2)): str,
        vol.Required(CONF_ENT_PV_NOW, default=d(CONF_ENT_PV_NOW)): str,
        vol.Required(CONF_ENT_PV_REMAIN, default=d(CONF_ENT_PV_REMAIN)): str,
        vol.Required(CONF_ENT_PV_TOMORROW, default=d(CONF_ENT_PV_TOMORROW)): str,
        vol.Required(CONF_CAPACITY, default=d(CONF_CAPACITY)): vol.Coerce(float),
        vol.Required(CONF_MIN_SOC_PCT, default=d(CONF_MIN_SOC_PCT)): vol.Coerce(float),
        vol.Required(CONF_P_CHARGE, default=d(CONF_P_CHARGE)): vol.Coerce(float),
        vol.Required(CONF_P_DISCHARGE, default=d(CONF_P_DISCHARGE)): vol.Coerce(float),
    }
    if adapter == ADAPTER_ZENDURE:
        fields.update({
            vol.Required(CONF_ENT_ZD_OPERATION, default=d(CONF_ENT_ZD_OPERATION)): str,
            vol.Required(CONF_ENT_ZD_MANUAL, default=d(CONF_ENT_ZD_MANUAL)): str,
            vol.Optional(CONF_ENT_ZD_HEMS, default=d(CONF_ENT_ZD_HEMS)): str,
            vol.Optional(CONF_ENT_ZD_CHG, default=d(CONF_ENT_ZD_CHG)): str,
            vol.Optional(CONF_ENT_ZD_DIS, default=d(CONF_ENT_ZD_DIS)): str,
        })
    else:
        fields.update({
            vol.Optional(CONF_ENT_GEN_POWER, default=d(CONF_ENT_GEN_POWER)): str,
            vol.Optional(CONF_ENT_GEN_CHARGE, default=d(CONF_ENT_GEN_CHARGE)): str,
            vol.Optional(CONF_ENT_GEN_DISCHARGE, default=d(CONF_ENT_GEN_DISCHARGE)): str,
        })
    return vol.Schema(fields)


class WattsonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: één instantie; alleen adapterkeuze, rest via Opties."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            options = dict(DEFAULT_OPTIONS)
            options[CONF_ADAPTER] = user_input[CONF_ADAPTER]
            return self.async_create_entry(title="Wattson", data={}, options=options)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADAPTER, default=ADAPTER_ZENDURE): vol.In(ADAPTERS),
            }),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "WattsonOptionsFlow":
        return WattsonOptionsFlow()


class WattsonOptionsFlow(config_entries.OptionsFlow):
    """Bron-entiteiten en accu-eigenschappen achteraf aanpasbaar.

    Let op: self.config_entry wordt door HA zelf gezet — handmatig toewijzen
    is sinds HA 2024.12 verboden en gaf hier een 500 op de options-flow.
    """

    _shown_adapter: str | None = None

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            merged = {**dict(self.config_entry.options), **user_input}
            if user_input.get(CONF_ADAPTER) != self._shown_adapter:
                # ander merk gekozen dan waar het formulier voor stond:
                # opnieuw tonen met de velden van dat merk
                self._shown_adapter = merged[CONF_ADAPTER]
                return self.async_show_form(step_id="init", data_schema=_schema(merged))
            return self.async_create_entry(title="", data=merged)
        opts = dict(self.config_entry.options)
        self._shown_adapter = opts.get(CONF_ADAPTER, ADAPTER_ZENDURE)
        return self.async_show_form(step_id="init", data_schema=_schema(opts))
