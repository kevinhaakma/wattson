"""Config- en options-flow voor Wattson.

Single-instance integratie met een begeleide eerste setup voor accumerk,
meetbronnen, stuurentiteiten en veilige accugrenzen. Alles blijft daarna
aanpasbaar via de Opties-knop op de integratie.
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
    CONF_ENT_BAT_CHG,
    CONF_ENT_BAT_DIS,
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
    CONF_ENT_WALLBOX_1_HOME,
    CONF_ENT_WALLBOX_2,
    CONF_ENT_WALLBOX_2_HOME,
    CONF_ENT_ZD_ACMODE,
    CONF_ENT_ZD_CHG,
    CONF_ENT_ZD_DIS,
    CONF_ENT_ZD_HEMS,
    CONF_ENT_ZD_INLIM,
    CONF_ENT_ZD_MANUAL,
    CONF_ENT_ZD_OPERATION,
    CONF_ENT_ZD_OUTLIM,
    CONF_MIN_SOC_PCT,
    CONF_P_CHARGE,
    CONF_P_DISCHARGE,
    CONF_WEDGE_POST,
    DEFAULT_OPTIONS,
    DOMAIN,
)

# optionele entity-velden per adapter: nodig om "leeg gelaten" te kunnen
# onderscheiden van "niet in het formulier" (de selector laat een geleegd
# veld weg uit user_input; zonder deze lijst kun je een optioneel veld
# nooit meer wissen omdat de merge de oude waarde terugzet)
_OPTIONAL_ENTITY_KEYS = {
    None: [
        CONF_ENT_WALLBOX_1, CONF_ENT_WALLBOX_2,
        CONF_ENT_WALLBOX_1_HOME, CONF_ENT_WALLBOX_2_HOME,
        CONF_ENT_PV_NOW, CONF_ENT_PV_REMAIN, CONF_ENT_PV_TOMORROW,
    ],  # adapter-onafhankelijk
    ADAPTER_ZENDURE: [CONF_ENT_ZD_ACMODE, CONF_ENT_ZD_HEMS, CONF_ENT_ZD_CHG, CONF_ENT_ZD_DIS],
    ADAPTER_MARSTEK: [CONF_ENT_BAT_CHG, CONF_ENT_BAT_DIS],
    ADAPTER_GENERIC: [
        CONF_ENT_GEN_POWER, CONF_ENT_GEN_CHARGE, CONF_ENT_GEN_DISCHARGE,
        CONF_ENT_BAT_CHG, CONF_ENT_BAT_DIS,
    ],
}


def _ent(domain):
    """Entity-dropdown (i.p.v. vrij tekstveld)."""
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _value(options: dict, key: str):
    return options.get(key, DEFAULT_OPTIONS[key])


def _field(options: dict, key, domain, required=True):
    """Entity-selector met een default alleen wanneer die echt bestaat."""
    current = _value(options, key)
    kwargs = {"default": current} if current else {}
    marker = vol.Required if required else vol.Optional
    return marker(key, **kwargs), _ent(domain)


def _source_schema(options: dict) -> vol.Schema:
    """Meetbronnen; alleen prijs, SoC en netvermogen zijn noodzakelijk."""
    return vol.Schema(dict([
        _field(options, CONF_ENT_PRICE, "sensor"),
        _field(options, CONF_ENT_SOC, "sensor"),
        _field(options, CONF_ENT_P1, "sensor"),
        _field(options, CONF_ENT_WALLBOX_1, "sensor", required=False),
        _field(options, CONF_ENT_WALLBOX_2, "sensor", required=False),
        # voertuigtelemetrie meet ook laden elders; de gate beperkt de
        # bijbehorende meting tot momenten dat het voertuig thuis is
        _field(options, CONF_ENT_WALLBOX_1_HOME,
               ["device_tracker", "person", "binary_sensor", "input_boolean"],
               required=False),
        _field(options, CONF_ENT_WALLBOX_2_HOME,
               ["device_tracker", "person", "binary_sensor", "input_boolean"],
               required=False),
        _field(options, CONF_ENT_PV_NOW, "sensor", required=False),
        _field(options, CONF_ENT_PV_REMAIN, "sensor", required=False),
        _field(options, CONF_ENT_PV_TOMORROW, "sensor", required=False),
    ]))


def _adapter_schema(options: dict) -> vol.Schema:
    adapter = _value(options, CONF_ADAPTER)
    pairs = []
    if adapter == ADAPTER_ZENDURE:
        pairs += [
            _field(options, CONF_ENT_ZD_OPERATION, "select"),
            _field(options, CONF_ENT_ZD_MANUAL, "number"),
            _field(options, CONF_ENT_ZD_INLIM, "number"),
            _field(options, CONF_ENT_ZD_OUTLIM, "number"),
            _field(options, CONF_ENT_ZD_ACMODE, "select", required=False),
            _field(options, CONF_ENT_ZD_HEMS, "binary_sensor", required=False),
            _field(options, CONF_ENT_ZD_CHG, "sensor", required=False),
            _field(options, CONF_ENT_ZD_DIS, "sensor", required=False),
        ]
    elif adapter == ADAPTER_MARSTEK:
        pairs += [
            _field(options, CONF_ENT_MS_MODE, ["select", "number"]),
            _field(options, CONF_ENT_MS_CHARGE, "number"),
            _field(options, CONF_ENT_MS_DISCHARGE, "number"),
            _field(options, CONF_ENT_BAT_CHG, "sensor", required=False),
            _field(options, CONF_ENT_BAT_DIS, "sensor", required=False),
        ]
    else:
        pairs += [
            _field(options, CONF_ENT_GEN_POWER, "number", required=False),
            _field(options, CONF_ENT_GEN_CHARGE, "number", required=False),
            _field(options, CONF_ENT_GEN_DISCHARGE, "number", required=False),
            _field(options, CONF_ENT_BAT_CHG, "sensor", required=False),
            _field(options, CONF_ENT_BAT_DIS, "sensor", required=False),
        ]
    return vol.Schema(dict(pairs))


def _battery_schema(options: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_CAPACITY, default=_value(options, CONF_CAPACITY)): vol.Coerce(float),
        vol.Required(CONF_MIN_SOC_PCT, default=_value(options, CONF_MIN_SOC_PCT)): vol.Coerce(float),
        vol.Required(CONF_P_CHARGE, default=_value(options, CONF_P_CHARGE)): vol.Coerce(float),
        vol.Required(CONF_P_DISCHARGE, default=_value(options, CONF_P_DISCHARGE)): vol.Coerce(float),
        vol.Required(CONF_WEDGE_POST, default=_value(options, CONF_WEDGE_POST)): vol.Coerce(float),
    })


def _schema(options: dict) -> vol.Schema:
    """Compacte options-flow voor bestaande installaties."""
    adapter = _value(options, CONF_ADAPTER)
    combined = {vol.Required(CONF_ADAPTER, default=adapter): vol.In(ADAPTERS)}
    combined.update(_source_schema(options).schema)
    combined.update(_adapter_schema(options).schema)
    combined.update(_battery_schema(options).schema)
    return vol.Schema(combined)


def _new_options(adapter: str) -> dict:
    """Veilige start voor nieuwe installaties zonder persoonlijke entity-id's."""
    options = dict(DEFAULT_OPTIONS)
    for key in options:
        if key.startswith("ent_"):
            options[key] = ""
    options[CONF_ADAPTER] = adapter
    return options


