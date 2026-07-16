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

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import replace
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
from . import planning_service as PS
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
    CONF_ENT_EXPORT_TOTALS,
    CONF_ENT_IMPORT_TOTALS,
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
from .netting import NettingMonitor
from .control import (
    AdviceMode,
    BatteryAction,
    BatteryCommand,
    CommandArbiter,
    CommandSource,
    Decision,
)
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
        self.netting = NettingMonitor(
            hass, list(o(CONF_ENT_IMPORT_TOTALS) or []),
            list(o(CONF_ENT_EXPORT_TOTALS) or []))
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
        self._decision = Decision(AdviceMode.INIT)
        self.plan_hours: list[dict] = []
        self.expected_saving = 0.0
        self.inputs: dict = {}
        self.plan_error: str | None = None
        self.last_applied: str | None = None
        self.history: deque = deque(maxlen=50)
        self._had_success = False
        self._retry_cancel = None
        self._last_action: BatteryAction | None = None
        self._last_charge_w = 0.0      # laatst werkelijk toegepast laadvermogen
        self._last_discharge_w = 0.0   # laatst werkelijk toegepast ontlaadvermogen
        self._switch_debt = 0.0        # opgeteld gemist voordeel van gedempte modewissels
        self._last_mode_switch = 0.0
        self._last_load_w: float | None = None  # huisvraag vorige tick (EV-sprong-detectie)
        self.command_arbiter = CommandArbiter()
        self._tick_lock = asyncio.Lock()
        self._replan_pending = False
        self.listeners: list = []
        self.sensors: list = []

    # ---------- compat / delegaties ----------
    @property
    def decision(self) -> Decision:
        return self._decision

    @property
    def mode(self) -> AdviceMode:
        return self._decision.mode

    def set_decision(self, decision: Decision) -> None:
        self._decision = decision

    @property
    def advies(self) -> str:
        """Bestaand UI-contract; interne logica gebruikt ``mode``."""
        return self._decision.mode.value

    @advies.setter
    def advies(self, value: AdviceMode | str) -> None:
        self._decision = replace(self._decision, mode=AdviceMode.parse(value))

    @property
    def setpoint_w(self) -> float:
        return self._decision.setpoint_w

    @setpoint_w.setter
    def setpoint_w(self, value: float) -> None:
        self._decision = replace(self._decision, setpoint_w=float(value))

    @property
    def reden(self) -> str:
        return self._decision.reason

    @reden.setter
    def reden(self, value: str) -> None:
        self._decision = replace(self._decision, reason=value)

    @property
    def volgende_actie(self) -> str | None:
        return self._decision.next_action

    @volgende_actie.setter
    def volgende_actie(self, value: str | None) -> None:
        self._decision = replace(self._decision, next_action=value)

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
                self.command_arbiter.invalidate_pending()
                await self.set_battery(
                    BatteryAction.IDLE,
                    0.0,
                    source=CommandSource.LIFECYCLE,
                )
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
        """Serialiseer replans; een trigger tijdens een run vraagt één rerun."""
        if self._tick_lock.locked():
            self._replan_pending = True
            return
        async with self._tick_lock:
            while True:
                self._replan_pending = False
                await self._tick_once()
                if not self._replan_pending:
                    break

    async def _tick_once(self) -> None:
        prev = (self.advies, self.last_applied)
        try:
            await self.safety.watchdog()
            await self._plan_and_apply()
            self.plan_error = None
        except Exception as err:  # noqa: BLE001 - watchdog: nooit crashen, wel loggen
            self.plan_error = str(err)
            _LOGGER.exception("Wattson-tick faalde")
            if self.control_enabled:
                self.command_arbiter.invalidate_pending()
                await self.set_battery(
                    BatteryAction.IDLE, 0.0, source=CommandSource.SAFETY)
        await self.safety.stale_guard()
        # zolang er nog geen geslaagd plan is (bronnen traag na herstart):
        # niet 5 minuten wachten maar elke 45 s opnieuw proberen
        if self.mode is AdviceMode.NO_DATA and not self._had_success:
            if self._retry_cancel is None:
                self._retry_cancel = async_call_later(self.hass, 45, self._retry)
        elif self.mode is not AdviceMode.NO_DATA:
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
        self.command_arbiter.invalidate_pending()
        try:
            await self.set_battery(
                BatteryAction.IDLE, 0.0, source=CommandSource.SAFETY)
        except Exception:  # noqa: BLE001 - noodstop mag nooit zelf crashen
            _LOGGER.exception("Wattson: noodstop via adapter faalde")
        try:
            command = self.command_arbiter.command(
                BatteryAction.IDLE,
                0.0,
                p1_cap=False,
                source=CommandSource.SAFETY,
            )

            async def apply_emergency(_command: BatteryCommand) -> float:
                await self.adapter_impl.emergency_stop(richting)
                return 0.0

            await self.command_arbiter.execute(command, apply_emergency)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Wattson: adapter-noodstopmaatregelen faalden")

    def log_decision(self, prev) -> None:
        """Historie bijhouden en logbook-regel schrijven bij een wijziging.

        Alleen échte modus-wissels tellen: vermogens-bijstellingen binnen
        dezelfde modus (volglus-stapjes elke ~30 s) zouden de 50-regel-
        historie volspammen en de werkelijke beslissingen eruit drukken."""
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
        previous = self.decision
        assist_reason = self.reden if self.assist_active else None
        context = await self._build_plan_context()
        if context is None:
            self.set_decision(Decision(AdviceMode.NO_DATA))
            return

        evaluation = PS.evaluate(
            context.steps,
            context.soc_kwh,
            self.params,
            context.terminal_value,
        )
        self._update_plan_outputs(context, evaluation)
        self.set_decision(PS.decision_from_plan(
            evaluation.setpoints[0],
            context.steps[0],
            context.wedge,
            self.values.lam_now(context.soc_kwh),
            self.plan_hours,
        ))
        self._stabilize_decision(previous, context, evaluation)
        self._guard_suspect_ev(context)
        await self._apply_decision(context, assist_reason)

    async def _build_plan_context(self) -> PS.PlanningContext | None:
        """Lees één consistente snapshot en bouw de DP-stappen."""
        prices = self.t.price_forecast()
        soc_pct = self.t.f(self.ent_soc)
        if not prices or soc_pct is None:
            return None
        soc = soc_pct / 100.0 * self.params.capacity_kwh
        hours = [dt for dt, _ in prices]
        pv = self.pv.curve(hours)
        today = dt_util.now().date()
        # jaarsaldering-positie verversen (throttled): raakt de netto-
        # importruimte op, dan schuift de wedge richting post-saldering
        await self.netting.refresh()
        self.scenario.netting_headroom_kwh = self.netting.headroom_kwh
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

        tv = P.terminal_value_from_prices(
            [step.price_imp for step in steps], self.params)
        return PS.PlanningContext(
            prices=prices,
            steps=steps,
            soc_kwh=soc,
            soc_pct=soc_pct,
            pv_w=pv,
            today=today,
            wedge=wedge,
            ev_now=ev_now,
            terminal_value=tv,
            p1_now_w=p1_now,
            battery_charge_w=b_chg_now,
            battery_discharge_w=b_dis_now,
        )

    def _update_plan_outputs(
        self,
        context: PS.PlanningContext,
        evaluation: PS.PlanEvaluation,
    ) -> None:
        """Publiceer rekenuitkomsten; verandert nog geen fysieke sturing."""
        steps = context.steps
        self.inputs = {
            "soc_kwh": round(context.soc_kwh, 2),
            "soc_pct": round(context.soc_pct, 1),
            "prijs_nu": round(steps[0].price_imp, 4),
            "p1_nu_w": (None if context.p1_now_w is None
                         else round(context.p1_now_w)),
            "huislast_nu_w": round(steps[0].load_w),
            "pv_nu_w": round(steps[0].pv_w),
            "accu_laden_w": round(context.battery_charge_w),
            "accu_ontladen_w": round(context.battery_discharge_w),
            "pv_rest_vandaag_kwh": round(self.pv.remain_kwh(), 1),
            "pv_morgen_kwh": round(self.pv.tomorrow_kwh(), 1),
            "ev_laadt": context.ev_now,
            "horizon_uren": len(steps),
            "eindwaarde_restlading": round(context.terminal_value, 3),
            "verkopen_actief": self.sell_enabled,
            "voorkeur_zelfvoorziening_eur_kwh": self.params.alpha,
            "scenario": self.scenario.label(context.today),
            "wedge_effectief": round(context.wedge, 3),
        }
        if self.netting.configured and self.netting.headroom_kwh is not None:
            self.inputs["salderingsruimte_kwh"] = round(self.netting.headroom_kwh)
        warning = self.scenario.transition_warning(context.today)
        if warning:
            self.inputs["scenario_waarschuwing"] = warning
        self.values.compute(
            steps,
            evaluation.setpoints,
            context.soc_kwh,
            context.terminal_value,
            evaluation.lambda_table,
        )
        self.inputs.update(self.values.as_inputs(context.soc_kwh))
        self.expected_saving = evaluation.expected_saving
        self.plan_hours = PS.plan_hours(
            context.prices,
            steps,
            evaluation.setpoints,
            context.soc_kwh,
            self.params,
            lambda when: dt_util.as_local(when).strftime("%H:%M"),
        )

    def _stabilize_decision(
        self,
        previous: Decision,
        context: PS.PlanningContext,
        evaluation: PS.PlanEvaluation,
    ) -> None:
        """Pas economische wisseldemping toe op een vers DP-besluit."""
        # wissel-demping: een marginale modewissel gaat pas door als het
        # cumulatieve voordeel de drempel overschrijdt. Het voordeel wordt
        # exact bepaald met een extra DP-run waarin het eerste uur wordt
        # vastgezet op de huidige stand; zo stopt het pendelen rond
        # break-even zonder echte marge weg te geven. verkopen telt mee:
        # het is een gewone DP-uitkomst en pendelt anders net zo hard.
        steps = context.steps
        stickable = {
            AdviceMode.IDLE,
            AdviceMode.CHARGE,
            AdviceMode.DISCHARGE,
            AdviceMode.SELL,
        }
        if (previous.mode in stickable and self.mode in stickable
                and self.mode is not previous.mode
                and not context.ev_now and len(steps) > 1):
            forced = 0.0 if previous.mode is AdviceMode.IDLE else previous.setpoint_w
            c0, soc1, _, _ = P.hour_result(
                steps[0], forced, context.soc_kwh, self.params)
            _, rest = P.plan(
                steps[1:], soc1, self.params,
                terminal_value=context.terminal_value)
            voordeel = max((c0 + rest) - evaluation.cost, 0.0)
            # Een oude laad/ontlaadstand die door SoC-, PV- of lastgrenzen
            # fysiek niets meer kan doen, mag nooit door de euro-demping blijven
            # hangen. Rust (of de nieuwe uitvoerbare richting) volgt direct.
            stale_active_mode = (
                previous.mode is not AdviceMode.IDLE
                and not P.action_is_effective(
                    steps[0], forced, context.soc_kwh, self.params)
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
                self.set_decision(replace(
                    self.decision,
                    mode=previous.mode,
                    setpoint_w=(0.0 if previous.mode is AdviceMode.IDLE
                                else previous.setpoint_w),
                    reason=(f"houdt {previous.mode.value} vast — wissel naar "
                            f"{self.mode.value} levert cumulatief "
                            f"€{self._switch_debt:.3f} op "
                            f"(drempel €{SWITCH_DEADBAND_EUR:.2f})"),
                ))
            elif dwelling and voordeel < DWELL_OVERRIDE_EUR:
                # vlakke prijzen laten de DP elke tick van gedachten wisselen;
                # net gewisseld + klein voordeel per tick = relais met rust
                # laten. Een echte piek (voordeel >= override) gaat wél door.
                self.set_decision(replace(
                    self.decision,
                    mode=previous.mode,
                    setpoint_w=(0.0 if previous.mode is AdviceMode.IDLE
                                else previous.setpoint_w),
                    reason=(f"houdt {previous.mode.value} vast — wisselde "
                            f"<{PLAN_MIN_DWELL_S // 60} min geleden en "
                            f"€{voordeel:.3f}/tick blijft onder de "
                            f"override €{DWELL_OVERRIDE_EUR:.2f}"),
                ))
            else:
                self._switch_debt = 0.0
                self._last_mode_switch = time.monotonic()
        else:
            self._switch_debt = 0.0

    def _guard_suspect_ev(self, context: PS.PlanningContext) -> None:
        """Parkeer één tick bij een onbevestigde, EV-achtige lastsprong."""
        # verdachte lastsprong: huisvraag springt hard omhoog zonder dat een
        # wallbox het bevestigt -> mogelijk een EV-start met achterlopende
        # vermogenstelemetrie; één tick niet ontladen tot er duidelijkheid is
        vorige_last = self._last_load_w
        load_w = context.steps[0].load_w
        self._last_load_w = load_w
        if (self.mode in (AdviceMode.DISCHARGE, AdviceMode.SELL)
                and not context.ev_now
                and vorige_last is not None
                and load_w - vorige_last > EV_SUSPECT_JUMP_W):
            self.set_decision(Decision(
                AdviceMode.EV_CHECK,
                reason=f"lastsprong +{load_w - vorige_last:.0f} W zonder "
                "wallbox-bevestiging — één tick wachten op EV-telemetrie",
            ))

    async def _apply_decision(
        self,
        context: PS.PlanningContext,
        assist_reason: str | None,
    ) -> None:
        """Voer één gestabiliseerd besluit uit via de command-arbiter."""
        if not self.control_enabled or self.safety.tripped:
            return
        if self.ent_zd_hems:
            hems = self.hass.states.get(self.ent_zd_hems)
            if hems is not None and hems.state == "on":
                # de eigen AI-modus is nog de baas: niet dubbel sturen
                self.last_applied = "geblokkeerd: accu-AI (HEMS) staat nog aan"
                return
        self.ev.house_share_active = False
        if self.mode is AdviceMode.CHARGE:
            self.assist_active = None
            # is er nú meer PV-overschot dan het geplande laadvermogen, gebruik
            # dan native surplus-matching: die pakt het hele overschot i.p.v.
            # een vast setpoint dat de rest laat wegexporteren. Demotie terug
            # naar vast netladen alleen op aangehouden bewijs over een heel
            # venster (track.surplus_carried) — anti-flapping bij wolkgaten.
            first_step = context.steps[0]
            surplus_w = max(first_step.pv_w - first_step.load_w, 0.0)
            blijf_overschot = (self._last_action == "laden_overschot"
                               and self.track.surplus_carried(abs(self.setpoint_w)))
            if self.caps.surplus_mode and (
                    surplus_w >= abs(self.setpoint_w) or blijf_overschot):
                await self.set_battery(
                    BatteryAction.SURPLUS_CHARGE, abs(self.setpoint_w))
            else:
                await self.set_battery(
                    BatteryAction.CHARGE, abs(self.setpoint_w))
        elif self.mode is AdviceMode.SELL and not context.ev_now:
            self.assist_active = None
            await self.set_battery(BatteryAction.SELL, abs(self.setpoint_w))
        elif self.mode is AdviceMode.DISCHARGE and not context.ev_now:
            self.assist_active = None
            await self.set_battery(BatteryAction.DISCHARGE, abs(self.setpoint_w))
        elif self.mode is AdviceMode.DISCHARGE and context.ev_now:
            # v1.9: EV laadt — de accu dekt alléén het huisdeel. De wallbox
            # meet zijn eigen vermogen, dus de EV-gecorrigeerde huislast
            # (steps[0].load_w) is betrouwbaar. Een vast manual-setpoint op
            # die huislast levert precies dat deel zonder de auto mee te
            # voeden. Voorwaarde: verse wallbox-telemetrie — anders het oude
            # veilige gedrag (rust).
            # advies 'ontladen' tijdens EV kan alleen met verse wallbox-
            # telemetrie ontstaan (anders zet ev_blind de planner op rust),
            # dus load_w is hier betrouwbaar EV-gecorrigeerd
            self.assist_active = None
            huis_w = min(
                max(context.steps[0].load_w, 0.0),
                self.params.p_discharge_max_w,
            )
            if huis_w >= EV_HOUSE_MIN_W:
                # BEWUST het "verkopen"-pad: dat omzeilt de normale P1-cap,
                # zodat exact de EV-gecorrigeerde huislast als vast vermogen
                # wordt gezet. Hertaxatie volgt iedere plantick.
                applied = await self.set_battery(BatteryAction.SELL, huis_w)
                if applied is None:
                    return
                self.ev.house_share_active = True
                self.set_decision(replace(
                    self.decision,
                    setpoint_w=-round(huis_w),
                    reason=(f"EV laadt — accu dekt alleen het huisdeel "
                            f"({huis_w:.0f} W, vast)"),
                ))
            else:
                await self.set_battery(BatteryAction.IDLE, 0.0)
                self.set_decision(Decision(
                    AdviceMode.IDLE,
                    reason=f"EV laadt — huislast te klein ({huis_w:.0f} W), accu rust",
                ))
        elif self.assist_active:
            # Het uurplan adviseert rust, maar de realtime-laag loopt nog. Houd
            # advies/setpoint daarmee in lijn: anders kan de volgende watchdog
            # de door Wattson zelf gestuurde activiteit als runaway beoordelen.
            if self.assist_active == "laden":
                mode = AdviceMode.ASSIST_CHARGE
                setpoint = round(self._last_charge_w)
            else:
                mode = AdviceMode.ASSIST_DISCHARGE
                setpoint = -round(self._last_discharge_w)
            self.set_decision(Decision(
                mode,
                setpoint,
                assist_reason or self.reden,
            ))
        else:
            await self.set_battery(BatteryAction.IDLE, 0.0)

    async def set_battery(
        self,
        action: BatteryAction | str,
        power_w: float,
        *,
        p1_cap: bool = True,
        source: CommandSource = CommandSource.PLANNER,
    ) -> float | None:
        """Stuur de accu via de adapter (laden/ontladen/verkopen/rust).

        p1_cap=False slaat de momentane P1-begrenzing over — voor de
        discharge-guard, die zelf al een lager (delta-gebaseerd) vermogen
        heeft berekend en niet nogmaals gecapt moet worden.
        """
        action = BatteryAction.parse(action)
        if action is BatteryAction.SURPLUS_CHARGE and not self.caps.surplus_mode:
            action = BatteryAction.CHARGE
        command = self.command_arbiter.command(
            action, power_w, p1_cap=p1_cap, source=source)
        result = await self.command_arbiter.execute(command, self._apply_command)
        if result.skipped:
            _LOGGER.debug(
                "Wattson: verouderd %s-commando overgeslagen (%s)",
                action.value, source.value,
            )
            return None
        applied = result.applied_w
        # eigen stop geregistreerd: het apparaat loopt (cloud-latentie) nog even
        # uit in de oude richting — de watchdog mag dat geen runaway noemen
        if action is BatteryAction.IDLE and self._last_action is not None:
            previous_action = BatteryAction.parse(self._last_action)
            self.safety.note_own_stop(
                "laden" if previous_action.is_charge else "ontladen")
        self._last_action = action
        self._last_charge_w = applied if action.is_charge else 0.0
        self._last_discharge_w = applied if action.is_discharge else 0.0
        self.export_recovery.note_action(action.value)
        self.track.note_applied(action.value, applied)
        return applied

    async def _apply_command(self, command: BatteryCommand) -> float:
        """Enige ongearbitreerde doorgang naar een merkadapter."""
        return await self.adapter_impl.apply(
            command.action.value,
            command.power_w,
            p1_cap=command.p1_cap,
        )

    async def set_discharge_limit(
        self,
        power_w: float,
        *,
        source: CommandSource = CommandSource.REALTIME,
    ) -> float | None:
        """Serialiseer een snelle adapter-specifieke matching-limietwrite."""
        command = self.command_arbiter.command(
            BatteryAction.DISCHARGE,
            power_w,
            p1_cap=False,
            source=source,
        )
        result = await self.command_arbiter.execute(
            command, self._apply_limit_command)
        return None if result.skipped else result.applied_w

    async def _apply_limit_command(self, command: BatteryCommand) -> float:
        return await self.adapter_impl.adjust_discharge_limit(command.power_w)
