"""Realtime-lagen bovenop het uurplan.

- TrackController : volgt de gemeten huisvraag (snel omhoog, lui omlaag)
- DischargeGuard  : exportrem voor vaste-setpoint-adapters (marstek/generic)
- ExportRecovery  : breekt vastgelopen ontladen af bij bevestigde bronexport
- AssistController: bijspringen op afwijkingen van de voorspelling, met
                    dezelfde beslisregel als het uurplan (marginale waarde λ)

Alle klassen volgen het adapter-patroon: ref naar de coordinator (c) voor
gedeelde staat (advies, setpoints, laatste actie) en de andere componenten.
"""
from __future__ import annotations

import logging
import time

from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from . import adapters as A
from .const import (
    ASSIST_EXPORT_W,
    ASSIST_IMPORT_W,
    ASSIST_MARGIN_EUR,
    ASSIST_MAX_SOC_MARGIN_KWH,
    ASSIST_MIN_RUN_S,
    ASSIST_POWER_DEADBAND_W,
    ASSIST_SOC_MARGE_KWH,
    ASSIST_STOP_GRACE_S,
    ASSIST_STOP_W,
    ASSIST_THROTTLE_S,
    DIS_GUARD_DEADBAND_W,
    DIS_GUARD_THROTTLE_S,
    DISCHARGE_EXPORT_ABORT_HOLD_S,
    DISCHARGE_EXPORT_ABORT_W,
    SETPOINT_ACK_DEADBAND_W,
    TRACK_DEADBAND_W,
    TRACK_FAST_THROTTLE_S,
    TRACK_LOWER_GRACE_S,
    TRACK_MARGE_W,
)

_LOGGER = logging.getLogger(__name__)


