"""Wattson-coordinator: orkestreert plannen, sturen en bewaken.

Schaduwmodus (sturing uit) publiceert alleen het advies; met de master-switch
aan wordt het eerste planuur uitgevoerd:
- laden      -> operation 'manual' + manual_power = -W  (negatief = laden)
- ontladen   -> operation 'manual' + manual_power = +W; Wattson volgt de P1
                event-gedreven en remt direct terug bij export
- verkopen   -> operation 'manual' + manual_power = +W  (vast vermogen,
                exporteert boven de huisvraag; alleen boven de drempelprijs
                en alleen met de verkoop-switch aan)
- rust       -> operation 'off'

De domeinlogica leeft in componenten (adapter-patroon: ref naar deze klasse):
  telemetry.Telemetry       entity-IO (W/kWh/€ lezen en normaliseren)
  ev.EvMonitor              wallboxen + thuis-gates + EV-guard
  forecast.LoadProfile/PvCurve   vraag- en PV-voorspelling
  scenario.PriceScenario    exportprijs incl. saldering-overgang 1-1-2027
  budget.PlanBudgets        reserve / zonbudget / voorzien restant / floors
  safety.Safety             watchdog + stale-guard + trip-status
  realtime.*                volglus, exportbewaking, exportherstel, assist
De coordinator zelf doet alleen: opties/wiring, de plan-tick (DP + advies +
demping + uitvoeren) en het gedeelde stuur-boekhoudinkje (set_battery).
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from datetime import timedelta

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
    CONF_ENT_ZD_CHG,
    CONF_ENT_ZD_DIS,
    CONF_ENT_ZD_ACMODE,
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
    DWELL_OVERRIDE_EUR,
    EV_HOUSE_MIN_W,
    EV_SUSPECT_JUMP_W,
    EV_THRESHOLD_KW,
    PLAN_MIN_DWELL_S,
    SWITCH_DEADBAND_EUR,
    TRACK_INTERVAL_S,
    UPDATE_MINUTES,
    WATCH_INTERVAL_S,
)
from .ev import EvMonitor
from .forecast import LoadProfile, PvCurve
from .realtime import (
    AssistController,
    DischargeGuard,
    ExportRecovery,
    TrackController,
)
from .safety import Safety
from .scenario import PriceScenario
from .telemetry import Telemetry
from .values import PlanValues

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
        self.ent_wallbox_1_home = o(CONF_ENT_WALLBOX_1_HOME)
        self.ent_wallbox_2_home = o(CONF_ENT_WALLBOX_2_HOME)
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
        self.ent_zd_acmode = o(CONF_ENT_ZD_ACMODE)
        self.adapter = o(CONF_ADAPTER)
        self.ent_gen_power = o(CONF_ENT_GEN_POWER)
        self.ent_gen_charge = o(CONF_ENT_GEN_CHARGE)
        self.ent_gen_discharge = o(CONF_ENT_GEN_DISCHARGE)
        self.ent_ms_mode = o(CONF_ENT_MS_MODE)
        self.ent_ms_charge = o(CONF_ENT_MS_CHARGE)
        self.ent_ms_discharge = o(CONF_ENT_MS_DISCHARGE)
        self.ent_bat_chg = o(CONF_ENT_BAT_CHG)
        self.ent_bat_dis = o(CONF_ENT_BAT_DIS)

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
            eta_nom=b["eta_nom"], p_fix_w=b["p_fix_w"],
            standby_w=b.get("standby_w", 0.0), deg_cost=cfg["deg_cost"],
            risk_k=cfg.get("risk_k", 0.0),
            risk_steps=tuple(cfg.get("risk_shape_steps", ())),
        )
        self.pv_bias = cfg["pv_bias"]
        self.trained_at = cfg["trained_at"]

        # ---------- componenten ----------
        self.t = Telemetry(hass, self.ent_price)
        self.ev = EvMonitor(self.t, [
            (self.ent_wallbox_1, self.ent_wallbox_1_home),
            (self.ent_wallbox_2, self.ent_wallbox_2_home),
        ])
        self.load_profile = LoadProfile(
            {tuple(int(x) for x in k.split("|")): v for k, v in cfg["load_profile"].items()})
        self.pv = PvCurve(self.t, self.ent_pv_now, self.ent_pv_remain,
                          self.ent_pv_tomorrow, self.pv_bias)
        self.scenario = PriceScenario(
            wedge_saldering=cfg["wedge"],
            wedge_post=float(o(CONF_WEDGE_POST)),
        )
        self.values = PlanValues(self.params)
        self.safety = Safety(self)
        self.track = TrackController(self)
        self.discharge_guard = DischargeGuard(self)
        self.export_recovery = ExportRecovery(self)
        self.assist = AssistController(self)

        # ---------- gedeelde staat ----------
        self.control_enabled = False   # master-switch (RestoreEntity zet dit terug)
        self.assist_enabled = False    # dynamisch bijspringen (aparte switch)
        self.sell_enabled = False      # verkopen boven drempelprijs (aparte switch)
        self.assist_active: str | None = None
        self.aggressiveness = "gebalanceerd"
        self.advies = "init"
        self.setpoint_w = 0.0
        self.plan_hours: list[dict] = []
        self.expected_saving = 0.0
        self.inputs: dict = {}
        self.plan_error: str | None = None
        self.last_applied: str | None = None
        self.reden: str = ""
        self.volgende_actie: str | None = None
        self.history: deque = deque(maxlen=50)
        self._had_success = False
        self._retry_cancel = None
        self._last_action: str | None = None
        self._last_charge_w = 0.0      # laatst werkelijk toegepast laadvermogen
        self._last_discharge_w = 0.0   # laatst werkelijk toegepast ontlaadvermogen
        self._switch_debt = 0.0        # opgeteld gemist voordeel van gedempte modewissels
        self._last_mode_switch = 0.0
        self._last_load_w: float | None = None  # huisvraag vorige tick (EV-sprong-detectie)
        self.listeners: list = []
        self.sensors: list = []

    # ---------- compat / delegaties ----------
    @property
    def last_error(self) -> str | None:
        """Het advies-sensor-attribuut 'fout' toont de ernstigste actuele fout."""
        return self.safety.watch_error or self.plan_error

    @property
    def watch_error(self) -> str | None:
        return self.safety.watch_error

    # adapters.py en de contract-tests spreken de coordinator aan op deze
    # namen; ze delegeren naar de componenten waar de logica nu leeft
    @property
    def _tripped(self) -> str | None:
        return self.safety.tripped

    @_tripped.setter
    def _tripped(self, value: str | None) -> None:
        self.safety.tripped = value

    def _f(self, entity: str) -> float | None:
        return self.t.f(entity)

    def _power_w(self, entity: str) -> float | None:
        return self.t.power_w(entity)

    def _fresh_power_w(self, entity: str, max_age_s: float | None = None) -> float | None:
        if max_age_s is None:
            return self.t.fresh_power_w(entity)
        return self.t.fresh_power_w(entity, max_age_s)

    def bat_flow_entities(self) -> tuple[str, str]:
        """(laad-, ontlaad-)telemetrie-entiteit voor de actieve adapter."""
        return self.adapter_impl.telemetry_entities()

    def write_entities(self) -> None:
        for s in self.sensors:
            s.async_write_ha_state()

    # ---------- lifecycle ----------
    async def async_start(self) -> None:
        self.listeners.append(async_track_time_interval(
            self.hass, self._tick, timedelta(minutes=UPDATE_MINUTES)))
        # veiligheidsbewaking los van de (tragere) plan-tick: runaway- en
        # stilte-detectie mogen niet wachten op het her-plan-interval
        self.listeners.append(async_track_time_interval(
            self.hass, self._safety_tick, timedelta(seconds=WATCH_INTERVAL_S)))
        # snelle volglus: stuurwaarden bijregelen op de gemeten vraag
        self.listeners.append(async_track_time_interval(
            self.hass, self.track.tick, timedelta(seconds=TRACK_INTERVAL_S)))
        # uurgrens-replan: prijzen wisselen op de klok, het 10-min-raster niet.
        # Zodra de prijssensor een nieuwe waarde meldt direct herplannen, zodat
        # een piekontlading om 20:00 start en niet pas op de eerstvolgende tick.
        if self.ent_price:
            self.listeners.append(async_track_state_change_event(
                self.hass, [self.ent_price], self._price_changed))
        ev_entities = self.ev.entities()
        if ev_entities:
            self.listeners.append(async_track_state_change_event(
                self.hass, ev_entities, self._ev_guard))
        if self.ent_p1:
            ent_chg, ent_dis = self.bat_flow_entities()
            assist_entities = list(dict.fromkeys(filter(None, (
                self.ent_p1, self.ent_soc, ent_chg, ent_dis,
            ))))
            self.listeners.append(async_track_state_change_event(
                self.hass, assist_entities, self.assist.check))
            # snelle volg-laag: ruimte geven zodra de meter een piek toont
            self.listeners.append(async_track_state_change_event(
                self.hass, [self.ent_p1], self.track.fast))
            if not self.caps.p1_matching:
                # vast-setpoint-adapters: altijd-actieve guard verlaagt het
                # ontlaadvermogen zodra de huisvraag zakt
                self.listeners.append(async_track_state_change_event(
                    self.hass, [self.ent_p1], self.discharge_guard.check))
            if self.caps.surplus_mode:
                # Sterke, bevestigde bronexport tijdens (ook al naar 0 W
                # teruggeregeld) ontladen mag niet tot de volgende plan-/stop-
                # timer in manual blijven hangen: promoveer naar surplusladen.
                self.listeners.append(async_track_state_change_event(
                    self.hass, [self.ent_p1], self.export_recovery.check))
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
                await self.set_battery("rust", 0.0)
            except Exception:  # noqa: BLE001 - unload mag nooit blokkeren
                _LOGGER.exception("Wattson: accu naar rust bij unload faalde")

    @callback
    def _price_changed(self, event) -> None:
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if old is None or new is None or old.state == new.state:
            return  # attribuut-updates en herhalingen zijn geen nieuw uur
        self.hass.async_create_task(self._tick(None))

    @callback
    def _ev_guard(self, event) -> None:
        self.ev.guard(self, event)

    async def _safety_tick(self, _now) -> None:
        await self.safety.tick()

    # ---------- kern ----------
    async def _tick(self, _now) -> None:
        prev = (self.advies, self.last_applied)
        try:
            await self.safety.watchdog()
            await self._plan_and_apply()
            self.plan_error = None
        except Exception as err:  # noqa: BLE001 - watchdog: nooit crashen, wel loggen
            self.plan_error = str(err)
            _LOGGER.exception("Wattson-tick faalde")
            if self.control_enabled:
                await self.set_battery("rust", 0.0)  # veilige stand
        await self.safety.stale_guard()
        # zolang er nog geen geslaagd plan is (bronnen traag na herstart):
        # niet 5 minuten wachten maar elke 45 s opnieuw proberen
        if self.advies == "geen data" and not self._had_success:
            if self._retry_cancel is None:
                self._retry_cancel = async_call_later(self.hass, 45, self._retry)
        elif self.advies != "geen data":
            self._had_success = True
        self.log_decision(prev)
        self.write_entities()

    async def _retry(self, _now) -> None:
        self._retry_cancel = None
        await self._tick(None)

    async def emergency_stop(self, richting: str | None) -> None:
        """Veilige stop, adapter-onafhankelijk.

        Altijd eerst 'rust' via de adapter (dat commando bestaat op elk merk);
        daarna mag de adapter extra maatregelen nemen (bv. apparaat-limieten
        van de foute richting dichtzetten als hij die heeft).
        """
        try:
            await self.set_battery("rust", 0.0)
        except Exception:  # noqa: BLE001 - noodstop mag nooit zelf crashen
            _LOGGER.exception("Wattson: noodstop via adapter faalde")
        try:
            await self.adapter_impl.emergency_stop(richting)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Wattson: adapter-noodstopmaatregelen faalden")

    def log_decision(self, prev) -> None:
        """Historie bijhouden en logbook-regel schrijven bij een wijziging.

        Alleen échte modus-wissels tellen: een vermogens-bijstelling binnen
        dezelfde modus (de volglus zet "manual (+230 W)" -> "manual (+362 W)"
        elke ~30 s bij pulserende last) spamde de 50-regel-historie vol en
        drukte de werkelijke beslissingen eruit (nacht 15->16 juli: alle 50
        regels waren volg-stapjes)."""
        now = dt_util.now().strftime("%Y-%m-%d %H:%M")

        def key(state):
            advies, applied = state
            return (advies, (applied or "").split(" (")[0])

        if key((self.advies, self.last_applied)) == key(prev):
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

    async def _plan_and_apply(self) -> None:
        prev_advies = self.advies
        prev_setpoint = self.setpoint_w
        assist_reason = self.reden if self.assist_active else None
        prices = self.t.price_forecast()
        soc_pct = self.t.f(self.ent_soc)
        if not prices or soc_pct is None:
            self.advies = "geen data"
            self.setpoint_w = 0.0
            return
        soc = soc_pct / 100.0 * self.params.capacity_kwh
        hours = [dt for dt, _ in prices]
        pv = self.pv.curve(hours)
        today = dt_util.now().date()
        wedge = self.scenario.wedge(today)
        ev_now = self.ev.charging()
        # v1.9: de harde EV-blokkade in de planner (hour_result: p=0) geldt
        # alleen nog als de wallbox-telemetrie NIET vers is — dan kan load_w
        # vervuild zijn met autovermogen en zou de net_home-cap de auto voeden.
        # Met verse wallbox-meting is load_w betrouwbaar EV-gecorrigeerd en mag
        # het plan het huisdeel bedienen (de apply-laag begrenst de
        # output-limiet daarbovenop nogmaals op huislast).
        wb_fresh_w = self.ev.max_w(180)
        ev_blind = ev_now and wb_fresh_w <= EV_THRESHOLD_KW * 1000.0

        steps = []
        p1_now = None
        b_chg_now = 0.0
        b_dis_now = 0.0
        for k, (dt, price) in enumerate(prices):
            load = self.load_profile.expected_w(dt)
            if k == 0:
                p1 = self.t.power_w(self.ent_p1)
                p1_now = p1
                # De twee bronnen kunnen twee laders zijn, maar ook wallbox +
                # voertuigtelemetrie van dezelfde sessie; max voorkomt dubbel tellen.
                wb_w = self.ev.max_w()
                if p1 is not None:
                    # actuele huisvraag excl. EV én excl. het eigen accuvermogen
                    # (anders ziet de planner zijn eigen laden als huislast)
                    ent_chg, ent_dis = self.bat_flow_entities()
                    b_chg = self.t.fresh_power_w(ent_chg, 120)
                    b_dis = self.t.fresh_power_w(ent_dis, 120)
                    # Zonder meettelemetrie kan een exact vast commando alsnog
                    # worden teruggenomen. Native P1-matching gebruikt een
                    # variabel vermogen; daarvoor is geen commando-fallback
                    # mogelijk en blijft verse telemetrie vereist.
                    if b_chg is None:
                        b_chg = self._last_charge_w if self._last_action == "laden" else 0.0
                    if b_dis is None:
                        fixed_dis = self._last_action == "verkopen" or not self.caps.p1_matching
                        b_dis = self._last_discharge_w if fixed_dis else 0.0
                    b_chg_now = b_chg
                    b_dis_now = b_dis
                    load = max(A.p1_without_battery(
                        p1, charge_w=b_chg, discharge_w=b_dis
                    ) - wb_w + (pv.get(dt, 0.0)), 0.0)
            steps.append(P.Step(
                price_imp=price,
                price_exp=self.scenario.export_price(price, today),
                load_w=load,
                pv_w=pv.get(dt, 0.0),
                ev_charging=ev_blind if k == 0 else False,
                # verkopen is een gebruikers-constraint (switch), geen
                # prijsdrempel: óf het loont beslist de DP per uur zelf
                sell_ok=self.sell_enabled,
            ))

        tv = P.terminal_value_from_prices([s.price_imp for s in steps], self.params)
        self.inputs = {
            "soc_kwh": round(soc, 2),
            "soc_pct": round(soc_pct, 1),
            "prijs_nu": round(steps[0].price_imp, 4),
            "p1_nu_w": None if p1_now is None else round(p1_now),
            "huislast_nu_w": round(steps[0].load_w),
            "pv_nu_w": round(steps[0].pv_w),
            "accu_laden_w": round(b_chg_now),
            "accu_ontladen_w": round(b_dis_now),
            "pv_rest_vandaag_kwh": round(self.pv.remain_kwh(), 1),
            "pv_morgen_kwh": round(self.pv.tomorrow_kwh(), 1),
            "ev_laadt": ev_now,
            "horizon_uren": len(steps),
            "eindwaarde_restlading": round(tv, 3),
            "verkopen_actief": self.sell_enabled,
            "voorkeur_zelfvoorziening_eur_kwh": self.params.alpha,
            "scenario": self.scenario.label(today),
        }
        warning = self.scenario.transition_warning(today)
        if warning:
            self.inputs["scenario_waarschuwing"] = warning
        setpoints, cost, lam = P.plan_with_values(
            steps, soc, self.params, terminal_value=tv)
        self.values.compute(steps, setpoints, soc, tv, lam)
        self.inputs.update(self.values.as_inputs(soc))
        # verwacht planvoordeel in KASGELD: het plan is geoptimaliseerd met de
        # voorkeursprijzen (alpha/beta), maar de €-sensor rapporteert wat het
        # werkelijk scheelt — beide paden gesimuleerd met kas-params, zelfde
        # horizon, zelfde eindwaarde voor restlading
        cash = P.Params(**self.params.to_dict())
        cash.alpha = 0.0
        cash.beta = 0.0
        cash.risk_k = 0.0
        base = 0.0
        cost_cash = 0.0
        soc_b = soc_c = min(max(soc, cash.soc_min_kwh), cash.soc_max_kwh)
        for s, a in zip(steps, setpoints):
            c0, soc_b, _, _ = P.hour_result(s, 0.0, soc_b, cash)
            c1, soc_c, _, _ = P.hour_result(s, a, soc_c, cash)
            base += c0
            cost_cash += c1
        base -= (soc_b - cash.soc_min_kwh) * tv
        cost_cash -= (soc_c - cash.soc_min_kwh) * tv
        self.expected_saving = round(base - cost_cash, 2)

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
                self.reden = (f"exportprijs €{steps[0].price_imp - wedge:.3f} "
                              f"> waarde van bewaren "
                              f"(λ €{self.values.lam_now(soc):.3f}/kWh)")
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

        # wissel-demping: een marginale modewissel (rust <-> laden/ontladen)
        # gaat pas door als het cumulatieve voordeel de drempel overschrijdt.
        # Het voordeel wordt exact bepaald met een extra DP-run waarin het
        # eerste uur wordt vastgezet op de huidige stand; zo stopt het
        # pendelen rond break-even zonder echte marge weg te geven.
        # verkopen hoort er sinds v3 bij: zonder drempel is het een gewone
        # DP-uitkomst en pendelde hij vrij tussen verkopen/laden/rust bij
        # bijna-gelijke waardes (2026-07-15 20:00-20:32: 4 wissels in 32 min)
        stickable = ("rust", "laden", "ontladen", "verkopen")
        if (prev_advies in stickable and self.advies in stickable
                and self.advies != prev_advies and not ev_now and len(steps) > 1):
            forced = 0.0 if prev_advies == "rust" else prev_setpoint
            c0, soc1, _, _ = P.hour_result(steps[0], forced, soc, self.params)
            _, rest = P.plan(steps[1:], soc1, self.params, terminal_value=tv)
            voordeel = max((c0 + rest) - cost, 0.0)
            # Een oude laad/ontlaadstand die door SoC-, PV- of lastgrenzen
            # fysiek niets meer kan doen, mag nooit door de euro-demping blijven
            # hangen. Rust (of de nieuwe uitvoerbare richting) volgt direct.
            stale_active_mode = (
                prev_advies != "rust"
                and not P.action_is_effective(steps[0], forced, soc, self.params)
            )
            if stale_active_mode:
                self._switch_debt = 0.0
                self._last_mode_switch = time.monotonic()
            else:
                self._switch_debt += voordeel
            dwelling = time.monotonic() - self._last_mode_switch < PLAN_MIN_DWELL_S
            if stale_active_mode:
                pass
            elif self._switch_debt < SWITCH_DEADBAND_EUR:
                self.reden = (f"houdt {prev_advies} vast — wissel naar {self.advies} "
                              f"levert cumulatief €{self._switch_debt:.3f} op "
                              f"(drempel €{SWITCH_DEADBAND_EUR:.2f})")
                self.advies = prev_advies
                self.setpoint_w = 0.0 if prev_advies == "rust" else prev_setpoint
            elif dwelling and voordeel < DWELL_OVERRIDE_EUR:
                # vlakke prijzen laten de DP elke tick van gedachten wisselen;
                # net gewisseld + klein voordeel per tick = relais met rust
                # laten. Een echte piek (voordeel >= override) gaat wél door.
                self.reden = (f"houdt {prev_advies} vast — wisselde "
                              f"<{PLAN_MIN_DWELL_S // 60} min geleden en "
                              f"€{voordeel:.3f}/tick blijft onder de "
                              f"override €{DWELL_OVERRIDE_EUR:.2f}")
                self.advies = prev_advies
                self.setpoint_w = 0.0 if prev_advies == "rust" else prev_setpoint
            else:
                self._switch_debt = 0.0
                self._last_mode_switch = time.monotonic()
        else:
            self._switch_debt = 0.0

        # verdachte lastsprong: huisvraag springt hard omhoog zonder dat een
        # wallbox het bevestigt -> mogelijk een EV-start met achterlopende
        # vermogenstelemetrie; één tick niet ontladen tot er duidelijkheid is
        vorige_last = self._last_load_w
        self._last_load_w = steps[0].load_w
        if (self.advies in ("ontladen", "verkopen") and not ev_now
                and vorige_last is not None
                and steps[0].load_w - vorige_last > EV_SUSPECT_JUMP_W):
            self.advies = "rust (EV-check)"
            self.setpoint_w = 0.0
            self.reden = (f"lastsprong +{steps[0].load_w - vorige_last:.0f} W zonder "
                          "wallbox-bevestiging — één tick wachten op EV-telemetrie")

        if not self.control_enabled or self.safety.tripped:
            return
        if self.ent_zd_hems:
            hems = self.hass.states.get(self.ent_zd_hems)
            if hems is not None and hems.state == "on":
                # de eigen AI-modus is nog de baas: niet dubbel sturen
                self.last_applied = "geblokkeerd: accu-AI (HEMS) staat nog aan"
                return
        self.ev.house_share_active = False
        if self.advies == "laden":
            self.assist_active = None
            # is er nú meer PV-overschot dan het geplande laadvermogen, gebruik
            # dan native surplus-matching: die pakt het hele overschot (gratis,
            # volgt wolken vanzelf) i.p.v. een vast setpoint dat de rest laat
            # wegexporteren. Demotie terug naar vast (net-)laden alleen op
            # aangehouden bewijs: het gemeten laadvermogen moet het plan een
            # heel venster niet gedragen hebben (track.surplus_carried) — een
            # wolkgat precies op het tick-moment flipte anders elke tick de
            # modus heen en weer (14:35-incident: demotie + 23 s later promotie).
            surplus_w = max(steps[0].pv_w - steps[0].load_w, 0.0)
            blijf_overschot = (self._last_action == "laden_overschot"
                               and self.track.surplus_carried(abs(self.setpoint_w)))
            if self.caps.surplus_mode and (
                    surplus_w >= abs(self.setpoint_w) or blijf_overschot):
                await self.set_battery("laden_overschot", abs(self.setpoint_w))
            else:
                await self.set_battery("laden", abs(self.setpoint_w))
        elif self.advies == "verkopen" and not ev_now:
            self.assist_active = None
            await self.set_battery("verkopen", abs(self.setpoint_w))
        elif self.advies == "ontladen" and not ev_now:
            self.assist_active = None
            await self.set_battery("ontladen", abs(self.setpoint_w))
        elif self.advies == "ontladen" and ev_now:
            # v1.9: EV laadt — de accu dekt alléén het huisdeel. De wallbox
            # meet zijn eigen vermogen, dus de EV-gecorrigeerde huislast
            # (steps[0].load_w) is betrouwbaar. Een vast manual-setpoint op
            # die huislast levert precies dat deel zonder de auto mee te
            # voeden. Voorwaarde: verse wallbox-telemetrie — anders het oude
            # veilige gedrag (rust).
            self.assist_active = None
            wb_fresh = self.ev.sum_fresh_w(180)
            huis_w = min(max(steps[0].load_w, 0.0), self.params.p_discharge_max_w)
            if wb_fresh > EV_THRESHOLD_KW * 1000.0 and huis_w >= EV_HOUSE_MIN_W:
                # BEWUST het "verkopen"-pad: dat omzeilt de normale P1-cap,
                # zodat exact de EV-gecorrigeerde huislast als vast vermogen
                # wordt gezet. Hertaxatie volgt iedere plantick.
                await self.set_battery("verkopen", huis_w)
                self.ev.house_share_active = True
                self.setpoint_w = -round(huis_w)
                self.reden = f"EV laadt — accu dekt alleen het huisdeel ({huis_w:.0f} W, vast)"
            else:
                await self.set_battery("rust", 0.0)
                self.setpoint_w = 0.0
                self.reden = ("EV laadt — wallbox-telemetrie niet vers, accu rust"
                              if wb_fresh <= EV_THRESHOLD_KW * 1000.0
                              else f"EV laadt — huislast te klein ({huis_w:.0f} W), accu rust")
        elif self.assist_active:
            # Het uurplan adviseert rust, maar de realtime-laag loopt nog. Houd
            # advies/setpoint daarmee in lijn: anders kan de volgende watchdog
            # de door Wattson zelf gestuurde activiteit als runaway beoordelen.
            self.advies = f"bijspringen: {self.assist_active}"
            if self.assist_active == "laden":
                self.setpoint_w = round(self._last_charge_w)
            else:
                self.setpoint_w = -round(self._last_discharge_w)
            if assist_reason:
                self.reden = assist_reason
        else:
            await self.set_battery("rust", 0.0)

    async def set_battery(self, action: str, power_w: float, *, p1_cap: bool = True) -> None:
        """Stuur de accu via de adapter (laden/ontladen/verkopen/rust).

        p1_cap=False slaat de momentane P1-begrenzing over — voor de
        discharge-guard, die zelf al een lager (delta-gebaseerd) vermogen
        heeft berekend en niet nogmaals gecapt moet worden.
        """
        if action == "laden_overschot" and not self.caps.surplus_mode:
            action = "laden"  # geen native overschot-modus op dit merk
        applied = await self.adapter_impl.apply(action, power_w, p1_cap=p1_cap)
        # eigen stop geregistreerd: het apparaat loopt (cloud-latentie) nog even
        # uit in de oude richting — de watchdog mag dat geen runaway noemen
        if action == "rust" and self._last_action in (
                "laden", "laden_overschot", "ontladen", "verkopen"):
            self.safety.note_own_stop(
                "laden" if self._last_action in ("laden", "laden_overschot") else "ontladen")
        self._last_action = action
        self._last_charge_w = applied if action in ("laden", "laden_overschot") else 0.0
        self._last_discharge_w = applied if action in ("ontladen", "verkopen") else 0.0
        self.export_recovery.note_action(action)
        self.track.note_applied(action, applied)

    # oude naam: switch.py en externe scripts spreken deze nog aan
    _set_battery = set_battery
