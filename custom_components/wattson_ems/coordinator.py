"""Coordinator: plant periodiek en stuurt (optioneel) de accu aan.

Schaduwmodus (sturing uit) publiceert alleen het advies; met de master-switch
aan wordt het eerste planuur uitgevoerd:
- laden      -> operation 'manual' + manual_power = -W  (negatief = laden)
- ontladen   -> operation 'smart_discharging' (P1-matching: nooit export,
                nooit meer dan de huisvraag)
- rust       -> operation 'off'
EV-guard: zodra een wallbox laadt wordt ontladen direct gestopt.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from . import planner as P
from .const import (
    ADAPTER_ZENDURE,
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
    DAGLICHT,
    DEFAULT_OPTIONS,
    EV_THRESHOLD_KW,
    UPDATE_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


class WattsonCoordinator:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        opt = entry.options

        def o(key: str) -> str:
            return opt.get(key, DEFAULT_OPTIONS[key])

        self.ent_price = o(CONF_ENT_PRICE)
        self.ent_soc = o(CONF_ENT_SOC)
        self.ent_p1 = o(CONF_ENT_P1)
        self.ent_wallbox_1 = o(CONF_ENT_WALLBOX_1)
        self.ent_wallbox_2 = o(CONF_ENT_WALLBOX_2)
        self.ent_pv_now = o(CONF_ENT_PV_NOW)
        self.ent_pv_remain = o(CONF_ENT_PV_REMAIN)
        self.ent_pv_tomorrow = o(CONF_ENT_PV_TOMORROW)
        self.ent_zd_operation = o(CONF_ENT_ZD_OPERATION)
        self.ent_zd_manual = o(CONF_ENT_ZD_MANUAL)
        self.ent_zd_hems = o(CONF_ENT_ZD_HEMS)
        self.ent_zd_chg = o(CONF_ENT_ZD_CHG)
        self.ent_zd_dis = o(CONF_ENT_ZD_DIS)
        self.adapter = o(CONF_ADAPTER)
        self.ent_gen_power = o(CONF_ENT_GEN_POWER)
        self.ent_gen_charge = o(CONF_ENT_GEN_CHARGE)
        self.ent_gen_discharge = o(CONF_ENT_GEN_DISCHARGE)

        cfg = json.load(open(os.path.join(os.path.dirname(__file__), "params.json"), encoding="utf-8"))
        b = cfg["battery"]
        cap = float(o(CONF_CAPACITY))
        min_soc = float(o(CONF_MIN_SOC_PCT)) / 100.0 * cap
        # actie-niveaus schalen mee met de ingestelde vermogens
        p_chg = float(o(CONF_P_CHARGE))
        p_dis = float(o(CONF_P_DISCHARGE))
        self.params = P.Params(
            capacity_kwh=cap, soc_min_kwh=min_soc, soc_max_kwh=cap,
            p_charge_max_w=p_chg, p_discharge_max_w=p_dis,
            charge_levels=tuple(p_chg * i / 4 for i in range(5)),
            discharge_levels=tuple(p_dis * i / 4 for i in range(5)),
            eta_nom=b["eta_nom"], p_fix_w=b["p_fix_w"], deg_cost=cfg["deg_cost"],
        )
        self.wedge = cfg["wedge"]
        self.pv_bias = cfg["pv_bias"]
        self.profile = {tuple(int(x) for x in k.split("|")): v for k, v in cfg["load_profile"].items()}
        self.trained_at = cfg["trained_at"]

        self.control_enabled = False   # master-switch (RestoreEntity zet dit terug)
        self.aggressiveness = "gebalanceerd"
        self.advies = "init"
        self.setpoint_w = 0.0
        self.plan_hours: list[dict] = []
        self.expected_saving = 0.0
        self.inputs: dict = {}
        self.last_error: str | None = None
        self.last_applied: str | None = None
        self.listeners: list = []
        self.sensors: list = []

    # ---------- lifecycle ----------
    async def async_start(self) -> None:
        self.listeners.append(async_track_time_interval(
            self.hass, self._tick, timedelta(minutes=UPDATE_MINUTES)))
        self.listeners.append(async_track_state_change_event(
            self.hass, [self.ent_wallbox_1, self.ent_wallbox_2], self._ev_guard))
        await self._tick(None)

    async def async_stop(self) -> None:
        for remove in self.listeners:
            remove()
        self.listeners = []

    # ---------- helpers ----------
    def _f(self, entity: str) -> float | None:
        if not entity:
            return None
        st = self.hass.states.get(entity)
        if st is None or st.state in ("unknown", "unavailable", None):
            return None
        try:
            return float(st.state)
        except ValueError:
            return None

    def _ev_charging(self) -> bool:
        w1 = self._f(self.ent_wallbox_1)
        w2 = self._f(self.ent_wallbox_2)
        return (w1 or 0.0) > EV_THRESHOLD_KW or (w2 or 0.0) > EV_THRESHOLD_KW

    def _price_forecast(self) -> list[tuple[datetime, float]]:
        st = self.hass.states.get(self.ent_price)
        if st is None:
            return []
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        out = []
        for item in st.attributes.get("forecast", []) or []:
            try:
                dt = datetime.fromisoformat(str(item["datetime"]).replace("Z", "+00:00"))
                price = float(item["electricity_price"]) / 1e7
            except (KeyError, ValueError, TypeError):
                continue
            if dt >= now:
                out.append((dt, price))
        out.sort(key=lambda x: x[0])
        if not out:
            cur = self._f(self.ent_price)
            if cur is not None:
                out = [(now, cur)]
        return out

    def _pv_curve(self, hours: list[datetime]) -> dict[datetime, float]:
        """Verdeel de PV-forecast dagtotalen over een daglicht-bel (W per uur)."""
        remain = (self._f(self.ent_pv_remain) or 0.0) * self.pv_bias
        tomorrow = (self._f(self.ent_pv_tomorrow) or 0.0) * self.pv_bias
        lo, hi = DAGLICHT
        today = datetime.now(timezone.utc).astimezone().date()

        def bell(h):  # gewicht per lokaal uur
            if h < lo or h >= hi:
                return 0.0
            return math.sin((h - lo) / (hi - lo) * math.pi) ** 2

        out = {}
        for day, budget in ((today, remain), (today + timedelta(days=1), tomorrow)):
            day_hours = [dt for dt in hours if dt.astimezone().date() == day]
            weights = [bell(dt.astimezone().hour) for dt in day_hours]
            tot = sum(weights)
            for dt, w in zip(day_hours, weights):
                out[dt] = (budget * w / tot * 1000.0) if tot > 0 else 0.0
        # het huidige uur weten we beter dan de bel
        pv_now = self._f(self.ent_pv_now)
        if hours and pv_now is not None:
            out[hours[0]] = pv_now * self.pv_bias
        return out

    # ---------- kern ----------
    async def _tick(self, _now) -> None:
        try:
            await self._plan_and_apply()
            self.last_error = None
        except Exception as err:  # noqa: BLE001 - watchdog: nooit crashen, wel loggen
            self.last_error = str(err)
            _LOGGER.exception("Wattson-tick faalde")
            if self.control_enabled:
                await self._set_battery("rust", 0.0)  # veilige stand
        for s in self.sensors:
            s.async_write_ha_state()

    async def _plan_and_apply(self) -> None:
        prices = self._price_forecast()
        soc_pct = self._f(self.ent_soc)
        if not prices or soc_pct is None:
            self.advies = "geen data"
            self.setpoint_w = 0.0
            return
        soc = soc_pct / 100.0 * self.params.capacity_kwh
        hours = [dt for dt, _ in prices]
        pv = self._pv_curve(hours)
        ev_now = self._ev_charging()

        steps = []
        for k, (dt, price) in enumerate(prices):
            loc = dt.astimezone()
            weekend = loc.weekday() >= 5
            load = self.profile.get((loc.hour, int(weekend)), 0.35) * 1000.0
            if k == 0:
                p1 = self._f(self.ent_p1)
                wb_w = max((self._f(self.ent_wallbox_1) or 0.0), (self._f(self.ent_wallbox_2) or 0.0)) * 1000.0
                if p1 is not None:
                    # actuele huisvraag excl. EV én excl. het eigen accuvermogen
                    # (anders ziet de planner zijn eigen laden als huislast)
                    b_chg = self._f(self.ent_zd_chg) or 0.0
                    b_dis = self._f(self.ent_zd_dis) or 0.0
                    load = max(p1 - wb_w - b_chg + b_dis + (pv.get(dt, 0.0)), 0.0)
            steps.append(P.Step(
                price_imp=price,
                price_exp=max(price - self.wedge, -0.5),
                load_w=load,
                pv_w=pv.get(dt, 0.0),
                ev_charging=ev_now if k == 0 else False,
            ))

        tv = P.terminal_value_from_prices([s.price_imp for s in steps], self.params)
        self.inputs = {
            "soc_kwh": round(soc, 2),
            "prijs_nu": round(steps[0].price_imp, 4),
            "huislast_nu_w": round(steps[0].load_w),
            "pv_nu_w": round(steps[0].pv_w),
            "pv_rest_vandaag_kwh": round((self._f(self.ent_pv_remain) or 0.0) * self.pv_bias, 1),
            "pv_morgen_kwh": round((self._f(self.ent_pv_tomorrow) or 0.0) * self.pv_bias, 1),
            "ev_laadt": ev_now,
            "horizon_uren": len(steps),
            "eindwaarde_restlading": round(tv, 3),
        }
        setpoints, cost = P.plan(steps, soc, self.params, terminal_value=tv)
        # kosten zonder accu over dezelfde horizon (voor het besparing-sensor)
        base = 0.0
        for s in steps:
            c, _, _, _ = P.hour_result(s, 0.0, self.params.soc_min_kwh, self.params)
            base += c
        self.expected_saving = round(base - cost, 2)

        sp = setpoints[0]
        self.setpoint_w = round(sp)
        self.plan_hours = []
        soc_sim = soc
        for (dt, pr), a, st in list(zip(prices, setpoints, steps))[:16]:
            _, soc_sim, _, _ = P.hour_result(st, a, soc_sim, self.params)
            self.plan_hours.append({
                "tijd": dt.astimezone().strftime("%H:%M"),
                "prijs": round(pr, 3),
                "setpoint_w": round(a),
                "soc_na_kwh": round(soc_sim, 2),
                "verwachte_last_w": round(st.load_w),
                "verwachte_pv_w": round(st.pv_w),
            })
        if sp > 50:
            self.advies = "laden"
        elif sp < -50:
            self.advies = "ontladen"
        else:
            self.advies = "rust"

        if not self.control_enabled:
            return
        if self.adapter == ADAPTER_ZENDURE and self.ent_zd_hems:
            hems = self.hass.states.get(self.ent_zd_hems)
            if hems is not None and hems.state == "on":
                # de eigen AI-modus is nog de baas: niet dubbel sturen
                self.last_applied = "geblokkeerd: accu-AI (HEMS) staat nog aan"
                return
        if self.advies == "laden":
            await self._set_battery("laden", abs(self.setpoint_w))
        elif self.advies == "ontladen" and not ev_now:
            await self._set_battery("ontladen", abs(self.setpoint_w))
        else:
            await self._set_battery("rust", 0.0)

    async def _set_battery(self, action: str, power_w: float) -> None:
        """Adapter-router: vertaal laden/ontladen/rust naar het accumerk."""
        if self.adapter == ADAPTER_ZENDURE:
            if action == "laden":
                await self._set_zendure("manual", -power_w)
            elif action == "ontladen":
                # smart_discharging = P1-matching van de Zendure zelf: volgt de
                # huisvraag en kan dus nooit exporteren
                await self._set_zendure("smart_discharging", 0.0)
            else:
                await self._set_zendure("off", 0.0)
            return
        # generiek: number-entiteiten; ontladen wordt begrensd op de actuele
        # netto-import zodat de accu nooit naar het net exporteert
        if action == "ontladen":
            p1 = self._f(self.ent_p1)
            power_w = min(power_w, max(p1 or 0.0, 0.0))
        signed = power_w if action == "laden" else (-power_w if action == "ontladen" else 0.0)
        if self.ent_gen_power:
            await self.hass.services.async_call(
                "number", "set_value", {"entity_id": self.ent_gen_power, "value": signed}, blocking=True)
        else:
            if self.ent_gen_charge:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": self.ent_gen_charge, "value": max(signed, 0.0)}, blocking=True)
            if self.ent_gen_discharge:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": self.ent_gen_discharge, "value": max(-signed, 0.0)}, blocking=True)
        self.last_applied = f"{action} ({signed:+.0f} W, generiek)"

    async def _set_zendure(self, mode: str, manual_w: float) -> None:
        cur = self.hass.states.get(self.ent_zd_operation)
        if mode == "manual":
            await self.hass.services.async_call(
                "number", "set_value", {"entity_id": self.ent_zd_manual, "value": manual_w}, blocking=True)
        if cur is None or cur.state != mode:
            await self.hass.services.async_call(
                "select", "select_option", {"entity_id": self.ent_zd_operation, "option": mode}, blocking=True)
        self.last_applied = f"{mode} ({manual_w:+.0f} W)" if mode == "manual" else mode

    @callback
    def _ev_guard(self, _event) -> None:
        """Auto begint te laden -> ontladen direct stoppen."""
        if self.control_enabled and self.advies == "ontladen" and self._ev_charging():
            self.hass.async_create_task(self._set_battery("rust", 0.0))
            self.advies = "rust (EV-guard)"
            for s in self.sensors:
                s.async_write_ha_state()