class TrackController:
    """Volgt de gemeten bronvraag tijdens ontladen; promoveert dal-laden
    naar surplus-matching zodra er meer bronoverschot is dan het setpoint."""

    def __init__(self, c) -> None:
        self.c = c
        self.tracked_outlim = 0.0     # laatst door de volglus geschreven outputlimiet
        self._fast_last = 0.0
        self._vraag_hist: list[tuple[float, float]] = []  # (t, vraag) voor terugneem-grace

    def note_applied(self, action: str, applied: float) -> None:
        if action == "ontladen" and self.c.caps.p1_matching:
            # referentie voor de volglus: dit is wat de adapter als limiet schreef
            self.tracked_outlim = applied

    # ---------- setpoint-feedback ----------
    def discharge_feedback(self) -> float | None:
        """Actueel fysiek ontlaadvermogen voor setpoint-bevestiging."""
        c = self.c
        if not c.caps.feedback_ack:
            return c._last_discharge_w
        _, ent_dis = c.bat_flow_entities()
        # Geen freshness-eis: een lang stabiel vermogen verandert in HA niet
        # altijd last_updated. Na een nieuw commando verschilt de oude waarde
        # vanzelf van het doel totdat echte telemetrie de wijziging bevestigt.
        return c.t.power_w(ent_dis)

    def discharge_command_settled(self) -> bool:
        c = self.c
        if not c.caps.feedback_ack:
            return True
        return A.setpoint_feedback_settled(
            c._last_discharge_w, self.discharge_feedback(), SETPOINT_ACK_DEADBAND_W)

    def discharge_target(self) -> float | None:
        """Gewenst ontlaadvermogen op basis van de gemeten bronvraag (of None)."""
        c = self.c
        if not c.control_enabled or c.safety.tripped or c.ev.charging():
            return None
        if c._last_action != "ontladen":
            return None
        if not self.discharge_command_settled():
            # P1 en accutelemetrie lopen bij Zendure enkele cycli uiteen. Een
            # nieuwe correctie vóór fysieke bevestiging combineert waarden van
            # twee verschillende setpoints en veroorzaakt import/export-pingpong.
            return None
        p1 = c.t.fresh_power_w(c.ent_p1, 90)
        if p1 is None:
            return None
        _, ent_dis = c.bat_flow_entities()
        dis = c.t.fresh_power_w(ent_dis)
        dis_now = dis if dis is not None else c._last_discharge_w
        return max(A.p1_without_battery(p1, discharge_w=dis_now), 0.0)

    # ---------- snelle lus (event-gedreven op P1) ----------
    @callback
    def fast(self, _event) -> None:
        """Ruimte geven zodra de meter een piek toont (event-gedreven, throttled).

        Staat de limiet/het setpoint onder de werkelijke vraag, dan komt dat
        verschil van het net. Dit is dus haastwerk: de P1-meter tikt elke ~1 s
        en een commando landt in ~0,2 s, dus de accu volgt binnen enkele
        seconden — net als de fabrikant-app. Terugnemen mag lui (tick):
        te veel ruimte kost niets, want matching exporteert niet en op vaste
        adapters remt de discharge-guard direct.
        """
        c = self.c
        vraag = self.discharge_target()
        if vraag is None:
            return
        now = time.monotonic()
        if now - self._fast_last < TRACK_FAST_THROTTLE_S:
            return
        if c.caps.p1_matching:
            doel = min(max(vraag + TRACK_MARGE_W, c.caps.min_setpoint_w),
                       c.params.p_discharge_max_w)
            if doel <= self.tracked_outlim + TRACK_DEADBAND_W:
                return  # alleen ophogen; verlagen doet de trage lus
        else:
            doel = min(vraag, c.params.p_discharge_max_w)
            if doel <= c._last_discharge_w + TRACK_DEADBAND_W:
                return
        self._fast_last = now
        c.hass.async_create_task(self._apply(doel))

    async def _apply(self, doel_w: float) -> None:
        c = self.c
        if c.caps.p1_matching:
            await A.set_power_number(c.hass, c.ent_zd_outlim, doel_w)
            self.tracked_outlim = doel_w
        else:
            await c.set_battery("ontladen", doel_w, p1_cap=False)

    # ---------- trage lus (interval) ----------
    async def tick(self, _now) -> None:
        """Trage volglus (elke TRACK_INTERVAL_S): ruimte terugnemen + promotie.

        - ontladen: limiet/setpoint zakt weer mee met een afnemende vraag,
          zodat het plan niet ongemerkt meer levert dan bedoeld;
        - laden (vast, dal-uur): verschijnt er intussen méér bronoverschot dan
          het setpoint, promoveer dan naar surplus-matching i.p.v. op de
          plan-tick te wachten.
        Ophogen gebeurt event-gedreven in fast().
        """
        c = self.c
        if not c.control_enabled or c.safety.tripped or c.ev.charging():
            return
        act = c._last_action
        if act == "ontladen":
            vraag = self.discharge_target()
            if vraag is None:
                return
            # Terugnemen op de PIEK-vraag van de afgelopen TRACK_LOWER_GRACE_S:
            # direct na matching leest P1 ~0 terwijl de ontlaadmeting (60s-poll)
            # achterloopt, waardoor de kale momentvraag tussen ~0 en de echte
            # huisvraag oscilleert. Elke limiet-write herstart het apparaat kort
            # (~40 s idle), dus zonder dit geheugen cyclet het ontladen continu.
            now_m = time.monotonic()
            self._vraag_hist.append((now_m, vraag))
            self._vraag_hist = [(t, v) for t, v in self._vraag_hist
                                if now_m - t <= TRACK_LOWER_GRACE_S]
            vraag_eff = max(v for _, v in self._vraag_hist)
            if c.caps.p1_matching:
                doel = min(max(vraag_eff + TRACK_MARGE_W, c.caps.min_setpoint_w),
                           c.params.p_discharge_max_w)
                if doel <= self.tracked_outlim - TRACK_DEADBAND_W:
                    await A.set_power_number(c.hass, c.ent_zd_outlim, doel)
                    self.tracked_outlim = doel
            else:
                doel = min(vraag_eff, c.params.p_discharge_max_w)
                if doel <= c._last_discharge_w - TRACK_DEADBAND_W:
                    await c.set_battery("ontladen", doel, p1_cap=False)
        elif act == "laden" and c.caps.surplus_mode:
            p1 = c.t.fresh_power_w(c.ent_p1, 90)
            if p1 is None:
                return
            ent_chg, _ = c.bat_flow_entities()
            chg = c.t.fresh_power_w(ent_chg)
            chg_now = chg if chg is not None else c._last_charge_w
            bron_export = -A.p1_without_battery(p1, charge_w=chg_now)
            if bron_export > c._last_charge_w + 300:
                await c.set_battery("laden_overschot", max(c._last_charge_w, 0.0))


