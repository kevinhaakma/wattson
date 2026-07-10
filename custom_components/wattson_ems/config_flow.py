"""Config- en options-flow voor Wattson.

Single-instance integratie: bij het toevoegen kies je alleen het accumerk
(adapter); alle bron-entiteiten en accu-eigenschappen zijn daarna aanpasbaar
via de Opties-knop op de integratie.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    ADAPTER_GENERIC,
    ADAPTER_MARSTEK,
    ADAPTER_ZENDURE,
    ADAPTERS,
    CONF_ADAPTER,
    CONF_CAPACITY,
    CONF_ENT_GEN_CHARGE,
    CONF_ENT_GEN_DISCHARGE,
    CONF_ENT_GEN_POWER,
    CONF_ENT_MS_CHARGE,
    CONF_ENT_MS_DISCHARGE,
    CONF_ENT_MS_MODE,
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
    CONF_SELL_THRESHOLD,
    DEFAULT_OPTIONS,
    DOMAIN,
)


def _ent(domain):
    """Entity-dropdown (i.p.v. vrij tekstveld)."""
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _schema(options: dict) -> vol.Schema:
    def d(key: str):
        return options.get(key, DEFAULT_OPTIONS[key])

    def field(key, domain, required=True):
        """Required/Optional met dropdown; lege default weglaten (selector
        accepteert geen lege string als suggested value)."""
        cur = d(key)
        kw = {"default": cur} if cur else {}
        cls = vol.Required if required and cur else vol.Optional
        return (cls(key, **kw), _ent(domain))

    adapter = d(CONF_ADAPTER)
    pairs = [
        (vol.Required(CONF_ADAPTER, default=adapter), vol.In(ADAPTERS)),
        field(CONF_ENT_PRICE, "sensor"),
        field(CONF_ENT_SOC, "sensor"),
        field(CONF_ENT_P1, "sensor"),
        field(CONF_ENT_WALLBOX_1, "sensor", required=False),
        field(CONF_ENT_WALLBOX_2, "sensor", required=False),
        field(CONF_ENT_PV_NOW, "sensor"),
        field(CONF_ENT_PV_REMAIN, "sensor"),
        field(CONF_ENT_PV_TOMORROW, "sensor"),
        (vol.Required(CONF_CAPACITY, default=d(CONF_CAPACITY)), vol.Coerce(float)),
        (vol.Required(CONF_MIN_SOC_PCT, default=d(CONF_MIN_SOC_PCT)), vol.Coerce(float)),
        (vol.Required(CONF_P_CHARGE, default=d(CONF_P_CHARGE)), vol.Coerce(float)),
        (vol.Required(CONF_P_DISCHARGE, default=d(CONF_P_DISCHARGE)), vol.Coerce(float)),
        (vol.Required(CONF_SELL_THRESHOLD, default=d(CONF_SELL_THRESHOLD)), vol.Coerce(float)),
    ]
    if adapter == ADAPTER_ZENDURE:
        pairs += [
            field(CONF_ENT_ZD_OPERATION, "select"),
            field(CONF_ENT_ZD_MANUAL, "number"),
            field(CONF_ENT_ZD_HEMS, "binary_sensor", required=False),
            field(CONF_ENT_ZD_CHG, "sensor", required=False),
            field(CONF_ENT_ZD_DIS, "sensor", required=False),
        ]
    elif adapter == ADAPTER_MARSTEK:
        pairs += [
            field(CONF_ENT_MS_MODE, ["select", "number"]),
            field(CONF_ENT_MS_CHARGE, "number"),
            field(CONF_ENT_MS_DISCHARGE, "number"),
        ]
    else:
        pairs += [
            field(CONF_ENT_GEN_POWER, "number", required=False),
            field(CONF_ENT_GEN_CHARGE, "number", required=False),
            field(CONF_ENT_GEN_DISCHARGE, "number", required=False),
        ]
    return vol.Schema(dict(pairs))


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
