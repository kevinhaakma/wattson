"""Realtime-lagen bovenop het uurplan.

- SourcePower     : één gefilterde, accu-gecorrigeerde kijk op de netflow;
                    de assist-startbeslissingen lezen hier
- TrackController : volgt de gemeten huisvraag op limiet-adapters (snel
                    omhoog, lui omlaag) en promoveert dal-laden naar surplus
- DischargeController: ontlaadregelaar voor vaste-setpoint-adapters (Zendure,
                    marstek, generic): één trage lus op de mediaan van de
                    huislast, nooit naar 0 — zie discharge_loop.py
- ExportRecovery  : breekt vastgelopen ontladen af bij bevestigde bronexport
                    (op vaste adapters alleen als de regelaar al op de vloer staat)
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
    ASSIST_START_CONFIRM_S,
    ASSIST_STOP_MARGIN_EUR,
    ASSIST_STOP_GRACE_S,
    ASSIST_STOP_W,
    ASSIST_THROTTLE_S,
    DIS_LOOP_DEADBAND_W,
    DIS_LOOP_FLOOR_W,
    DIS_LOOP_KICK_COOLDOWN_S,
    DIS_LOOP_STUCK_S,
    DIS_LOOP_LATENCY_S,
    DIS_LOOP_MAX_AGE_S,
    DIS_LOOP_MIN_SAMPLES,
    DIS_LOOP_WINDOW_S,
    DISCHARGE_EXPORT_ABORT_HOLD_S,
    DISCHARGE_EXPORT_ABORT_W,
    SETPOINT_ACK_DEADBAND_W,
    SOURCE_MIN_SAMPLES,
    SOURCE_WINDOW_S,
    SURPLUS_DEMOTE_MARGIN_W,
    SURPLUS_DEMOTE_WINDOW_S,
    SURPLUS_PROMOTE_W,
    TRACK_DEADBAND_W,
    TRACK_FAST_THROTTLE_S,
    TRACK_LOWER_GRACE_S,
    TRACK_MARGE_W,
)
from .control import AdviceMode, BatteryAction, CommandSource, Decision
from .discharge_loop import ChargeLoop, DischargeLoop

_LOGGER = logging.getLogger(__name__)


class SourcePower:
    """Één gefilterde kijk op de netflow — de bron van waarheid voor realtime.

    Positief = het huis vraagt van het net, negatief = de bron (PV) exporteert.

    Twee eigenschappen die losse `fresh_power_w`-aanroepen niet kunnen geven:

    1. **Historie.** Een beslissing kan eisen dat een afwijking een heel
       venster aanhoudt. Enkelvoudige meteruitschieters (gemeten: +2026 W
       tussen twee samples van -2000 W) halen zo'n eis nooit, dus starten ze
       ook niets meer.
    2. **Eén waarheid.** Elke laag las voorheen zelf de meter met een eigen
       versheidsgrens (assist 120 s, export-recovery 60 s) en trok daar zijn
       eigen conclusie uit. Twee lagen konden elkaar dus tegenspreken over
       hetzelfde huis. Nu leest iedereen dezelfde reeks.

    Per sample worden twee waarden bewaard: de kale meterstand en de
    accu-gecorrigeerde. Zijn ze niet eensluidend over hetzelfde teken — het
    apparaat loopt na een stop nog uit, of de telemetrie is stil — dan is er
    geen bewijs en levert `sustained()` False. Niets doen is daar de veilige
    uitkomst.
    """

    def __init__(self, c) -> None:
        self.c = c
        # (monotone tijd, kale P1, accu-gecorrigeerde P1)
        self._hist: list[tuple[float, float, float]] = []
        self._last_seen: float | None = None   # last_updated van de meter

    # ---------- vullen ----------
    def sample(self) -> float | None:
        """Neem de actuele meterstand op en geef de gecorrigeerde waarde terug.

        Idempotent per meterupdate: dezelfde meting wordt niet twee keer
        opgenomen, zodat een callback-storm het venster niet kan volstoppen
        met kopieën van één sample.
        """
        c = self.c
        st = c.hass.states.get(c.ent_p1) if c.ent_p1 else None
        if st is None:
            return None
        raw = c.t.fresh_power_w(c.ent_p1)
        if raw is None:
            return None
        stamp = st.last_updated.timestamp()
        now = time.monotonic()
        corrected = self.corrected(raw)
        if self._last_seen is None or stamp > self._last_seen:
            self._last_seen = stamp
            self._hist.append((now, raw, corrected))
            cutoff = now - SOURCE_WINDOW_S
            if self._hist[0][0] < cutoff:
                self._hist = [s for s in self._hist if s[0] >= cutoff]
        return corrected

    def corrected(self, p1_w: float) -> float:
        """P1 met het effect van de eigen accu eruit gerekend.

        Gebruikt hetzelfde ongunstigste-geval als de export-recovery: het
        hoogste van commando en meting. Bij een accu in rust is dit gelijk
        aan de kale meting.
        """
        c = self.c
        ent_chg, ent_dis = c.bat_flow_entities()
        dis = max(c._last_discharge_w, c.t.power_w(ent_dis) or 0.0, 0.0)
        chg = max(c._last_charge_w, c.t.power_w(ent_chg) or 0.0, 0.0)
        if dis > 0.0:
            return A.p1_without_battery(p1_w, discharge_w=dis)
        if chg > 0.0:
            return A.p1_without_battery(p1_w, charge_w=chg)
        return p1_w

    # ---------- lezen ----------
    def now(self) -> float | None:
        """Laatste gecorrigeerde waarde (None als er geen verse meting is)."""
        return self.sample()

    def _window(self, window_s: float) -> list[tuple[float, float, float]]:
        now = time.monotonic()
        return [s for s in self._hist if now - s[0] <= window_s]

    def sustained(self, threshold_w: float, window_s: float, *,
                  importing: bool) -> bool:
        """Lag de netflow dit HELE venster voorbij de drempel?

        Eist drie dingen: genoeg samples, volledige tijdsdekking van het
        venster, en dat élk sample voorbij de drempel lag — in zowel de kale
        als de gecorrigeerde meting. Eén sample terug binnen de drempel
        (uitschieter, wolkgat, run-down van het apparaat) is genoeg om False
        te leveren.
        """
        self.sample()
        if not self._hist:
            return False
        now = time.monotonic()
        if now - self._hist[0][0] < window_s:
            return False      # nog niet lang genoeg aan het meten
        win = self._window(window_s)
        if len(win) < SOURCE_MIN_SAMPLES:
            return False      # meter stil gevallen: geen bewijs
        if importing:
            return all(raw > threshold_w and corr > threshold_w
                       for _, raw, corr in win)
        return all(raw < -threshold_w and corr < -threshold_w
                   for _, raw, corr in win)

    def sustained_import(self, threshold_w: float,
                         window_s: float = ASSIST_START_CONFIRM_S) -> bool:
        return self.sustained(threshold_w, window_s, importing=True)

    def sustained_export(self, threshold_w: float,
                         window_s: float = ASSIST_START_CONFIRM_S) -> bool:
        return self.sustained(threshold_w, window_s, importing=False)

    def typical(self, window_s: float = ASSIST_START_CONFIRM_S) -> float | None:
        """Mediaan van de gecorrigeerde netflow over het venster.

        Startvermogen wordt hierop bepaald in plaats van op de laatste
        meting: die laatste kan de uitschieter zelf zijn (een piek van
        2026 W zette voorheen direct een setpoint van 1400 W).
        """
        self.sample()
        win = self._window(window_s)
        if not win:
            return None
        vals = sorted(s[2] for s in win)
        mid = len(vals) // 2
        if len(vals) % 2:
            return vals[mid]
        return (vals[mid - 1] + vals[mid]) / 2.0

    def reset(self) -> None:
        """Vergeet de historie (na een eigen commando is oud bewijs vervuild)."""
        self._hist = []
        self._last_seen = None


class TrackController:
    """Volgt de gemeten bronvraag tijdens ontladen; promoveert dal-laden
    naar surplus-matching zodra er meer bronoverschot is dan het setpoint."""

    def __init__(self, c) -> None:
        self.c = c
        self.tracked_outlim = 0.0     # laatst door de volglus geschreven outputlimiet
        self._fast_last = 0.0
        self._vraag_hist: list[tuple[float, float]] = []  # (t, vraag) voor terugneem-grace
        self._chg_hist: list[tuple[float, float]] = []    # (t, laadvermogen) in overschotmodus

    def surplus_promotable(self) -> bool:
        """Mag vast netladen naar overschotladen promoveren?

        Eist dat de kale P1 het hele confirmvenster meer dan
        SURPLUS_PROMOTE_W exporteerde — dezelfde SourcePower-toets als de
        assist-start. Tijdens vast laden op P staat de kale meter op
        (huis - PV + P); export daar is dus bron-overschot voorbij het
        setpoint. Vóór 03-09 volstond één sample (bron_export > P + 300):
        elk wolkgat promoveerde, en de demotie (plantick + piek-geheugen)
        hield de trage overschotmodus daarna minutenlang vast.
        """
        return self.c.source.sustained_export(SURPLUS_PROMOTE_W)

    def surplus_carried(self, setpoint_w: float) -> bool:
        """Heeft het zonoverschot het geplande laadvermogen recent (piek)
        kunnen dragen? Peak-memory over SURPLUS_DEMOTE_WINDOW_S: een wolkgat
        op het tick-moment is geen bewijs dat het overschot weg is — demotie
        naar vast netladen pas als het hele venster onder het plan bleef."""
        now_m = time.monotonic()
        recent = [v for t, v in self._chg_hist
                  if now_m - t <= SURPLUS_DEMOTE_WINDOW_S]
        if not recent:
            return False
        return max(recent) >= setpoint_w - SURPLUS_DEMOTE_MARGIN_W

    def note_applied(self, action: str, applied: float) -> None:
        if action == "ontladen" and self.c.caps.p1_matching:
            # referentie voor de volglus: dit is wat de adapter als limiet schreef
            self.tracked_outlim = applied
        if action == "laden_overschot" and not self._chg_hist:
            # vers gepromoveerd: gun de overschotmodus één demotie-venster
            # voordat een tick-moment-wolkgat hem alweer terugzet
            self._chg_hist.append((time.monotonic(), applied))

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
        now = time.monotonic()
        if now - self._fast_last < TRACK_FAST_THROTTLE_S:
            return
        # surplus-promotie ook event-gedreven: groeit het bronoverschot tijdens
        # vast (dal-)laden voorbij het setpoint, dan zou het verschil tot de
        # trage lus het net op lekken — naar surplus-matching zodra de export
        # het hele confirmvenster aanhoudt (één wolkgat-sample is geen bewijs)
        if (c._last_action == "laden" and c.caps.surplus_mode
                and c.control_enabled and not c.safety.tripped
                and not c.ev.charging()):
            if self.surplus_promotable():
                self._fast_last = now
                c.hass.async_create_task(c.set_battery(
                    BatteryAction.SURPLUS_CHARGE,
                    max(c._last_charge_w, 0.0),
                    source=CommandSource.REALTIME))
            return
        if not c.caps.p1_matching:
            return  # vaste setpoints: DischargeController regelt (nooit per sample)
        vraag = self.discharge_target()
        if vraag is None:
            return
        doel = min(max(vraag + TRACK_MARGE_W, c.caps.min_setpoint_w),
                   c.params.p_discharge_max_w)
        if doel <= self.tracked_outlim + TRACK_DEADBAND_W:
            return  # alleen ophogen; verlagen doet de trage lus
        self._fast_last = now
        c.hass.async_create_task(self._apply(doel))

    async def _apply(self, doel_w: float) -> None:
        c = self.c
        if c.caps.p1_matching:
            applied = await c.set_discharge_limit(doel_w)
            if applied is not None:
                self.tracked_outlim = applied
        else:
            await c.set_battery(
                BatteryAction.DISCHARGE, doel_w, p1_cap=False,
                source=CommandSource.REALTIME)

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
        if act == "laden_overschot":
            # peak-memory voor de demotie-beslissing van de plan-tick: alleen
            # als het overschot het geplande vermogen dit hele venster niet
            # droeg, mag vast netladen het overnemen (anti-flapping)
            chg = c.t.fresh_power_w(c.bat_flow_entities()[0])
            if chg is not None:
                now_m = time.monotonic()
                self._chg_hist.append((now_m, chg))
                self._chg_hist = [(t, v) for t, v in self._chg_hist
                                  if now_m - t <= SURPLUS_DEMOTE_WINDOW_S]
        elif act != "laden" and self._chg_hist:
            self._chg_hist = []
        if act == "ontladen" and c.caps.p1_matching:
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
            doel = min(max(vraag_eff + TRACK_MARGE_W, c.caps.min_setpoint_w),
                       c.params.p_discharge_max_w)
            if doel <= self.tracked_outlim - TRACK_DEADBAND_W:
                applied = await c.set_discharge_limit(doel)
                if applied is not None:
                    self.tracked_outlim = applied
        elif act == "laden" and c.caps.surplus_mode:
            if self.surplus_promotable():
                await c.set_battery(
                    BatteryAction.SURPLUS_CHARGE,
                    max(c._last_charge_w, 0.0),
                    source=CommandSource.REALTIME)


class DischargeController:
    """Ontladen op vaste-setpoint-adapters: HA-schil om DischargeLoop.

    Eén lus, event-gedreven op de P1 maar per constructie traag: een sample
    telt pas mee DIS_LOOP_LATENCY_S na het laatste eigen commando, en een
    besluit eist DIS_LOOP_WINDOW_S aan bewijs. Verhogen en verlagen gaan door
    dezelfde regel; naar 0 gaat hij nooit (vloer). Actief zodra de laatste
    actie 'ontladen' is — gepland of via bijspringen — dus ook de assist-
    modulatie loopt hierlangs. Uitschakelen blijft aan planner/veiligheid.
    """

    def __init__(self, c) -> None:
        self.c = c
        self.loop = DischargeLoop(
            latency_s=DIS_LOOP_LATENCY_S, window_s=DIS_LOOP_WINDOW_S,
            min_samples=DIS_LOOP_MIN_SAMPLES, deadband_w=DIS_LOOP_DEADBAND_W,
            floor_w=DIS_LOOP_FLOOR_W, max_age_s=DIS_LOOP_MAX_AGE_S)
        self._pending = False
        self._windup_logged = False
        self._stuck_since: float | None = None
        self._last_kick = 0.0

    @property
    def active(self) -> bool:
        c = self.c
        return (not c.caps.p1_matching and c.control_enabled
                and not c.safety.tripped and c._last_action == "ontladen")

    def at_floor(self) -> bool:
        return self.c._last_discharge_w <= DIS_LOOP_FLOOR_W + DIS_LOOP_DEADBAND_W

    def note_applied(self, action: str, applied: float) -> None:
        """Elk fysiek commando (planner, assist, veiligheid) reset het bewijs."""
        if self.c.caps.p1_matching:
            return
        self.loop.note_command(
            time.monotonic(), applied if action == "ontladen" else 0.0)

    @callback
    def on_p1(self, _event) -> None:
        c = self.c
        if not self.active or self._pending or c.ev.charging():
            return
        self._check_stuck()
        if self._pending:
            return
        p1 = c.t.fresh_power_w(c.ent_p1, 60)
        if p1 is None:
            return
        self.loop.observe(time.monotonic(), p1)
        doel = self.loop.decide(c.params.p_discharge_max_w)
        if doel is None:
            return
        if doel > c._last_discharge_w and not self._device_following():
            # anti-windup: de lus rekent huislast = P1 + commando en neemt
            # aan dat het apparaat volgt. Volgt het aantoonbaar niet (05-09:
            # zenSDK-test, commando's bereikten het apparaat niet, P1 bleef
            # +390 W en de lus liep op tot 1386 W), dan niet verder verhogen.
            if not self._windup_logged:
                _LOGGER.warning(
                    "Wattson ontlaadregelaar: apparaat volgt het setpoint niet "
                    "(%.0f W gecommandeerd, %s W gemeten) — niet verder verhogen",
                    c._last_discharge_w, self._measured_str())
                self._windup_logged = True
            return
        self._windup_logged = False
        self._pending = True
        c.hass.async_create_task(self._apply(doel))

    def _check_stuck(self) -> None:
        """Apparaat volgt het commando langer dan DIS_LOOP_STUCK_S niet: één
        schop (rust en opnieuw), zodat select én vermogen opnieuw worden
        geschreven. Wattson schrijft normaal alleen bij wijziging; na een
        reload van de accu-integratie kan de entity de oude stand tonen
        terwijl de manager zelf idle staat — dan wacht hij anders eeuwig."""
        now = time.monotonic()
        if self._device_following():
            self._stuck_since = None
            return
        if self._stuck_since is None:
            self._stuck_since = now
            return
        if (now - self._stuck_since >= DIS_LOOP_STUCK_S
                and now - self._last_kick >= DIS_LOOP_KICK_COOLDOWN_S
                and not self._pending):
            self._last_kick = now
            self._stuck_since = None
            self._pending = True
            self.c.hass.async_create_task(self._kick())

    async def _kick(self) -> None:
        c = self.c
        try:
            w = c._last_discharge_w
            _LOGGER.warning(
                "Wattson ontlaadregelaar: apparaat volgt %.0f W al %d s niet "
                "(%s W gemeten) — rust en opnieuw sturen", w, DIS_LOOP_STUCK_S,
                self._measured_str())
            if await c.set_battery(BatteryAction.IDLE, 0.0,
                                   source=CommandSource.REALTIME) is None:
                return
            await c.set_battery(BatteryAction.DISCHARGE, max(w, DIS_LOOP_FLOOR_W),
                                p1_cap=False, source=CommandSource.REALTIME)
            self.loop.unconfirmed = 0
            c.write_entities()
        finally:
            self._pending = False

    def _measured_dis(self) -> float | None:
        """Gemeten ontlaadvermogen als de telemetrie leeft (last_reported)."""
        return self.c.t.live_power_w(self.c.bat_flow_entities()[1], 180)

    def _measured_str(self) -> str:
        m = self._measured_dis()
        return "?" if m is None else f"{m:.0f}"

    def _device_following(self) -> bool:
        """Plausibiliteit: is het gemeten ontlaadvermogen (levende telemetrie)
        in de buurt van het commando? Geen levende meting = geen bewijs van
        het tegendeel."""
        c = self.c
        if c._last_discharge_w <= 0:
            return True
        soc = c.soc_pct()
        if soc is not None and c.params.capacity_kwh > 0 and (
                soc <= c.params.soc_min_kwh / c.params.capacity_kwh * 100.0 + 1.0):
            # lege accu (vloer bereikt): het apparaat stopt zelf en dat is
            # geen niet-volgen. 06-09 06:46: schop op een lege accu.
            return True
        cmd_t = self.loop.cmd_t
        if cmd_t is not None and time.monotonic() - cmd_t < DIS_LOOP_LATENCY_S + 30:
            return True  # nog binnen de latentie: niets te bewijzen
        if not self.loop.following:
            return False  # P1-ack: commando's komen niet op de meter terug
        m = self._measured_dis()
        if m is None:
            return True
        return m >= 0.5 * c._last_discharge_w - 50.0

    async def _apply(self, doel_w: float) -> None:
        c = self.c
        try:
            prev_w = c._last_discharge_w
            applied = await c.set_battery(
                BatteryAction.DISCHARGE, doel_w, p1_cap=False,
                source=CommandSource.REALTIME)
            if applied is None:
                return
            # set_battery -> note_applied heeft het commando al genoteerd
            c.setpoint_w = -round(applied)
            _LOGGER.debug("Wattson ontlaadregelaar: %.0f -> %.0f W (huis ~%.0f W)",
                          prev_w, applied, self.loop.house_estimate() or -1)
            c.write_entities()
        finally:
            self._pending = False


class ChargeController:
    """Laden op vaste-setpoint-adapters: spiegel van DischargeController.

    Actief zodra de laatste actie 'laden' is (gepland of bijspringen). De lus
    schat het bronoverschot (-P1 + commando) op de mediaan en zet het
    laadvermogen op max(plan, overschot): zon boven het plan wordt meegenomen,
    daaronder blijft het geplande netladen staan. Nooit naar 0 (vloer):
    stoppen is aan planner (uur klaar, plafond) en bijspringen-stop.
    """

    def __init__(self, c) -> None:
        self.c = c
        self.loop = ChargeLoop(
            latency_s=DIS_LOOP_LATENCY_S, window_s=DIS_LOOP_WINDOW_S,
            min_samples=DIS_LOOP_MIN_SAMPLES, deadband_w=DIS_LOOP_DEADBAND_W,
            floor_w=DIS_LOOP_FLOOR_W, max_age_s=DIS_LOOP_MAX_AGE_S)
        self._pending = False
        self._windup_logged = False
        self._stuck_since: float | None = None
        self._last_kick = 0.0

    @property
    def active(self) -> bool:
        c = self.c
        return (not c.caps.p1_matching and c.control_enabled
                and not c.safety.tripped and c._last_action in ("laden", "laden_overschot"))

    def plan_floor_w(self) -> float:
        """Geplande netlading als ondergrens; bij bijspringen 0."""
        c = self.c
        if c.mode is AdviceMode.CHARGE and c.setpoint_w > 0:
            return float(c.setpoint_w)
        return 0.0

    def note_applied(self, action: str, applied: float) -> None:
        if self.c.caps.p1_matching:
            return
        self.loop.note_command(
            time.monotonic(), applied if action in ("laden", "laden_overschot") else 0.0)

    def _measured_chg(self) -> float | None:
        return self.c.t.live_power_w(self.c.bat_flow_entities()[0], 180)

    def _device_following(self) -> bool:
        c = self.c
        if c._last_charge_w <= 0:
            return True
        soc = c.soc_pct()
        if soc is not None and c.params.capacity_kwh > 0 and (
                soc >= c.params.soc_max_kwh / c.params.capacity_kwh * 100.0 - 1.0):
            return True  # vol (plafond): het apparaat stopt zelf
        cmd_t = self.loop.cmd_t
        if cmd_t is not None and time.monotonic() - cmd_t < DIS_LOOP_LATENCY_S + 30:
            return True
        if not self.loop.following:
            return False  # P1-ack: commando's komen niet op de meter terug
        m = self._measured_chg()
        if m is None:
            return True
        return m >= 0.5 * c._last_charge_w - 50.0

    def _check_stuck(self) -> None:
        now = time.monotonic()
        if self._device_following():
            self._stuck_since = None
            return
        if self._stuck_since is None:
            self._stuck_since = now
            return
        if (now - self._stuck_since >= DIS_LOOP_STUCK_S
                and now - self._last_kick >= DIS_LOOP_KICK_COOLDOWN_S
                and not self._pending):
            self._last_kick = now
            self._stuck_since = None
            self._pending = True
            self.c.hass.async_create_task(self._kick())

    async def _kick(self) -> None:
        c = self.c
        try:
            w = c._last_charge_w
            m = self._measured_chg()
            _LOGGER.warning(
                "Wattson laadregelaar: apparaat volgt %.0f W al %d s niet (%s W gemeten) — rust en opnieuw sturen",
                w, DIS_LOOP_STUCK_S, "?" if m is None else f"{m:.0f}")
            if await c.set_battery(BatteryAction.IDLE, 0.0,
                                   source=CommandSource.REALTIME) is None:
                return
            await c.set_battery(BatteryAction.CHARGE, max(w, DIS_LOOP_FLOOR_W),
                                source=CommandSource.REALTIME)
            self.loop.unconfirmed = 0
            c.write_entities()
        finally:
            self._pending = False

    @callback
    def on_p1(self, _event) -> None:
        c = self.c
        if not self.active or self._pending:
            return
        self._check_stuck()
        if self._pending:
            return
        p1 = c.t.fresh_power_w(c.ent_p1, 60)
        if p1 is None:
            return
        self.loop.observe(time.monotonic(), p1)
        doel = self.loop.decide(c.params.p_charge_max_w, self.plan_floor_w())
        if doel is None:
            return
        if doel > c._last_charge_w and not self._device_following():
            if not self._windup_logged:
                _LOGGER.warning(
                    "Wattson laadregelaar: apparaat volgt het setpoint niet (%.0f W gecommandeerd) — niet verder verhogen",
                    c._last_charge_w)
                self._windup_logged = True
            return
        self._windup_logged = False
        self._pending = True
        c.hass.async_create_task(self._apply(doel))

    async def _apply(self, doel_w: float) -> None:
        c = self.c
        try:
            prev_w = c._last_charge_w
            applied = await c.set_battery(
                BatteryAction.CHARGE, doel_w, source=CommandSource.REALTIME)
            if applied is None:
                return
            c.setpoint_w = round(applied)
            _LOGGER.debug("Wattson laadregelaar: %.0f -> %.0f W (overschot ~%.0f W, plan %.0f W)",
                          prev_w, applied, self.loop.house_estimate() or -1, self.plan_floor_w())
            c.write_entities()
        finally:
            self._pending = False


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
            and c.mode in (AdviceMode.DISCHARGE, AdviceMode.ASSIST_DISCHARGE)
        )
        if not active:
            self.since = None
            return
        if not c.caps.p1_matching and not c.discharge_ctl.at_floor():
            # de ontlaadregelaar kan nog verlagen: export is dan geen bewijs
            # van bronoverschot maar van een nog niet uitgeregelde stap
            self.since = None
            return
        # v3: export tijdens ontladen is geen fout op zich — verkopen mag als
        # het loont. Alleen ingrijpen als exporteren de λ-regel NIET haalt
        # (dan lekt bewaarwaarde het net op); haalt hij hem wel, dan is dit
        # gewoon winstgevende teruglevering en regelt de volglus het vermogen.
        if c.sell_enabled:
            soc_pct = c.soc_pct()
            prijs = c.t.current_price()
            if soc_pct is not None and prijs is not None:
                soc = soc_pct / 100.0 * c.params.capacity_kwh
                exportprijs = c.scenario.export_price(prijs, dt_util.now().date())
                if (exportprijs - c.params.beta
                        > c.values.discharge_floor(soc) + ASSIST_MARGIN_EUR):
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
            soc_pct = c.soc_pct()
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
            stopped = await c.set_battery(
                BatteryAction.IDLE, 0.0, source=CommandSource.REALTIME)
            if stopped is None:
                return
            if can_store and charge_economic:
                target = min(max(-source_p1, c.caps.min_setpoint_w),
                             c.params.p_charge_max_w)
                applied = await c.set_battery(
                    BatteryAction.SURPLUS_CHARGE, target,
                    source=CommandSource.REALTIME)
                if applied is None:
                    return
                c.assist_active = "laden"
                c.assist.started = time.monotonic()
                c.set_decision(Decision(
                    AdviceMode.ASSIST_CHARGE,
                    round(c._last_charge_w),
                    f"sterke bronexport {-source_p1:.0f} W bevestigd — "
                    "ontladen afgebroken en overschotladen hervat",
                ))
            else:
                waarom = "accu vrijwel vol" if not can_store else "opslaan niet economisch"
                c.set_decision(Decision(
                    AdviceMode.IDLE,
                    reason=f"sterke bronexport {-source_p1:.0f} W bevestigd — "
                    f"ontladen afgebroken ({waarom})",
                ))
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
        if c.mode not in (AdviceMode.IDLE, AdviceMode.EV_GUARD) and not c.assist_active:
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
        # assist staat stil: de accu kan nog uitlopen na een eigen stop, dus ook
        # hier de accu eruit rekenen in plaats van de kale meterstand teruggeven
        return c.source.corrected(p1_w)

    async def apply(self) -> None:
        c = self.c
        v = c.values
        await c.safety.watchdog()
        if c.safety.tripped or c.export_recovery.pending:
            return
        src = c.source
        src.sample()
        p1 = c.t.fresh_power_w(c.ent_p1, 120)
        soc_pct = c.soc_pct()
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
        # lopende assist: bredere stop-hysterese (start en stop niet op dezelfde grens)
        discharge_keep = dek_waarde > floor - ASSIST_STOP_MARGIN_EUR
        charge_keep = exportprijs - c.params.beta < ceil + ASSIST_STOP_MARGIN_EUR
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
                or not discharge_keep):
            c.assist_active = None
            await c.set_battery(
                BatteryAction.IDLE, 0.0, source=CommandSource.REALTIME)
            c.set_decision(Decision(
                AdviceMode.IDLE,
                reason=("bijspringen klaar (piek voorbij)" if peak_ended
                        else "bijspringen klaar (bewaren is weer waardevoller)"),
            ))
        elif c.assist_active == "laden" and (
                surplus_ended or charge_full or not charge_keep):
            c.assist_active = None
            await c.set_battery(
                BatteryAction.IDLE, 0.0, source=CommandSource.REALTIME)
            c.set_decision(Decision(
                AdviceMode.IDLE,
                reason=("bijspringen klaar (maximale SoC bereikt)" if charge_full
                        else "bijspringen klaar (overschot voorbij of accu vol genoeg)"),
            ))
        elif c.assist_active == "ontladen":
            if not c.caps.p1_matching:
                return  # DischargeController moduleert op vaste adapters
            if source_p1 is None:
                return
            target = min(max(source_p1, 0.0), c.params.p_discharge_max_w)
            if abs(target - c._last_discharge_w) < ASSIST_POWER_DEADBAND_W:
                return
            applied = await c.set_battery(
                BatteryAction.DISCHARGE, target, p1_cap=False,
                source=CommandSource.REALTIME)
            if applied is None:
                return
            c.setpoint_w = -round(c._last_discharge_w)
            c.reden = f"piek volgt bronvraag {source_p1:.0f} W"
        elif c.assist_active == "laden":
            # Native surplus-matching regelt zelf continu. Vaste adapters
            # krijgen hier een nieuw setpoint op basis van de bronflow, zodat
            # een afnemend overschot niet ongemerkt netimport veroorzaakt.
            if c.caps.surplus_mode or source_p1 is None:
                return
            if not c.caps.p1_matching:
                return  # ChargeController moduleert op vaste adapters
            target = min(max(-source_p1, 0.0), c.params.p_charge_max_w)
            if abs(target - c._last_charge_w) < ASSIST_POWER_DEADBAND_W:
                return
            applied = await c.set_battery(
                BatteryAction.CHARGE, target, source=CommandSource.REALTIME)
            if applied is None:
                return
            c.setpoint_w = round(c._last_charge_w)
            c.reden = f"zonoverschot volgt bronexport {-source_p1:.0f} W"
        # Starten mag alleen op een AANHOUDENDE afwijking (SOURCE-venster), niet
        # op de laatste meting: losse meteruitschieters startten voorheen een
        # ontlading die de export-recovery direct weer afbrak. Het vermogen komt
        # van de mediaan over het venster, zodat de uitschieter ook het setpoint
        # niet meer bepaalt.
        elif (discharge_worth and not c.ev.charging() and vrij_dis > 0
              and src.sustained_import(ASSIST_IMPORT_W)):
            vraag = src.typical()
            if vraag is None:
                return
            applied = await c.set_battery(
                BatteryAction.DISCHARGE,
                min(vraag, c.params.p_discharge_max_w),
                source=CommandSource.REALTIME)
            if applied is None:
                return
            c.assist_active = "ontladen"
            self.started = time.monotonic()
            self.end_since = None
            c.set_decision(Decision(
                AdviceMode.ASSIST_DISCHARGE,
                -round(c._last_discharge_w),
                f"aanhoudende piek {vraag:.0f} W ({ASSIST_START_CONFIRM_S:.0f}s): "
                f"dekken is €{dek_waarde:.3f}/kWh waard, bewaren €{floor:.3f}",
            ))
        elif (not charge_full and charge_worth
              and src.sustained_export(ASSIST_EXPORT_W)):
            overschot = src.typical()
            if overschot is None:
                return
            applied = await c.set_battery(
                BatteryAction.SURPLUS_CHARGE,
                min(-overschot, c.params.p_charge_max_w),
                source=CommandSource.REALTIME)
            if applied is None:
                return
            c.assist_active = "laden"
            self.started = time.monotonic()
            self.end_since = None
            c.set_decision(Decision(
                AdviceMode.ASSIST_CHARGE,
                round(c._last_charge_w),
                f"aanhoudend zonoverschot {-overschot:.0f} W "
                f"({ASSIST_START_CONFIRM_S:.0f}s): opslaan is tot €{ceil:.3f}/kWh "
                f"waard, exporteren levert €{exportprijs - c.params.beta:.3f}",
            ))
        else:
            return
        c.log_decision(prev)
        c.write_entities()