class DischargeGuard:
    """Altijd actieve exportbewaking voor marstek/generic (throttled).

    Op die adapters is het ontlaad-setpoint een vást vermogen dat tot de
    volgende plan-tick blijft staan; zakt de huisvraag intussen, dan zou
    de accu exporteren. P1 is inclusief het accu-effect, dus het maximaal
    toegestane ontlaadvermogen = huidig setpoint + P1. De guard verlaagt
    alléén (verhogen doet de volgende tick), en staat los van bijspringen.
    """

    def __init__(self, c) -> None:
        self.c = c
        self._last = 0.0

    @callback
    def check(self, _event) -> None:
        c = self.c
        if c.caps.p1_matching or not c.control_enabled:
            return
        if c._last_discharge_w <= 0 or c.advies not in ("ontladen", "bijspringen: ontladen"):
            return
        now = time.monotonic()
        if now - self._last < DIS_GUARD_THROTTLE_S:
            return
        p1 = c.t.fresh_power_w(c.ent_p1, 60)
        if p1 is None or p1 >= 0:
            return  # geen export: niets te verlagen
        measured = c.track.discharge_feedback()
        if (c.caps.feedback_ack and measured is not None
                and not c.track.discharge_command_settled()):
            return  # vorige correctie is nog onderweg; wacht op fysieke ack
        # Bij ontbrekende feedback mag exportveiligheid nog steeds verlagen;
        # verhogen blijft via discharge_target geblokkeerd tot telemetrie er is.
        current_dis = c._last_discharge_w if measured is None else measured
        allowed = max(current_dis + p1, 0.0)
        if allowed < c._last_discharge_w - DIS_GUARD_DEADBAND_W:
            self._last = now
            c.hass.async_create_task(self._apply(allowed))

    async def _apply(self, allowed_w: float) -> None:
        c = self.c
        prev_w = c._last_discharge_w
        await c.set_battery("ontladen", allowed_w, p1_cap=False)
        _LOGGER.debug("Wattson discharge-guard: ontladen %0.f -> %.0f W", prev_w, allowed_w)
        c.write_entities()


