"""Coordinator: plant periodiek en stuurt (optioneel) de accu aan.

Schaduwmodus (sturing uit) publiceert alleen het advies; met de master-switch
aan wordt het eerste planuur uitgevoerd:
- laden      -> operation 'manual' + manual_power = -W  (negatief = laden)
- ontladen   -> operation 'smart_discharging' (P1-matching: nooit export,
                nooit meer dan de huisvraag)
- verkopen   -> operation 'manual' + manual_power = +W  (vast vermogen,
                exporteert boven de huisvraag; alleen boven de drempelprijs
                en alleen met de verkoop-switch aan)
- rust       -> operation 'off'
EV-guard: zodra een wallbox laadt wordt ontladen/verkopen direct gestopt.

Robuustheid (v1.5):
- alle besluiten over apparaatgedrag gebruiken VERSE data (last_updated-leeftijd);
  een bevroren sensorwaarde is geen bewijs, dus geen noodstop en ook geen
  opheffing daarvan op basis van stilstaande of unavailable telemetrie
- de watchdog stopt een laad-runaway door de INPUT-limiet dicht te zetten en
  een ontlaad-runaway via de OUTPUT-limiet (device-niveau: dat komt ook aan
  als de manager-select al 'off' zegt)
- na een noodstop blijven de limieten dicht; de eerstvolgende echte actie
  opent alleen de limiet die die actie nodig heeft
- 'rust' vertrouwt niet op de select-stand: meet de accu aantoonbaar activiteit
  terwijl rust gecommandeerd is, dan gaan de apparaat-limieten direct op 0
- blijft telemetrie langer dan GEENDATA_STOP_S stil terwijl sturing aan staat,
  dan gaat de accu eenmalig naar de veilige stand
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from . import adapters as A
from . import planner as P
from .const import (
    ASSIST_EXPORT_W,
    ASSIST_IMPORT_W,
    ASSIST_SOC_MARGE_KWH,
    ASSIST_STOP_W,
    ASSIST_THROTTLE_S,
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
    CONF_ENT_WALLBOX_2,
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
    CONF_SELL_THRESHOLD,
    DAGLICHT,
    DEFAULT_OPTIONS,
    DIS_GUARD_DEADBAND_W,
    DIS_GUARD_THROTTLE_S,
    EV_THRESHOLD_KW,
    GEENDATA_STOP_S,
    UPDATE_MINUTES,
    WATCH_FRESH_S,
    WATCH_RUNAWAY_W,
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
        self.ent_zd_inlim = o(CONF_ENT_ZD_INLIM)
        self.ent_zd_outlim = o(CONF_ENT_ZD_OUTLIM)
        self.adapter = o(CONF_ADAPTER)
        self.ent_gen_power = o(CONF_ENT_GEN_POWER)
        self.ent_gen_charge = o(CONF_ENT_GEN_CHARGE)
        self.ent_gen_discharge = o(CONF_ENT_GEN_DISCHARGE)
        self.ent_ms_mode = o(CONF_ENT_MS_MODE)
        self.ent_ms_charge = o(CONF_ENT_MS_CHARGE)
        self.ent_ms_discharge = o(CONF_ENT_MS_DISCHARGE)
        self.ent_bat_chg = o(CONF_ENT_BAT_CHG)
        self.ent_bat_dis = o(CONF_ENT_BAT_DIS)
        self.sell_threshold = float(o(CONF_SELL_THRESHOLD))

        # adapter-implementatie + capabilities; alle merk-specifieke kennis
        # leeft in adapters.py — de coordinator stuurt op self.caps
        self.adapter_impl = A.create_adapter(self.adapter, self)
        self.caps = self.adapter_impl.caps

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
        self.assist_enabled = False    # dynamisch bijspringen (aparte switch)
        self.sell_enabled = False      # verkopen boven drempelprijs (aparte switch)
        self.assist_active: str | None = None
        self._tripped: str | None = None   # None | "laden" | "ontladen" (richting van de runaway)
        self.reserve_kwh = 0.0
        self._cheap_future = 0.0
        self._max_future = 0.0
        self._assist_last = 0.0
        self.aggressiveness = "gebalanceerd"
        self.advies = "init"
        self.setpoint_w = 0.0
        self.plan_hours: list[dict] = []
        self.expected_saving = 0.0
        self.inputs: dict = {}
        self.plan_error: str | None = None
        self.watch_error: str | None = None
        self.last_applied: str | None = None
        self.reden: str = ""
        self.volgende_actie: str | None = None
        self.history: deque = deque(maxlen=50)
        self._had_success = False
        self._data_ok_at: datetime | None = None
        self._safe_stopped = False
        self._retry_cancel = None
        self._last_discharge_w = 0.0   # laatst gecommandeerd ontlaadvermogen (marstek/generic)
        self._dis_guard_last = 0.0
        self.listeners: list = []
        self.sensors: list = []

    # compat: het advies-sensor-attribuut 'fout' toont de ernstigste actuele fout
    @property
    def last_error(self) -> str | None:
        return self.watch_error or self.plan_error

    # ---------- lifecycle ----------
    async def async_start(self) -> None:
        self.listeners.append(async_track_time_interval(
            self.hass, self._tick, timedelta(minutes=UPDATE_MINUTES)))
        self.listeners.append(async_track_state_change_event(
            self.hass, [self.ent_wallbox_1, self.ent_wallbox_2], self._ev_guard))
        if self.ent_p1:
            self.listeners.append(async_track_state_change_event(
                self.hass, [self.ent_p1], self._assist_check))
            if not self.caps.p1_matching:
                # vast-setpoint-adapters: altijd-actieve guard verlaagt het
                # ontlaadvermogen zodra de huisvraag zakt
                self.listeners.append(async_track_state_change_event(
                    self.hass, [self.ent_p1], self._discharge_guard))
        await self._tick(None)

    async def async_stop(self) -> None:
        """Unload/reload: listeners weg, retry cancelen en de accu naar rust —
        anders blijft de laatste actieve stand ongecontroleerd doorlopen."""
        for remove in self.listeners:
            remove()
        self.listeners = []
        if self._retry_cancel is not None:
            self._retry_cancel()
            self._retry_cancel = None
        if self.control_enabled:
            try:
                await self._set_battery("rust", 0.0)
            except Exception:  # noqa: BLE001 - unload mag nooit blokkeren
                _LOGGER.exception("Wattson: accu naar rust bij unload faalde")

    # ---------- helpers (entity-IO gedeeld met adapters.py) ----------
    def _f(self, entity: str) -> float | None:
        return A.read_f(self.hass, entity)

    def _fresh(self, entity: str, max_age_s: float = WATCH_FRESH_S) -> float | None:
        """Als _f, maar alleen als de waarde recent is bijgewerkt (vers bewijs)."""
        return A.read_fresh(self.hass, entity, max_age_s, dt_util.utcnow())

    def _unit(self, entity: str) -> str:
        return A.unit_of(self.hass, entity)

    def _power_w(self, entity: str) -> float | None:
        return A.read_power_w(self.hass, entity)

    def _fresh_power_w(self, entity: str, max_age_s: float = WATCH_FRESH_S) -> float | None:
        return A.read_fresh_power_w(self.hass, entity, max_age_s, dt_util.utcnow())

    def _energy_kwh(self, entity: str) -> float | None:
        """Lees een energie-forecast als kWh; accepteert Wh, kWh en MWh."""
        value = self._f(entity)
        if value is None:
            return None
        unit = self._unit(entity)
        if unit == "wh":
            return value / 1000.0
        if unit == "mwh":
            return value * 1000.0
        return value

    @staticmethod
    def _price_eur_kwh(value: float, unit: str) -> float:
        """Normaliseer gangbare prijs-eenheden naar EUR/kWh."""
        unit = unit.replace(" ", "").lower()
        if "/mwh" in unit:
            return value / 1000.0
        if unit.startswith(("ct/", "c/")) or "cent/kwh" in unit:
            return value / 100.0
        return value

    def _current_price(self) -> float | None:
        value = self._f(self.ent_price)
        return None if value is None else self._price_eur_kwh(value, self._unit(self.ent_price))

    def _ev_charging(self) -> bool:
        w1 = self._power_w(self.ent_wallbox_1)
        w2 = self._power_w(self.ent_wallbox_2)
        threshold_w = EV_THRESHOLD_KW * 1000.0
        return (w1 or 0.0) > threshold_w or (w2 or 0.0) > threshold_w

    def _bat_flow_entities(self) -> tuple[str, str]:
        """(laad-, ontlaad-)telemetrie-entiteit voor de actieve adapter."""
        return self.adapter_impl.telemetry_entities()

    def _price_forecast(self) -> list[tuple[datetime, float]]:
        """Uur-forecast uit het forecast-attribuut van de prijs-sensor.

        Ondersteunde contracten per forecast-item:
        - Zonneplan: {"datetime": iso, "electricity_price": prijs x 1e7}
        - generiek:  {"datetime"|"start"|"from": iso, "price"|"value": €/kWh}
        Begint de forecast pas bij het volgende uur, dan wordt het huidige uur
        aangevuld met de actuele sensorwaarde — anders zou het setpoint van
        het volgende uur nu al uitgevoerd worden.
        """
        st = self.hass.states.get(self.ent_price)
        if st is None:
            return []
        now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        out = []
        for item in st.attributes.get("forecast", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                t = item.get("datetime") or item.get("start") or item.get("from")
                dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                if item.get("electricity_price") is not None:
                    price = float(item["electricity_price"]) / 1e7  # zonneplan-schaal
                else:
                    raw = item.get("price", item.get("value"))
                    if raw is None:
                        continue
                    price = self._price_eur_kwh(float(raw), self._unit(self.ent_price))
            except (KeyError, ValueError, TypeError):
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= now:
                out.append((dt, price))
        out.sort(key=lambda x: x[0])
        cur = self._current_price()
        if not out:
            if cur is not None:
                out = [(now, cur)]
        elif out[0][0] > now and cur is not None:
            # forecast begint pas volgend uur: huidig uur expliciet toevoegen
            out.insert(0, (now, cur))
        return out

    def _pv_curve(self, hours: list[datetime]) -> dict[datetime, float]:
        """Verdeel de PV-forecast dagtotalen over een daglicht-bel (W per uur)."""
        remain = (self._energy_kwh(self.ent_pv_remain) or 0.0) * self.pv_bias
        tomorrow = (self._energy_kwh(self.ent_pv_tomorrow) or 0.0) * self.pv_bias
        lo, hi = DAGLICHT
        # HA-tijdzone, niet de host-OS-tijdzone (docker draait vaak op UTC)
        today = dt_util.now().date()

        def bell(h):  # gewicht per lokaal uur
            if h < lo or h >= hi:
                return 0.0
            return math.sin((h - lo) / (hi - lo) * math.pi) ** 2

        out = {}
        for day, budget in ((today, remain), (today + timedelta(days=1), tomorrow)):
            day_hours = [dt for dt in hours if dt_util.as_local(dt).date() == day]
            weights = [bell(dt_util.as_local(dt).hour) for dt in day_hours]
            tot = sum(weights)
            for dt, w in zip(day_hours, weights):
                out[dt] = (budget * w / tot * 1000.0) if tot > 0 else 0.0
        # het huidige uur weten we beter dan de bel
        pv_now = self._power_w(self.ent_pv_now)
        if hours and pv_now is not None:
            out[hours[0]] = pv_now * self.pv_bias
        return out

    # ---------- kern ----------
    async def _tick(self, _now) -> None:
        prev = (self.advies, self.last_applied)
        try:
            await self._watchdog()
            await self._plan_and_apply()
            self.plan_error = None
        except Exception as err:  # noqa: BLE001 - watchdog: nooit crashen, wel loggen
            self.plan_error = str(err)
            _LOGGER.exception("Wattson-tick faalde")
            if self.control_enabled:
                await self._set_battery("rust", 0.0)  # veilige stand
        await self._stale_guard()
        # zolang er nog geen geslaagd plan is (bronnen traag na herstart):
        # niet 5 minuten wachten maar elke 45 s opnieuw proberen
        if self.advies == "geen data" and not self._had_success:
            if self._retry_cancel is None:
                self._retry_cancel = async_call_later(self.hass, 45, self._retry)
        elif self.advies != "geen data":
            self._had_success = True
        self._log_decision(prev)
        for s in self.sensors:
            s.async_write_ha_state()

    def _expected_direction(self) -> tuple[bool, bool]:
        """(laden verwacht, ontladen verwacht) op basis van het actuele advies."""
        chg = self.advies in ("laden", "bijspringen: laden")
        dis = self.advies in ("ontladen", "verkopen", "bijspringen: ontladen")
        return chg, dis

    async def _watchdog(self) -> None:
        """Luistert-niet-detector: meet het werkelijke accuvermogen tegen wat
        wij gecommandeerd hebben; bij grove afwijking -> noodstop + melding.

        Werkt uitsluitend op VERSE meetwaarden en op de telemetrie van de
        ACTIEVE adapter. De noodstop loopt via de adapter-router; bij Zendure
        gaat daarbovenop de limiet van de foute richting dicht (laden -> input,
        ontladen -> output) — een apparaat-commando dat ook aankomt als de
        manager-select al 'off' staat. Opheffen gebeurt alleen op vers bewijs
        dat het vermogen weer laag is, en zet limieten NIET terug open: de
        eerstvolgende echte actie opent zelf de limiet die hij nodig heeft.
        """
        if not self.control_enabled:
            return
        ent_chg, ent_dis = self._bat_flow_entities()
        dis = self._fresh_power_w(ent_dis)
        chg = self._fresh_power_w(ent_chg)
        if dis is None and chg is None:
            return  # geen vers bewijs: geen oordeel
        verwacht_chg, verwacht_dis = self._expected_direction()
        afwijking = None
        richting = None
        if chg is not None and chg > WATCH_RUNAWAY_W and not verwacht_chg:
            afwijking = f"accu laadt {chg:.0f} W terwijl '{self.advies}' gecommandeerd is"
            richting = "laden"
        elif dis is not None and dis > WATCH_RUNAWAY_W and not verwacht_dis:
            afwijking = f"accu ontlaadt {dis:.0f} W terwijl '{self.advies}' gecommandeerd is"
            richting = "ontladen"
        elif dis is not None and dis > self.params.p_discharge_max_w + 500:
            afwijking = f"ontlaadvermogen {dis:.0f} W ver boven limiet"
            richting = "ontladen"
        if afwijking:
            # eerst registreren en melden, dan pas stoppen: ook als het
            # stop-commando faalt is de ingreep zichtbaar
            self._tripped = richting
            self.assist_active = None
            self.watch_error = f"WATCHDOG: {afwijking}"
            _LOGGER.warning("Wattson watchdog: %s", afwijking)
            self.hass.bus.async_fire("logbook_entry", {
                "name": "Wattson", "message": f"WATCHDOG ingegrepen: {afwijking}",
                "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})
            await self._emergency_stop(richting)
        elif self._tripped:
            # alleen opheffen op vers bewijs dat de runaway-richting stil ligt
            gestopt = (self._tripped == "laden" and chg is not None and chg < 50) or (
                self._tripped == "ontladen" and dis is not None and dis < 50)
            if gestopt:
                self._tripped = None
                self.watch_error = None
                self.hass.bus.async_fire("logbook_entry", {
                    "name": "Wattson", "message": "WATCHDOG opgeheven, sturing hervat",
                    "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})

    async def _stale_guard(self) -> None:
        """Telemetrie te lang stil terwijl sturing aan staat -> eenmalig veilig stoppen.

        Zonder verse SoC/vermogens is elke actieve stand blind vertrouwen op de
        laatste opdracht; na GEENDATA_STOP_S gaat de accu naar rust met dichte
        limieten tot er weer data is.
        """
        if not self.control_enabled:
            return
        ent_chg, ent_dis = self._bat_flow_entities()
        vers = (
            self._fresh(self.ent_soc, GEENDATA_STOP_S) is not None
            or self._fresh_power_w(ent_chg, GEENDATA_STOP_S) is not None
            or self._fresh_power_w(ent_dis, GEENDATA_STOP_S) is not None
        )
        now = dt_util.utcnow()
        if vers:
            self._data_ok_at = now
            self._safe_stopped = False
            return
        if self._data_ok_at is None:
            self._data_ok_at = now
            return
        if not self._safe_stopped and (now - self._data_ok_at).total_seconds() > GEENDATA_STOP_S:
            self._safe_stopped = True
            self.assist_active = None
            await self._emergency_stop(None)
            self.reden = "telemetrie stil — veilig gestopt"
            self.hass.bus.async_fire("logbook_entry", {
                "name": "Wattson",
                "message": f"telemetrie > {GEENDATA_STOP_S / 60:.0f} min stil: accu veilig gestopt",
                "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})

    async def _emergency_stop(self, richting: str | None) -> None:
        """Veilige stop, adapter-onafhankelijk.

        Altijd eerst 'rust' via de adapter (dat commando bestaat op elk merk);
        daarna mag de adapter extra maatregelen nemen (bv. apparaat-limieten
        van de foute richting dichtzetten als hij die heeft).
        """
        try:
            await self._set_battery("rust", 0.0)
        except Exception:  # noqa: BLE001 - noodstop mag nooit zelf crashen
            _LOGGER.exception("Wattson: noodstop via adapter faalde")
        try:
            await self.adapter_impl.emergency_stop(richting)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Wattson: adapter-noodstopmaatregelen faalden")

    async def _retry(self, _now) -> None:
        self._retry_cancel = None
        await self._tick(None)

    def _log_decision(self, prev) -> None:
        """Historie bijhouden en logbook-regel schrijven bij een wijziging."""
        now = dt_util.now().strftime("%Y-%m-%d %H:%M")
        if (self.advies, self.last_applied) == prev:
            return
        self.history.appendleft({
            "tijd": now,
            "advies": self.advies,
            "setpoint_w": self.setpoint_w,
            "gestuurd": self.last_applied if self.control_enabled else "schaduw",
            "reden": self.reden,
        })
        try:
            self.hass.bus.async_fire("logbook_entry", {
                "name": "Wattson",
                "message": f"{self.advies} ({self.setpoint_w:+.0f} W) — {self.reden}"
                           + ("" if self.control_enabled else " [schaduw]"),
                "entity_id": "sensor.wattson_advies",
                "domain": "wattson_ems",
            })
        except Exception:  # noqa: BLE001 - logbook is nice-to-have
            pass

    def _sell_ok(self, price: float) -> bool:
        """Mag dit uur boven de huisvraag uit verkocht worden?"""
        return self.sell_enabled and (price - self.wedge) >= self.sell_threshold

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
            loc = dt_util.as_local(dt)
            weekend = loc.weekday() >= 5
            load = self.profile.get((loc.hour, int(weekend)), 0.35) * 1000.0
            if k == 0:
                p1 = self._power_w(self.ent_p1)
                # De twee bronnen kunnen twee laders zijn, maar ook wallbox +
                # voertuigtelemetrie van dezelfde sessie; max voorkomt dubbel tellen.
                wb_w = max(
                    self._power_w(self.ent_wallbox_1) or 0.0,
                    self._power_w(self.ent_wallbox_2) or 0.0,
                )
                if p1 is not None:
                    # actuele huisvraag excl. EV én excl. het eigen accuvermogen
                    # (anders ziet de planner zijn eigen laden als huislast)
                    ent_chg, ent_dis = self._bat_flow_entities()
                    b_chg = self._power_w(ent_chg) or 0.0
                    b_dis = self._power_w(ent_dis) or 0.0
                    load = max(p1 - wb_w - b_chg + b_dis + (pv.get(dt, 0.0)), 0.0)
            steps.append(P.Step(
                price_imp=price,
                price_exp=max(price - self.wedge, -0.5),
                load_w=load,
                pv_w=pv.get(dt, 0.0),
                ev_charging=ev_now if k == 0 else False,
                sell_ok=self._sell_ok(price),
            ))

        tv = P.terminal_value_from_prices([s.price_imp for s in steps], self.params)
        self.inputs = {
            "soc_kwh": round(soc, 2),
            "prijs_nu": round(steps[0].price_imp, 4),
            "huislast_nu_w": round(steps[0].load_w),
            "pv_nu_w": round(steps[0].pv_w),
            "pv_rest_vandaag_kwh": round((self._energy_kwh(self.ent_pv_remain) or 0.0) * self.pv_bias, 1),
            "pv_morgen_kwh": round((self._energy_kwh(self.ent_pv_tomorrow) or 0.0) * self.pv_bias, 1),
            "ev_laadt": ev_now,
            "horizon_uren": len(steps),
            "eindwaarde_restlading": round(tv, 3),
            "verkoop_drempel": self.sell_threshold if self.sell_enabled else None,
        }
        setpoints, cost = P.plan(steps, soc, self.params, terminal_value=tv)
        prices_only = [s2.price_imp for s2 in steps]
        self._cheap_future = min(prices_only)
        self._max_future = max(prices_only)
        # reserve: wat het plan later nog wil ontladen (accu-zijde kWh);
        # bijspringen mag alleen boven deze reserve
        eta = P.eta_oneway(self.params.p_discharge_max_w, self.params) or 1.0
        self.reserve_kwh = sum(-sp for sp in setpoints[1:] if sp < 0) / 1000.0 / eta
        # verwacht planvoordeel: kosten van niets-doen minus plan-kosten, over
        # dezelfde horizon en symmetrisch verrekend — beide paden starten op de
        # actuele SoC en krijgen dezelfde eindwaarde voor restlading (plan()
        # trekt die zelf al af; niets-doen behoudt de volledige startlading)
        base = 0.0
        for s in steps:
            c, _, _, _ = P.hour_result(s, 0.0, soc, self.params)
            base += c
        base -= (soc - self.params.soc_min_kwh) * tv
        self.expected_saving = round(base - cost, 2)

        sp = setpoints[0]
        self.setpoint_w = round(sp)
        self.plan_hours = []
        soc_sim = soc
        for (dt, pr), a, st in list(zip(prices, setpoints, steps))[:16]:
            _, soc_sim, _, _ = P.hour_result(st, a, soc_sim, self.params)
            self.plan_hours.append({
                "tijd": dt_util.as_local(dt).strftime("%H:%M"),
                "prijs": round(pr, 3),
                "setpoint_w": round(a),
                "soc_na_kwh": round(soc_sim, 2),
                "verwachte_last_w": round(st.load_w),
                "verwachte_pv_w": round(st.pv_w),
            })
        # volgende geplande actie (voor het "waarom wacht hij"-inzicht)
        nxt = next((p for p in self.plan_hours if abs(p["setpoint_w"]) > 50), None)
        if sp > 50:
            self.advies = "laden"
            self.reden = f"goedkoop uur (€{steps[0].price_imp:.3f})"
            self.volgende_actie = None
        elif sp < -50:
            # ontladen boven de actuele netto-huisvraag kan alleen verkopen zijn
            net_home = max(steps[0].load_w - steps[0].pv_w, 0.0)
            if steps[0].sell_ok and -sp > net_home + 100:
                self.advies = "verkopen"
                self.reden = (f"verkoopprijs €{steps[0].price_imp - self.wedge:.3f} "
                              f">= drempel €{self.sell_threshold:.2f}")
            else:
                self.advies = "ontladen"
                self.reden = f"duur uur (€{steps[0].price_imp:.3f}), huis vraagt {steps[0].load_w:.0f} W"
            self.volgende_actie = None
        else:
            self.advies = "rust"
            if nxt and nxt is not self.plan_hours[0]:
                act = "laden" if nxt["setpoint_w"] > 0 else "ontladen"
                self.reden = f"wacht: {act} om {nxt['tijd']} (€{nxt['prijs']:.3f})"
                self.volgende_actie = f"{act} om {nxt['tijd']} ({nxt['setpoint_w']:+d} W)"
            else:
                self.reden = "spread te klein binnen de horizon"
                self.volgende_actie = None

        if not self.control_enabled or self._tripped:
            return
        if self.ent_zd_hems:
            hems = self.hass.states.get(self.ent_zd_hems)
            if hems is not None and hems.state == "on":
                # de eigen AI-modus is nog de baas: niet dubbel sturen
                self.last_applied = "geblokkeerd: accu-AI (HEMS) staat nog aan"
                return
        if self.advies == "laden":
            self.assist_active = None
            await self._set_battery("laden", abs(self.setpoint_w))
        elif self.advies == "verkopen" and not ev_now:
            self.assist_active = None
            await self._set_battery("verkopen", abs(self.setpoint_w))
        elif self.advies == "ontladen" and not ev_now:
            self.assist_active = None
            await self._set_battery("ontladen", abs(self.setpoint_w))
        elif self.assist_active:
            pass  # bijspringen loopt; de P1-listener beheert dit
        else:
            await self._set_battery("rust", 0.0)

    async def _set_battery(self, action: str, power_w: float, *, p1_cap: bool = True) -> None:
        """Stuur de accu via de adapter (laden/ontladen/verkopen/rust).

        p1_cap=False slaat de momentane P1-begrenzing over — voor de
        discharge-guard, die zelf al een lager (delta-gebaseerd) vermogen
        heeft berekend en niet nogmaals gecapt moet worden.
        """
        if action == "laden_overschot" and not self.caps.surplus_mode:
            action = "laden"  # geen native overschot-modus op dit merk
        if action not in ("ontladen", "verkopen"):
            self._last_discharge_w = 0.0
        applied = await self.adapter_impl.apply(action, power_w, p1_cap=p1_cap)
        if action == "ontladen" and not self.caps.p1_matching:
            # vast-setpoint-adapters: onthoud het werkelijk gecommandeerde
            # vermogen voor de discharge-guard
            self._last_discharge_w = applied

    @callback
    def _discharge_guard(self, _event) -> None:
        """Altijd actieve exportbewaking voor marstek/generic (throttled).

        Op die adapters is het ontlaad-setpoint een vást vermogen dat tot de
        volgende plan-tick blijft staan; zakt de huisvraag intussen, dan zou
        de accu exporteren. P1 is inclusief het accu-effect, dus het maximaal
        toegestane ontlaadvermogen = huidig setpoint + P1. De guard verlaagt
        alléén (verhogen doet de volgende tick), en staat los van bijspringen.
        """
        if self.caps.p1_matching or not self.control_enabled:
            return
        if self._last_discharge_w <= 0 or self.advies not in ("ontladen", "bijspringen: ontladen"):
            return
        now = time.monotonic()
        if now - self._dis_guard_last < DIS_GUARD_THROTTLE_S:
            return
        p1 = self._fresh_power_w(self.ent_p1, 60)
        if p1 is None or p1 >= 0:
            return  # geen export: niets te verlagen
        allowed = max(self._last_discharge_w + p1, 0.0)
        if allowed < self._last_discharge_w - DIS_GUARD_DEADBAND_W:
            self._dis_guard_last = now
            self.hass.async_create_task(self._discharge_guard_apply(allowed))

    async def _discharge_guard_apply(self, allowed_w: float) -> None:
        prev_w = self._last_discharge_w
        await self._set_battery("ontladen", allowed_w, p1_cap=False)
        _LOGGER.debug("Wattson discharge-guard: ontladen %0.f -> %.0f W", prev_w, allowed_w)
        for s in self.sensors:
            s.async_write_ha_state()

    @callback
    def _assist_check(self, _event) -> None:
        """Realtime laag: bijspringen op pieken en zonoverschot (throttled)."""
        if not (self.control_enabled and self.assist_enabled):
            return
        if self.advies not in ("rust", "rust (EV-guard)") and not self.assist_active:
            return
        now = time.monotonic()
        if now - self._assist_last < ASSIST_THROTTLE_S:
            return
        self._assist_last = now
        self.hass.async_create_task(self._assist_apply())

    async def _assist_apply(self) -> None:
        await self._watchdog()
        if self._tripped:
            return
        p1 = self._fresh_power_w(self.ent_p1, 120)
        soc_pct = self._f(self.ent_soc)
        prijs = self._current_price()
        if p1 is None or soc_pct is None or prijs is None:
            return
        soc = soc_pct / 100.0 * self.params.capacity_kwh
        vrij = soc - self.params.soc_min_kwh - self.reserve_kwh - ASSIST_SOC_MARGE_KWH
        eta_rt = P.eta_oneway(800.0, self.params) ** 2
        prev = (self.advies, self.last_applied)

        if self.assist_active == "ontladen" and (p1 < ASSIST_STOP_W or vrij <= 0 or self._ev_charging()):
            self.assist_active = None
            await self._set_battery("rust", 0.0)
            self.advies = "rust"
            self.reden = "bijspringen klaar (piek voorbij of reserve bereikt)"
        elif self.assist_active == "laden" and p1 > -ASSIST_STOP_W:
            self.assist_active = None
            await self._set_battery("rust", 0.0)
            self.advies = "rust"
            self.reden = "bijspringen klaar (overschot voorbij)"
        elif (self.assist_active in (None, "ontladen") and p1 > ASSIST_IMPORT_W
              and not self._ev_charging() and vrij > 0
              and prijs > self._cheap_future / max(eta_rt, 0.5)):
            self.assist_active = "ontladen"
            await self._set_battery("ontladen", min(p1, self.params.p_discharge_max_w))
            self.advies = "bijspringen: ontladen"
            self.reden = f"piek {p1:.0f} W, prijs €{prijs:.3f} > herlaadprijs"
        elif (self.assist_active in (None, "laden") and p1 < -ASSIST_EXPORT_W
              and soc < self.params.soc_max_kwh - 0.2
              and self._max_future * eta_rt > prijs):
            self.assist_active = "laden"
            await self._set_battery("laden_overschot", min(-p1, self.params.p_charge_max_w))
            self.advies = "bijspringen: laden"
            self.reden = f"zonoverschot {-p1:.0f} W, piek later €{self._max_future:.3f}"
        else:
            return
        self._log_decision(prev)
        for s in self.sensors:
            s.async_write_ha_state()

    @callback
    def _ev_guard(self, _event) -> None:
        """Auto begint te laden -> ontladen/verkopen direct stoppen."""
        if self.control_enabled and self._ev_charging() and (
                self.advies in ("ontladen", "verkopen") or self.assist_active == "ontladen"):
            self.assist_active = None
            self.hass.async_create_task(self._set_battery("rust", 0.0))
            self.advies = "rust (EV-guard)"
            for s in self.sensors:
                s.async_write_ha_state()