def _validate(merged: dict) -> dict[str, str]:
    """Cross-veld-validatie; retourneert {veld_of_base: error_key}."""
    errors: dict[str, str] = {}
    try:
        cap = float(merged.get(CONF_CAPACITY, 0))
        min_soc = float(merged.get(CONF_MIN_SOC_PCT, 0))
        p_chg = float(merged.get(CONF_P_CHARGE, 0))
        p_dis = float(merged.get(CONF_P_DISCHARGE, 0))
    except (TypeError, ValueError):
        return {"base": "invalid_number"}
    if cap <= 0:
        errors[CONF_CAPACITY] = "must_be_positive"
    if not 0 <= min_soc < 100:
        errors[CONF_MIN_SOC_PCT] = "soc_out_of_range"
    if p_chg <= 0:
        errors[CONF_P_CHARGE] = "must_be_positive"
    if p_dis <= 0:
        errors[CONF_P_DISCHARGE] = "must_be_positive"
    try:
        if float(merged.get(CONF_WEDGE_POST, 0)) < 0:
            errors[CONF_WEDGE_POST] = "must_be_positive"
    except (TypeError, ValueError):
        errors[CONF_WEDGE_POST] = "invalid_number"
    adapter = merged.get(CONF_ADAPTER)
    if adapter == ADAPTER_GENERIC:
        has_signed = bool(merged.get(CONF_ENT_GEN_POWER))
        has_pair = bool(merged.get(CONF_ENT_GEN_CHARGE)) and bool(merged.get(CONF_ENT_GEN_DISCHARGE))
        if not (has_signed or has_pair):
            errors["base"] = "generic_power_missing"
    return errors


class WattsonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Begeleide eerste setup: merk, bronnen, bediening en accugegevens."""

    VERSION = 1
    _setup_options: dict

    async def async_step_user(self, user_input: dict | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            self._setup_options = _new_options(user_input[CONF_ADAPTER])
            return await self.async_step_sources()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADAPTER, default=ADAPTER_ZENDURE): vol.In(ADAPTERS),
            }),
        )

    async def async_step_sources(self, user_input: dict | None = None):
        if user_input is not None:
            self._setup_options.update(user_input)
            for key in _OPTIONAL_ENTITY_KEYS[None]:
                if key not in user_input:
                    self._setup_options[key] = ""
            return await self.async_step_adapter()
        return self.async_show_form(
            step_id="sources", data_schema=_source_schema(self._setup_options))

    async def async_step_adapter(self, user_input: dict | None = None):
        if user_input is not None:
            self._setup_options.update(user_input)
            adapter = self._setup_options[CONF_ADAPTER]
            for key in _OPTIONAL_ENTITY_KEYS.get(adapter, []):
                if key not in user_input:
                    self._setup_options[key] = ""
            errors = _validate(self._setup_options)
            # Numerieke defaults zijn hier al geldig; alleen een generic
            # combinatie kan op deze stap een cross-field-fout opleveren.
            if errors.get("base") == "generic_power_missing":
                return self.async_show_form(
                    step_id="adapter",
                    data_schema=_adapter_schema(self._setup_options),
                    errors={"base": "generic_power_missing"},
                )
            return await self.async_step_battery()
        return self.async_show_form(
            step_id="adapter", data_schema=_adapter_schema(self._setup_options))

    async def async_step_battery(self, user_input: dict | None = None):
        errors = {}
        if user_input is not None:
            self._setup_options.update(user_input)
            errors = _validate(self._setup_options)
            if not errors:
                return self.async_create_entry(
                    title="Wattson", data={}, options=self._setup_options)
        return self.async_show_form(
            step_id="battery",
            data_schema=_battery_schema(self._setup_options),
            errors=errors,
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
            # optionele velden die in het getoonde formulier stonden maar
            # niet in user_input zitten, zijn door de gebruiker geleegd
            for key in _OPTIONAL_ENTITY_KEYS[None] + _OPTIONAL_ENTITY_KEYS.get(self._shown_adapter, []):
                if key not in user_input:
                    merged[key] = ""
            errors = _validate(merged)
            if errors:
                return self.async_show_form(
                    step_id="init", data_schema=_schema(merged), errors=errors)
            return self.async_create_entry(title="", data=merged)
        opts = dict(self.config_entry.options)
        self._shown_adapter = opts.get(CONF_ADAPTER, ADAPTER_ZENDURE)
        return self.async_show_form(step_id="init", data_schema=_schema(opts))