class ExportRecovery:
    """Herstel uit manual-ontladen als echte bronexport blijft staan.

    De berekening is bewust conservatief: ook het volledige gecommandeerde
    ontlaadvermogen wordt bij P1 teruggeteld als de fysieke telemetrie nog
    achterloopt. Alleen export die dán nog overblijft kan niet door de accu
    zelf zijn veroorzaakt. Een korte hold voorkomt reageren op regelruis.
    """

    def __init__(self, c) -> None:
        self.c = c
        self.since: float | None = None
        self.pending = False

    def note_action(self, action: str) -> None:
        if action != "ontladen":
            self.since = None

    @callback
    def check(self, _event) -> None:
        c = self.c
        active = (
            c.control_enabled
            and not c.safety.tripped
            and c.caps.surplus_mode
            and c._last_action == "ontladen"
            and c.advies in ("ontladen", "bijspringen: ontladen")
        )
        if not active:
            self.since = None
            return
        p1 = c.t.fresh_power_w(c.ent_p1, 60)
        if p1 is None:
            self.since = None
            return
        source_p1 = A.conservative_source_p1(
            p1, c._last_discharge_w, c.track.discharge_feedback())
        now = time.monotonic()
        self.since, ready = A.export_recovery_state(
            source_p1,
            threshold_w=DISCHARGE_EXPORT_ABORT_W,
            now_s=now,
            since_s=self.since,
            hold_s=DISCHARGE_EXPORT_ABORT_HOLD_S,
        )
        if ready and not self.pending:
            self.pending = True
            c.hass.async_create_task(self._apply())

    async def _apply(self) -> None:
        """Stop vastgelopen ontladen en promoveer bruikbare export naar laden."""
        c = self.c
        try:
            if not (c.control_enabled and not c.safety.tripped
                    and c.caps.surplus_mode and c._last_action == "ontladen"):
                return
            p1 = c.t.fresh_power_w(c.ent_p1, 60)
            if p1 is None:
                return
            source_p1 = A.conservative_source_p1(
                p1, c._last_discharge_w, c.track.discharge_feedback())
            if source_p1 > -DISCHARGE_EXPORT_ABORT_W:
                return  # export verdween tussen callback en service-call

            prev = (c.advies, c.last_applied)
            soc_pct = c.t.f(c.ent_soc)
            prijs = c.t.current_price()
            soc = (soc_pct / 100.0 * c.params.capacity_kwh
                   if soc_pct is not None else None)
            can_store = (soc is not None
                         and soc < c.params.soc_max_kwh - ASSIST_MAX_SOC_MARGIN_KWH)
            charge_economic = False
            if soc is not None and prijs is not None:
                # zelfde afweging als het plan: opslaan loont als de
                # misgelopen export onder het laadplafond (λ) blijft
                exportprijs = c.scenario.export_price(prijs, dt_util.now().date())
                charge_economic = (exportprijs - c.params.beta
                                   < c.values.charge_ceiling(soc) - ASSIST_MARGIN_EUR)

            # Eerst expliciet rust: daarmee sluit de uitrichting en krijgt de
            # watchdog stop-grace voor eventuele fysieke uitloop. Daarna pas
            # de tegengestelde richting openen.
            c.assist_active = None
            c.assist.end_since = None
            await c.set_battery("rust", 0.0)
            if can_store and charge_economic:
                target = min(max(-source_p1, c.caps.min_setpoint_w),
                             c.params.p_charge_max_w)
                await c.set_battery("laden_overschot", target)
                c.assist_active = "laden"
                c.assist.started = time.monotonic()
                c.advies = "bijspringen: laden"
                c.setpoint_w = round(c._last_charge_w)
                c.reden = (f"sterke bronexport {-source_p1:.0f} W bevestigd — "
                           "ontladen afgebroken en overschotladen hervat")
            else:
                c.advies = "rust"
                c.setpoint_w = 0.0
                waarom = "accu vrijwel vol" if not can_store else "opslaan niet economisch"
                c.reden = (f"sterke bronexport {-source_p1:.0f} W bevestigd — "
                           f"ontladen afgebroken ({waarom})")
            c.log_decision(prev)
            c.write_entities()
        finally:
            self.since = None
            self.pending = False


