"""Coordinator: plant periodiek en stuurt (optioneel) de accu aan.

Schaduwmodus (sturing uit) publiceert alleen het advies; met de master-switch
aan wordt het eerste planuur uitgevoerd:
- laden      -> operation 'manual' + manual_power = -W  (negatief = laden)
- ontladen   -> operation 'manual' + manual_power = +W; Wattson volgt de P1
                event-gedreven en remt direct terug bij export
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
    ASSIST_MAX_SOC_MARGIN_KWH,
    ASSIST_MIN_RUN_S,
    ASSIST_POWER_DEADBAND_W,
    ASSIST_STOP_GRACE_S,
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
    CONF_SELL_THRESHOLD,
    DAGLICHT,
    DEFAULT_OPTIONS,
    DISCHARGE_EXPORT_ABORT_HOLD_S,
    DISCHARGE_EXPORT_ABORT_W,
    DIS_GUARD_DEADBAND_W,
    DIS_GUARD_THROTTLE_S,
    DWELL_OVERRIDE_EUR,
    EV_HOUSE_MIN_W,
    EV_SUSPECT_JUMP_W,
    EV_THRESHOLD_KW,
    GEENDATA_STOP_S,
    PLAN_MIN_DWELL_S,
    SETPOINT_ACK_DEADBAND_W,
    SOLAR_ASSIST_IMPORT_W,
    SOLAR_BUFFER_KWH,
    SOLAR_FORECAST_CONFIDENCE,
    SWITCH_DEADBAND_EUR,
    TRACK_DEADBAND_W,
    TRACK_FAST_THROTTLE_S,
    TRACK_INTERVAL_S,
    TRACK_LOWER_GRACE_S,
    TRACK_MARGE_W,
    UPDATE_MINUTES,
    WATCH_INTERVAL_S,
    WATCH_FRESH_S,
    WATCH_RUNAWAY_W,
    WATCH_STOP_GRACE_S,
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
        self.solar_backed_kwh = 0.0
        self._plan_dis_floor: float | None = None  # goedkoopste geplande ontlaaduurprijs
        self._cheap_future = 0.0
        self._max_future = 0.0
        self._assist_last = 0.0
        self._assist_started = 0.0
        self._assist_end_since: float | None = None
        self._ev_house = False     # v1.9: bewust huisdeel-ontladen tijdens EV-sessie
        self._last_mode_switch = 0.0
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
        self._last_action: str | None = None
        self._last_charge_w = 0.0      # laatst werkelijk toegepast laadvermogen
        self._last_discharge_w = 0.0   # laatst werkelijk toegepast ontlaadvermogen
        self._stop_grace_until = 0.0   # tot dit monotonic-moment is uitloop van
        self._stopped_richting: str | None = None  # ...deze richting geen runaway
        self._tracked_outlim = 0.0     # laatst door de volglus geschreven outputlimiet
        self._vraag_hist: list[tuple[float, float]] = []  # (t, vraag) voor terugneem-grace
        self._track_fast_last = 0.0
        self._dis_guard_last = 0.0
        self._export_recovery_since: float | None = None
        self._export_recovery_pending = False
        self._switch_debt = 0.0        # opgeteld gemist voordeel van gedempte modewissels
        self._last_load_w: float | None = None  # huisvraag vorige tick (EV-sprong-detectie)
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
        # veiligheidsbewaking los van de (tragere) plan-tick: runaway- en
        # stilte-detectie mogen niet wachten op het her-plan-interval
        self.listeners.append(async_track_time_interval(
            self.hass, self._safety_tick, timedelta(seconds=WATCH_INTERVAL_S)))
        # snelle volglus: stuurwaarden bijregelen op de gemeten vraag
        self.listeners.append(async_track_time_interval(
            self.hass, self._track_tick, timedelta(seconds=TRACK_INTERVAL_S)))
        ev_entities = list(filter(None, (
            self.ent_wallbox_1, self.ent_wallbox_2,
            # gates ook volgen: aankomst/vertrek moet de guard herevalueren
            self.ent_wallbox_1_home, self.ent_wallbox_2_home,
        )))
        if ev_entities:
            self.listeners.append(async_track_state_change_event(
                self.hass, ev_entities, self._ev_guard))
        if self.ent_p1:
            ent_chg, ent_dis = self._bat_flow_entities()
            assist_entities = list(dict.fromkeys(filter(None, (
                self.ent_p1, self.ent_soc, ent_chg, ent_dis,
            ))))
            self.listeners.append(async_track_state_change_event(
                self.hass, assist_entities, self._assist_check))
            # snelle volg-laag: ruimte geven zodra de meter een piek toont
            self.listeners.append(async_track_state_change_event(
                self.hass, [self.ent_p1], self._track_fast))
            if not self.caps.p1_matching:
                # vast-setpoint-adapters: altijd-actieve guard verlaagt het
                # ontlaadvermogen zodra de huisvraag zakt
                self.listeners.append(async_track_state_change_event(
                    self.hass, [self.ent_p1], self._discharge_guard))
            if self.caps.surplus_mode:
                # Sterke, bevestigde bronexport tijdens (ook al naar 0 W
                # teruggeregeld) ontladen mag niet tot de volgende plan-/stop-
                # timer in manual blijven hangen: promoveer naar surplusladen.
                self.listeners.append(async_track_state_change_event(
                    self.hass, [self.ent_p1], self._export_recovery_check))
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

    def _ev_at_home(self, gate: str) -> bool:
        if not gate:
            return True
        st = self.hass.states.get(gate)
        return A.ev_gate_allows(None if st is None else st.state)

    def _wallbox_w(self, ent: str, gate: str, fresh_s: float | None = None) -> float:
        """EV-laadvermogen dat als THUIS-last telt. Voertuigtelemetrie
        (bijv. sensor.<auto>_charger_power) meet ook laden elders; met een
        geconfigureerde thuis-gate telt die meting dan niet mee."""
        if not ent or not self._ev_at_home(gate):
            return 0.0
        w = self._power_w(ent) if fresh_s is None else self._fresh_power_w(ent, fresh_s)
        return w or 0.0

    def _ev_charging(self) -> bool:
        threshold_w = EV_THRESHOLD_KW * 1000.0
        return (self._wallbox_w(self.ent_wallbox_1, self.ent_wallbox_1_home) > threshold_w
                or self._wallbox_w(self.ent_wallbox_2, self.ent_wallbox_2_home) > threshold_w)

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
        # het huidige uur weten we beter dan de bel; dit is een échte meting,
        # dus de forecast-bias hoort hier niet overheen
        pv_now = self._power_w(self.ent_pv_now)
        if hours and pv_now is not None:
            out[hours[0]] = pv_now
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
        # assist_active is de stuurwaarheid. Het advies kan tijdens een
        # gelijktijdige plan-tick kort veranderen en mag de watchdog dan niet
        # laten ingrijpen tegen een actie die Wattson zelf nog beheert.
        chg = self.advies in ("laden", "bijspringen: laden") or self.assist_active == "laden"
        dis = (self.advies in ("ontladen", "verkopen", "bijspringen: ontladen")
               or self.assist_active == "ontladen")
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
        if afwijking and richting == self._stopped_richting and time.monotonic() < self._stop_grace_until:
            # uitloop van een zojuist zelf gestopte actie: het apparaat heeft
            # cloud-latentie en mag binnen de grace nog in die richting actief
            # zijn — geen runaway; na de grace geldt de normale bewaking weer
            _LOGGER.debug("Wattson watchdog: %s genegeerd (stop-grace na eigen stopcommando)", afwijking)
            return
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

    def _discharge_target(self) -> float | None:
        """Gewenst ontlaadvermogen op basis van de gemeten bronvraag (of None)."""
        if not self.control_enabled or self._tripped or self._ev_charging():
            return None
        if self._last_action != "ontladen":
            return None
        if not self._discharge_command_settled():
            # P1 en accutelemetrie lopen bij Zendure enkele cycli uiteen. Een
            # nieuwe correctie vóór fysieke bevestiging combineert waarden van
            # twee verschillende setpoints en veroorzaakt import/export-pingpong.
            return None
        p1 = self._fresh_power_w(self.ent_p1, 90)
        if p1 is None:
            return None
        _, ent_dis = self._bat_flow_entities()
        dis = self._fresh_power_w(ent_dis)
        dis_now = dis if dis is not None else self._last_discharge_w
        return max(A.p1_without_battery(p1, discharge_w=dis_now), 0.0)

    def _discharge_feedback(self) -> float | None:
        """Actueel fysiek ontlaadvermogen voor setpoint-bevestiging."""
        if not self.caps.feedback_ack:
            return self._last_discharge_w
        _, ent_dis = self._bat_flow_entities()
        # Geen freshness-eis: een lang stabiel vermogen verandert in HA niet
        # altijd last_updated. Na een nieuw commando verschilt de oude waarde
        # vanzelf van het doel totdat echte telemetrie de wijziging bevestigt.
        return self._power_w(ent_dis)

    def _discharge_command_settled(self) -> bool:
        if not self.caps.feedback_ack:
            return True
        return A.setpoint_feedback_settled(
            self._last_discharge_w,
            self._discharge_feedback(),
            SETPOINT_ACK_DEADBAND_W,
        )

    @callback
    def _track_fast(self, _event) -> None:
        """Ruimte geven zodra de meter een piek toont (event-gedreven, throttled).

        Staat de limiet/het setpoint onder de werkelijke vraag, dan komt dat
        verschil van het net. Dit is dus haastwerk: de P1-meter tikt elke ~1 s
        en een commando landt in ~0,2 s, dus de accu volgt binnen enkele
        seconden — net als de fabrikant-app. Terugnemen mag lui (_track_tick):
        te veel ruimte kost niets, want matching exporteert niet en op vaste
        adapters remt de discharge-guard direct.
        """
        vraag = self._discharge_target()
        if vraag is None:
            return
        now = time.monotonic()
        if now - self._track_fast_last < TRACK_FAST_THROTTLE_S:
            return
        if self.caps.p1_matching:
            doel = min(max(vraag + TRACK_MARGE_W, self.caps.min_setpoint_w),
                       self.params.p_discharge_max_w)
            if doel <= self._tracked_outlim + TRACK_DEADBAND_W:
                return  # alleen ophogen; verlagen doet de trage lus
        else:
            doel = min(vraag, self.params.p_discharge_max_w)
            if doel <= self._last_discharge_w + TRACK_DEADBAND_W:
                return
        self._track_fast_last = now
        self.hass.async_create_task(self._track_apply(doel))

    async def _track_apply(self, doel_w: float) -> None:
        if self.caps.p1_matching:
            await A.set_power_number(self.hass, self.ent_zd_outlim, doel_w)
            self._tracked_outlim = doel_w
        else:
            await self._set_battery("ontladen", doel_w, p1_cap=False)

    async def _track_tick(self, _now) -> None:
        """Trage volglus (elke TRACK_INTERVAL_S): ruimte terugnemen + promotie.

        - ontladen: limiet/setpoint zakt weer mee met een afnemende vraag,
          zodat het plan niet ongemerkt meer levert dan bedoeld;
        - laden (vast, dal-uur): verschijnt er intussen méér bronoverschot dan
          het setpoint, promoveer dan naar surplus-matching i.p.v. op de
          plan-tick te wachten.
        Ophogen gebeurt event-gedreven in _track_fast.
        """
        if not self.control_enabled or self._tripped or self._ev_charging():
            return
        act = self._last_action
        if act == "ontladen":
            vraag = self._discharge_target()
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
            if self.caps.p1_matching:
                doel = min(max(vraag_eff + TRACK_MARGE_W, self.caps.min_setpoint_w),
                           self.params.p_discharge_max_w)
                if doel <= self._tracked_outlim - TRACK_DEADBAND_W:
                    await A.set_power_number(self.hass, self.ent_zd_outlim, doel)
                    self._tracked_outlim = doel
            else:
                doel = min(vraag_eff, self.params.p_discharge_max_w)
                if doel <= self._last_discharge_w - TRACK_DEADBAND_W:
                    await self._set_battery("ontladen", doel, p1_cap=False)
        elif act == "laden" and self.caps.surplus_mode:
            p1 = self._fresh_power_w(self.ent_p1, 90)
            if p1 is None:
                return
            ent_chg, _ = self._bat_flow_entities()
            chg = self._fresh_power_w(ent_chg)
            chg_now = chg if chg is not None else self._last_charge_w
            bron_export = -A.p1_without_battery(p1, charge_w=chg_now)
            if bron_export > self._last_charge_w + 300:
                await self._set_battery("laden_overschot", max(self._last_charge_w, 0.0))

    async def _safety_tick(self, _now) -> None:
        """Lichte bewakingslus (elke WATCH_INTERVAL_S): watchdog + stale-guard.

        Los van de plan-tick zodat runaway-detectie en trip-opheffing niet op
        het her-plan-interval hoeven te wachten. Doet zelf geen planning.
        """
        if not self.control_enabled:
            return
        prev_err = self.last_error
        try:
            await self._watchdog()
        except Exception:  # noqa: BLE001 - bewaking mag nooit zelf crashen
            _LOGGER.exception("Wattson safety-tick faalde")
        await self._stale_guard()
        if self.last_error != prev_err:
            for s in self.sensors:
                s.async_write_ha_state()

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
        prev_advies = self.advies
        prev_setpoint = self.setpoint_w
        assist_reason = self.reden if self.assist_active else None
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
        # v1.9: de harde EV-blokkade in de planner (hour_result: p=0) geldt
        # alleen nog als de wallbox-telemetrie NIET vers is — dan kan load_w
        # vervuild zijn met autovermogen en zou de net_home-cap de auto voeden.
        # Met verse wallbox-meting is load_w betrouwbaar EV-gecorrigeerd en mag
        # het plan het huisdeel bedienen (de apply-laag begrenst de
        # output-limiet daarbovenop nogmaals op huislast).
        wb_fresh_w = max(
            self._wallbox_w(self.ent_wallbox_1, self.ent_wallbox_1_home, 180),
            self._wallbox_w(self.ent_wallbox_2, self.ent_wallbox_2_home, 180),
        )
        ev_blind = ev_now and wb_fresh_w <= EV_THRESHOLD_KW * 1000.0

        steps = []
        p1_now = None
        b_chg_now = 0.0
        b_dis_now = 0.0
        for k, (dt, price) in enumerate(prices):
            loc = dt_util.as_local(dt)
            weekend = loc.weekday() >= 5
            load = self.profile.get((loc.hour, int(weekend)), 0.35) * 1000.0
            if k == 0:
                p1 = self._power_w(self.ent_p1)
                p1_now = p1
                # De twee bronnen kunnen twee laders zijn, maar ook wallbox +
                # voertuigtelemetrie van dezelfde sessie; max voorkomt dubbel tellen.
                wb_w = max(
                    self._wallbox_w(self.ent_wallbox_1, self.ent_wallbox_1_home),
                    self._wallbox_w(self.ent_wallbox_2, self.ent_wallbox_2_home),
                )
                if p1 is not None:
                    # actuele huisvraag excl. EV én excl. het eigen accuvermogen
                    # (anders ziet de planner zijn eigen laden als huislast)
                    ent_chg, ent_dis = self._bat_flow_entities()
                    b_chg = self._fresh_power_w(ent_chg, 120)
                    b_dis = self._fresh_power_w(ent_dis, 120)
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
                price_exp=max(price - self.wedge, -0.5),
                load_w=load,
                pv_w=pv.get(dt, 0.0),
                ev_charging=ev_blind if k == 0 else False,
                sell_ok=self._sell_ok(price),
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
        # Reserveer alleen het grootste cumulatieve tekort van het toekomstige
        # plan. Toekomstig laden mag een latere ontlading dus aanvullen; dezelfde
        # kWh wordt niet langer voor ieder ontlaaduur opnieuw gereserveerd.
        _, soc_after_now, _, _ = P.hour_result(steps[0], setpoints[0], soc, self.params)
        self.reserve_kwh = min(
            P.future_reserve_kwh(steps[1:], setpoints[1:], soc_after_now, self.params),
            max(soc - self.params.soc_min_kwh, 0.0),
        )

        # Kijk alleen naar de resterende volledige uren van vandaag. Van de
        # PV-prognose telt 75%; daarna gaan huislast, vrije accuruimte en een
        # onzekerheidsbuffer eraf. Alleen productie die anders waarschijnlijk
        # niet in de accu past, mag actuele netimport alvast verdringen.
        today = dt_util.as_local(prices[0][0]).date()
        solar_steps = [
            st for (dt, _), st in zip(prices[1:], steps[1:])
            if dt_util.as_local(dt).date() == today and st.pv_w > 0.0
        ]
        self.solar_backed_kwh = P.solar_backed_budget_kwh(
            solar_steps,
            soc,
            self.params,
            confidence=SOLAR_FORECAST_CONFIDENCE,
            buffer_kwh=SOLAR_BUFFER_KWH,
            soc_margin_kwh=ASSIST_SOC_MARGE_KWH,
        )
        self.inputs.update({
            "planreserve_kwh": round(self.reserve_kwh, 2),
            "zon_gedekt_beschikbaar_kwh": round(self.solar_backed_kwh, 2),
            "zon_prognose_zekerheid_pct": round(SOLAR_FORECAST_CONFIDENCE * 100),
        })
        # goedkoopste uur waarin het plan nog wil ontladen: een piek NU met een
        # prijs daarboven mag de assist "voordringend" bedienen (frontrun) —
        # dezelfde energie, alleen eerder en waardevoller; de replan verdeelt
        # de rest daarna opnieuw
        dis_prices = [s2.price_imp for s2, sp2 in zip(steps[1:], setpoints[1:]) if sp2 < 0]
        self._plan_dis_floor = min(dis_prices) if dis_prices else None
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

        # wissel-demping: een marginale modewissel (rust <-> laden/ontladen)
        # gaat pas door als het cumulatieve voordeel de drempel overschrijdt.
        # Het voordeel wordt exact bepaald met een extra DP-run waarin het
        # eerste uur wordt vastgezet op de huidige stand; zo stopt het
        # pendelen rond break-even zonder echte marge weg te geven.
        stickable = ("rust", "laden", "ontladen")
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

        if not self.control_enabled or self._tripped:
            return
        if self.ent_zd_hems:
            hems = self.hass.states.get(self.ent_zd_hems)
            if hems is not None and hems.state == "on":
                # de eigen AI-modus is nog de baas: niet dubbel sturen
                self.last_applied = "geblokkeerd: accu-AI (HEMS) staat nog aan"
                return
        self._ev_house = False
        if self.advies == "laden":
            self.assist_active = None
            # is er nú meer PV-overschot dan het geplande laadvermogen, gebruik
            # dan native surplus-matching: die pakt het hele overschot (gratis,
            # volgt wolken vanzelf) i.p.v. een vast setpoint dat de rest laat
            # wegexporteren. Zakt het overschot onder het plan, dan schakelt de
            # volgende tick terug naar vast (net-)laden tegen de dalprijs.
            surplus_w = max(steps[0].pv_w - steps[0].load_w, 0.0)
            if self.caps.surplus_mode and surplus_w >= abs(self.setpoint_w):
                await self._set_battery("laden_overschot", abs(self.setpoint_w))
            else:
                await self._set_battery("laden", abs(self.setpoint_w))
        elif self.advies == "verkopen" and not ev_now:
            self.assist_active = None
            await self._set_battery("verkopen", abs(self.setpoint_w))
        elif self.advies == "ontladen" and not ev_now:
            self.assist_active = None
            await self._set_battery("ontladen", abs(self.setpoint_w))
        elif self.advies == "ontladen" and ev_now:
            # v1.9: EV laadt — de accu dekt alléén het huisdeel. De wallbox
            # meet zijn eigen vermogen, dus de EV-gecorrigeerde huislast
            # (steps[0].load_w) is betrouwbaar. Een vast manual-setpoint op
            # die huislast levert precies dat deel zonder de auto mee te
            # voeden. Voorwaarde: verse wallbox-telemetrie — anders het oude
            # veilige gedrag (rust).
            self.assist_active = None
            wb_fresh = (self._wallbox_w(self.ent_wallbox_1, self.ent_wallbox_1_home, 180)
                        + self._wallbox_w(self.ent_wallbox_2, self.ent_wallbox_2_home, 180))
            huis_w = min(max(steps[0].load_w, 0.0), self.params.p_discharge_max_w)
            if wb_fresh > EV_THRESHOLD_KW * 1000.0 and huis_w >= EV_HOUSE_MIN_W:
                # BEWUST het "verkopen"-pad: dat omzeilt de normale P1-cap,
                # zodat exact de EV-gecorrigeerde huislast als vast vermogen
                # wordt gezet. Hertaxatie volgt iedere plantick.
                await self._set_battery("verkopen", huis_w)
                self._ev_house = True
                self.setpoint_w = -round(huis_w)
                self.reden = f"EV laadt — accu dekt alleen het huisdeel ({huis_w:.0f} W, vast)"
            else:
                await self._set_battery("rust", 0.0)
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
            await self._set_battery("rust", 0.0)

    async def _set_battery(self, action: str, power_w: float, *, p1_cap: bool = True) -> None:
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
            self._stopped_richting = (
                "laden" if self._last_action in ("laden", "laden_overschot") else "ontladen")
            self._stop_grace_until = time.monotonic() + WATCH_STOP_GRACE_S
        self._last_action = action
        self._last_charge_w = applied if action in ("laden", "laden_overschot") else 0.0
        self._last_discharge_w = applied if action in ("ontladen", "verkopen") else 0.0
        if action != "ontladen":
            self._export_recovery_since = None
        if action == "ontladen" and self.caps.p1_matching:
            # referentie voor de volglus: dit is wat de adapter als limiet schreef
            self._tracked_outlim = applied

    def _assist_source_p1(self, p1_w: float) -> float | None:
        """P1 zonder het effect van de lopende realtime-assist.

        Bij native matching is het commando slechts een limiet en niet het
        werkelijke vermogen. Zonder verse accutelemetrie kan Wattson dan niet
        bewijzen dat de bronpiek/het bronoverschot voorbij is; None voorkomt
        dat een succesvol naar nul geregelde P1 als stopbewijs wordt gebruikt.
        Vaste adapters kunnen terugvallen op het werkelijk toegepaste setpoint.
        """
        ent_chg, ent_dis = self._bat_flow_entities()
        if self.assist_active == "laden":
            measured = self._fresh_power_w(ent_chg, 120)
            if measured is not None:
                return A.p1_without_battery(p1_w, charge_w=measured)
            if self._last_action == "laden":  # fixed fallback (generic/marstek)
                return A.p1_without_battery(p1_w, charge_w=self._last_charge_w)
            return None
        if self.assist_active == "ontladen":
            measured = self._fresh_power_w(ent_dis, 120)
            if measured is not None:
                return A.p1_without_battery(p1_w, discharge_w=measured)
            if not self.caps.p1_matching:
                return A.p1_without_battery(p1_w, discharge_w=self._last_discharge_w)
            return None
        return p1_w

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
        measured = self._discharge_feedback()
        if (self.caps.feedback_ack and measured is not None
                and not self._discharge_command_settled()):
            return  # vorige correctie is nog onderweg; wacht op fysieke ack
        # Bij ontbrekende feedback mag exportveiligheid nog steeds verlagen;
        # verhogen blijft via _discharge_target geblokkeerd tot telemetrie er is.
        current_dis = self._last_discharge_w if measured is None else measured
        allowed = max(current_dis + p1, 0.0)
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
    def _export_recovery_check(self, _event) -> None:
        """Herstel uit manual-ontladen als echte bronexport blijft staan.

        De berekening is bewust conservatief: ook het volledige gecommandeerde
        ontlaadvermogen wordt bij P1 teruggeteld als de fysieke telemetrie nog
        achterloopt. Alleen export die dán nog overblijft kan niet door de accu
        zelf zijn veroorzaakt. Een korte hold voorkomt reageren op regelruis.
        """
        active = (
            self.control_enabled
            and not self._tripped
            and self.caps.surplus_mode
            and self._last_action == "ontladen"
            and self.advies in ("ontladen", "bijspringen: ontladen")
        )
        if not active:
            self._export_recovery_since = None
            return
        p1 = self._fresh_power_w(self.ent_p1, 60)
        if p1 is None:
            self._export_recovery_since = None
            return
        source_p1 = A.conservative_source_p1(
            p1,
            self._last_discharge_w,
            self._discharge_feedback(),
        )
        now = time.monotonic()
        self._export_recovery_since, ready = A.export_recovery_state(
            source_p1,
            threshold_w=DISCHARGE_EXPORT_ABORT_W,
            now_s=now,
            since_s=self._export_recovery_since,
            hold_s=DISCHARGE_EXPORT_ABORT_HOLD_S,
        )
        if ready and not self._export_recovery_pending:
            self._export_recovery_pending = True
            self.hass.async_create_task(self._export_recovery_apply())

    async def _export_recovery_apply(self) -> None:
        """Stop vastgelopen ontladen en promoveer bruikbare export naar laden."""
        try:
            if not (self.control_enabled and not self._tripped
                    and self.caps.surplus_mode and self._last_action == "ontladen"):
                return
            p1 = self._fresh_power_w(self.ent_p1, 60)
            if p1 is None:
                return
            source_p1 = A.conservative_source_p1(
                p1,
                self._last_discharge_w,
                self._discharge_feedback(),
            )
            if source_p1 > -DISCHARGE_EXPORT_ABORT_W:
                return  # export verdween tussen callback en service-call

            prev = (self.advies, self.last_applied)
            soc_pct = self._f(self.ent_soc)
            prijs = self._current_price()
            can_store = (soc_pct is not None and
                         soc_pct / 100.0 * self.params.capacity_kwh
                         < self.params.soc_max_kwh - ASSIST_MAX_SOC_MARGIN_KWH)
            eta_rt = P.eta_oneway(800.0, self.params) ** 2
            charge_economic = (prijs is not None and
                               self._max_future * eta_rt > prijs)

            # Eerst expliciet rust: daarmee sluit de uitrichting en krijgt de
            # watchdog stop-grace voor eventuele fysieke uitloop. Daarna pas
            # de tegengestelde richting openen.
            self.assist_active = None
            self._assist_end_since = None
            await self._set_battery("rust", 0.0)
            if can_store and charge_economic:
                target = min(max(-source_p1, self.caps.min_setpoint_w),
                             self.params.p_charge_max_w)
                await self._set_battery("laden_overschot", target)
                self.assist_active = "laden"
                self._assist_started = time.monotonic()
                self.advies = "bijspringen: laden"
                self.setpoint_w = round(self._last_charge_w)
                self.reden = (f"sterke bronexport {-source_p1:.0f} W bevestigd — "
                              "ontladen afgebroken en overschotladen hervat")
            else:
                self.advies = "rust"
                self.setpoint_w = 0.0
                waarom = "accu vrijwel vol" if not can_store else "opslaan niet economisch"
                self.reden = (f"sterke bronexport {-source_p1:.0f} W bevestigd — "
                              f"ontladen afgebroken ({waarom})")
            self._log_decision(prev)
            for s in self.sensors:
                s.async_write_ha_state()
        finally:
            self._export_recovery_since = None
            self._export_recovery_pending = False

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
        if self._tripped or self._export_recovery_pending:
            return
        p1 = self._fresh_power_w(self.ent_p1, 120)
        soc_pct = self._f(self.ent_soc)
        prijs = self._current_price()
        if p1 is None or soc_pct is None or prijs is None:
            return
        soc = soc_pct / 100.0 * self.params.capacity_kwh
        vrij = soc - self.params.soc_min_kwh - self.reserve_kwh - ASSIST_SOC_MARGE_KWH
        physical_free = soc - self.params.soc_min_kwh - ASSIST_SOC_MARGE_KWH
        # frontrun: is de prijs nú minstens zo hoog als het goedkoopste uur
        # waarin het plan deze energie later toch al wil ontladen, dan is een
        # piek nu bedienen strikt beter — de planreserve telt dan niet als
        # blokkade en de economie-check (herladen tegen netprijs) evenmin
        floor = self._plan_dis_floor
        frontrun = floor is not None and prijs >= floor - 0.005
        vrij_dis = physical_free if frontrun else vrij
        eta_rt = P.eta_oneway(800.0, self.params) ** 2
        prev = (self.advies, self.last_applied)
        source_p1 = self._assist_source_p1(p1)
        import_need = source_p1 if source_p1 is not None else p1
        solar_allowed = self.solar_backed_kwh > 0.01 and (
            self.assist_active == "ontladen" or import_need > SOLAR_ASSIST_IMPORT_W)
        if solar_allowed:
            # Maximaal het zon-gedekte budget boven op de gewone planreserve.
            # Iedere plantick herberekent dit met de dan actuele SoC; naarmate
            # de accu leger wordt, groeit de ruimte en krimpt dit budget mee.
            vrij_dis = max(
                vrij_dis,
                min(max(physical_free, 0.0), self.solar_backed_kwh),
            )

        peak_ended = source_p1 is not None and source_p1 < ASSIST_STOP_W
        surplus_ended = source_p1 is not None and source_p1 > -ASSIST_STOP_W
        # Opwarmvenster: direct na de start regelt native matching P1 al naar
        # ~0 terwijl het gemeten accuvermogen nog een verse "0" van vóór de
        # start leest (60s-poll, write-on-change). source_p1 rekent het
        # accuvermogen dan niet terug en "voorbij" is vals — geen stopbewijs.
        # Harde stops (SoC vol, reserve, EV, prijsconditie) blijven gelden.
        if self.assist_active and time.monotonic() - self._assist_started < ASSIST_MIN_RUN_S:
            peak_ended = surplus_ended = False
        # Stop-dwell: "voorbij" moet ASSIST_STOP_GRACE_S aanhouden voordat we
        # echt stoppen. Wolk-dips en apparaat-eigen pauzes rond de drempel
        # cyclen anders het relais (~4-5 min aan / 25 s uit); in de grace
        # moduleert native matching zelf mee, dus dit kost geen netstroom.
        ended_now = peak_ended if self.assist_active == "ontladen" else surplus_ended
        if self.assist_active:
            if ended_now:
                if self._assist_end_since is None:
                    self._assist_end_since = time.monotonic()
            else:
                self._assist_end_since = None
            held = (self._assist_end_since is not None
                    and time.monotonic() - self._assist_end_since >= ASSIST_STOP_GRACE_S)
            peak_ended = held if self.assist_active == "ontladen" else False
            surplus_ended = held if self.assist_active == "laden" else False
        charge_full = soc >= self.params.soc_max_kwh - ASSIST_MAX_SOC_MARGIN_KWH
        discharge_economic = prijs > self._cheap_future / max(eta_rt, 0.5)
        charge_economic = self._max_future * eta_rt > prijs
        discharge_allowed = discharge_economic or frontrun or solar_allowed
        if self.assist_active == "ontladen" and (
                peak_ended or vrij_dis <= 0 or self._ev_charging()
                or not discharge_allowed):
            self.assist_active = None
            await self._set_battery("rust", 0.0)
            self.advies = "rust"
            self.setpoint_w = 0.0
            self.reden = "bijspringen klaar (piek, reserve of prijsconditie voorbij)"
        elif self.assist_active == "laden" and (
                surplus_ended or charge_full or not charge_economic):
            self.assist_active = None
            await self._set_battery("rust", 0.0)
            self.advies = "rust"
            self.setpoint_w = 0.0
            self.reden = ("bijspringen klaar (maximale SoC bereikt)" if charge_full
                          else "bijspringen klaar (overschot of prijsconditie voorbij)")
        elif self.assist_active == "ontladen":
            if source_p1 is None:
                return
            target = min(max(source_p1, 0.0), self.params.p_discharge_max_w)
            if abs(target - self._last_discharge_w) < ASSIST_POWER_DEADBAND_W:
                return
            await self._set_battery("ontladen", target, p1_cap=False)
            self.setpoint_w = -round(self._last_discharge_w)
            self.reden = f"piek volgt bronvraag {source_p1:.0f} W"
        elif self.assist_active == "laden":
            # Native surplus-matching regelt zelf continu. Vaste adapters
            # krijgen hier een nieuw setpoint op basis van de bronflow, zodat
            # een afnemend overschot niet ongemerkt netimport veroorzaakt.
            if self.caps.surplus_mode or source_p1 is None:
                return
            target = min(max(-source_p1, 0.0), self.params.p_charge_max_w)
            if abs(target - self._last_charge_w) < ASSIST_POWER_DEADBAND_W:
                return
            await self._set_battery("laden", target)
            self.setpoint_w = round(self._last_charge_w)
            self.reden = f"zonoverschot volgt bronexport {-source_p1:.0f} W"
        elif (((p1 > ASSIST_IMPORT_W and (discharge_economic or frontrun))
               or (p1 > SOLAR_ASSIST_IMPORT_W and solar_allowed))
              and not self._ev_charging() and vrij_dis > 0):
            self.assist_active = "ontladen"
            self._assist_started = time.monotonic()
            self._assist_end_since = None
            await self._set_battery("ontladen", min(p1, self.params.p_discharge_max_w))
            self.advies = "bijspringen: ontladen"
            self.setpoint_w = -round(self._last_discharge_w)
            if p1 > SOLAR_ASSIST_IMPORT_W and solar_allowed:
                self.reden = (f"netimport {p1:.0f} W zon-gedekt; "
                              f"{self.solar_backed_kwh:.2f} kWh verwacht hervulbaar")
            elif discharge_economic:
                self.reden = f"piek {p1:.0f} W, prijs €{prijs:.3f} > herlaadprijs"
            else:
                self.reden = (f"piek {p1:.0f} W, prijs €{prijs:.3f} >= "
                              f"plan-ontlaadvloer €{floor:.3f} (frontrun)")
        elif (p1 < -ASSIST_EXPORT_W and soc < self.params.soc_max_kwh - 0.2
              and charge_economic):
            self.assist_active = "laden"
            self._assist_started = time.monotonic()
            self._assist_end_since = None
            await self._set_battery("laden_overschot", min(-p1, self.params.p_charge_max_w))
            self.advies = "bijspringen: laden"
            self.setpoint_w = round(self._last_charge_w)
            self.reden = f"zonoverschot {-p1:.0f} W, piek later €{self._max_future:.3f}"
        else:
            return
        self._log_decision(prev)
        for s in self.sensors:
            s.async_write_ha_state()

    @callback
    def _ev_guard(self, _event) -> None:
        """Auto begint te laden -> ontladen/verkopen direct stoppen.

        Uitzondering (v1.9): bewust huisdeel-ontladen tijdens een EV-sessie
        (_ev_house) is door de plantick zelf gezet en mag blijven staan —
        de output-limiet staat dan op huislast, niet op vol vermogen.
        """
        if self._ev_house:
            return
        if self.control_enabled and self._ev_charging() and (
                self.advies in ("ontladen", "verkopen") or self.assist_active == "ontladen"):
            prev = (self.advies, self.last_applied)
            self.assist_active = None
            self.hass.async_create_task(self._set_battery("rust", 0.0))
            self.advies = "rust (EV-guard)"
            self.setpoint_w = 0.0
            self.reden = "EV begon te laden — ontladen direct gestopt"
            self._log_decision(prev)  # ingreep zichtbaar in het historie-attribuut
            for s in self.sensors:
                s.async_write_ha_state()