class AssistController:
    """Realtime bijspringen op afwijkingen van de voorspelling.

    Eén beslisregel, dezelfde als het uurplan: vergelijk de actuele
    voorkeursprijs met de marginale waarde van de accu-inhoud (λ).
    - onverwachte importpiek: dekken zodra prijs + alpha > ontlaadvloer(SoC)
    - onverwacht zonoverschot: opslaan zodra exportprijs - beta < laadplafond
    De oude budget-heuristieken (planreserve, frontrun, zon-gedekt, gestrand
    restant) zijn hier speciale gevallen van: zakt de lading dan stijgt λ en
    stopt ontladen vanzelf (reserve); eindigt het plan met surplus dan zakt λ
    naar de restwaarde en mag elke redelijke piek bediend worden (restant);
    komt er meer zon aan dan er ruimte is dan zakt λ naar de exportprijs en
    is opslaan tegen actuele import altijd goed (zon-gedekt). Geplande handel
    (incl. verkopen boven de huisvraag) doet het uurplan zelf; deze laag
    reageert alleen op wat de voorspelling niet zag.
    """

    def __init__(self, c) -> None:
        self.c = c
        self._last = 0.0
        self.started = 0.0
        self.end_since: float | None = None

    @callback
    def check(self, _event) -> None:
        """Realtime laag: bijspringen op pieken en zonoverschot (throttled)."""
        c = self.c
        if not (c.control_enabled and c.assist_enabled):
            return
        if c.advies not in ("rust", "rust (EV-guard)") and not c.assist_active:
            return
        now = time.monotonic()
        if now - self._last < ASSIST_THROTTLE_S:
            return
        self._last = now
        c.hass.async_create_task(self.apply())

    def source_p1(self, p1_w: float) -> float | None:
        """P1 zonder het effect van de lopende realtime-assist.

        Bij native matching is het commando slechts een limiet en niet het
        werkelijke vermogen. Zonder verse accutelemetrie kan Wattson dan niet
        bewijzen dat de bronpiek/het bronoverschot voorbij is; None voorkomt
        dat een succesvol naar nul geregelde P1 als stopbewijs wordt gebruikt.
        Vaste adapters kunnen terugvallen op het werkelijk toegepaste setpoint.
        """
        c = self.c
        ent_chg, ent_dis = c.bat_flow_entities()
        if c.assist_active == "laden":
            measured = c.t.fresh_power_w(ent_chg, 120)
            if measured is not None:
                return A.p1_without_battery(p1_w, charge_w=measured)
            if c._last_action == "laden":  # fixed fallback (generic/marstek)
                return A.p1_without_battery(p1_w, charge_w=c._last_charge_w)
            return None
        if c.assist_active == "ontladen":
            measured = c.t.fresh_power_w(ent_dis, 120)
            if measured is not None:
                return A.p1_without_battery(p1_w, discharge_w=measured)
            if not c.caps.p1_matching:
                return A.p1_without_battery(p1_w, discharge_w=c._last_discharge_w)
            return None
        return p1_w

    async def apply(self) -> None:
        c = self.c
        v = c.values
        await c.safety.watchdog()
        if c.safety.tripped or c.export_recovery.pending:
            return
        p1 = c.t.fresh_power_w(c.ent_p1, 120)
        soc_pct = c.t.f(c.ent_soc)
        prijs = c.t.current_price()
        if p1 is None or soc_pct is None or prijs is None:
            return
        soc = soc_pct / 100.0 * c.params.capacity_kwh
        vrij_dis = soc - c.params.soc_min_kwh - ASSIST_SOC_MARGE_KWH
        # de beslisregel: dezelfde voorkeursprijzen als de DP. Import
        # verdringen is prijs + alpha waard; overschot opslaan kost de
        # misgelopen export (exportprijs - beta). λ levert per actuele SoC
        # de grens — de kleine marge is hysterese tegen randgeflipper.
        exportprijs = c.scenario.export_price(prijs, dt_util.now().date())
        dek_waarde = prijs + c.params.alpha
        floor = v.discharge_floor(soc)
        ceil = v.charge_ceiling(soc)
        discharge_worth = dek_waarde > floor + ASSIST_MARGIN_EUR
        charge_worth = exportprijs - c.params.beta < ceil - ASSIST_MARGIN_EUR
        prev = (c.advies, c.last_applied)
        source_p1 = self.source_p1(p1)

        peak_ended = source_p1 is not None and source_p1 < ASSIST_STOP_W
        surplus_ended = source_p1 is not None and source_p1 > -ASSIST_STOP_W
        # Opwarmvenster: direct na de start regelt native matching P1 al naar
        # ~0 terwijl het gemeten accuvermogen nog een verse "0" van vóór de
        # start leest (60s-poll, write-on-change). source_p1 rekent het
        # accuvermogen dan niet terug en "voorbij" is vals — geen stopbewijs.
        # Harde stops (SoC vol, reserve, EV, prijsconditie) blijven gelden.
        if c.assist_active and time.monotonic() - self.started < ASSIST_MIN_RUN_S:
            peak_ended = surplus_ended = False
        # Stop-dwell: "voorbij" moet ASSIST_STOP_GRACE_S aanhouden voordat we
        # echt stoppen. Wolk-dips en apparaat-eigen pauzes rond de drempel
        # cyclen anders het relais (~4-5 min aan / 25 s uit); in de grace
        # moduleert native matching zelf mee, dus dit kost geen netstroom.
        ended_now = peak_ended if c.assist_active == "ontladen" else surplus_ended
        if c.assist_active:
            if ended_now:
                if self.end_since is None:
                    self.end_since = time.monotonic()
            else:
                self.end_since = None
            held = (self.end_since is not None
                    and time.monotonic() - self.end_since >= ASSIST_STOP_GRACE_S)
            peak_ended = held if c.assist_active == "ontladen" else False
            surplus_ended = held if c.assist_active == "laden" else False
        charge_full = soc >= c.params.soc_max_kwh - ASSIST_MAX_SOC_MARGIN_KWH
        if c.assist_active == "ontladen" and (
                peak_ended or vrij_dis <= 0 or c.ev.charging()
                or not discharge_worth):
            c.assist_active = None
            await c.set_battery("rust", 0.0)
            c.advies = "rust"
            c.setpoint_w = 0.0
            c.reden = ("bijspringen klaar (piek voorbij)" if peak_ended
                       else "bijspringen klaar (bewaren is weer waardevoller)")
        elif c.assist_active == "laden" and (
                surplus_ended or charge_full or not charge_worth):
            c.assist_active = None
            await c.set_battery("rust", 0.0)
            c.advies = "rust"
            c.setpoint_w = 0.0
            c.reden = ("bijspringen klaar (maximale SoC bereikt)" if charge_full
                       else "bijspringen klaar (overschot voorbij of accu vol genoeg)")
        elif c.assist_active == "ontladen":
            if source_p1 is None:
                return
            target = min(max(source_p1, 0.0), c.params.p_discharge_max_w)
            if abs(target - c._last_discharge_w) < ASSIST_POWER_DEADBAND_W:
                return
            await c.set_battery("ontladen", target, p1_cap=False)
            c.setpoint_w = -round(c._last_discharge_w)
            c.reden = f"piek volgt bronvraag {source_p1:.0f} W"
        elif c.assist_active == "laden":
            # Native surplus-matching regelt zelf continu. Vaste adapters
            # krijgen hier een nieuw setpoint op basis van de bronflow, zodat
            # een afnemend overschot niet ongemerkt netimport veroorzaakt.
            if c.caps.surplus_mode or source_p1 is None:
                return
            target = min(max(-source_p1, 0.0), c.params.p_charge_max_w)
            if abs(target - c._last_charge_w) < ASSIST_POWER_DEADBAND_W:
                return
            await c.set_battery("laden", target)
            c.setpoint_w = round(c._last_charge_w)
            c.reden = f"zonoverschot volgt bronexport {-source_p1:.0f} W"
        elif (p1 > ASSIST_IMPORT_W and discharge_worth
              and not c.ev.charging() and vrij_dis > 0):
            c.assist_active = "ontladen"
            self.started = time.monotonic()
            self.end_since = None
            await c.set_battery("ontladen", min(p1, c.params.p_discharge_max_w))
            c.advies = "bijspringen: ontladen"
            c.setpoint_w = -round(c._last_discharge_w)
            c.reden = (f"piek {p1:.0f} W: dekken is €{dek_waarde:.3f}/kWh waard, "
                       f"bewaren €{floor:.3f}")
        elif p1 < -ASSIST_EXPORT_W and not charge_full and charge_worth:
            c.assist_active = "laden"
            self.started = time.monotonic()
            self.end_since = None
            await c.set_battery("laden_overschot", min(-p1, c.params.p_charge_max_w))
            c.advies = "bijspringen: laden"
            c.setpoint_w = round(c._last_charge_w)
            c.reden = (f"zonoverschot {-p1:.0f} W: opslaan is tot €{ceil:.3f}/kWh "
                       f"waard, exporteren levert €{exportprijs - c.params.beta:.3f}")
        else:
            return
        c.log_decision(prev)
        c.write_entities()
