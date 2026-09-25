"""Veiligheid: watchdog (runaway-detectie) en stale-guard (stille telemetrie).

Los van de plan-tick: bewaking mag nooit op het her-plan-interval wachten.
De coordinator delegeert hierheen; alle trip- en grace-status leeft hier.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .const import (
    CMD_ACK_BACKOFF_S,
    CMD_ACK_MIN_W,
    CMD_ACK_S,
    GEENDATA_RELOAD_COOLDOWN_S,
    GEENDATA_RELOAD_S,
    GEENDATA_STOP_S,
    STARTUP_GRACE_S,
    WATCH_RUNAWAY_W,
    WATCH_STOP_GRACE_S,
)

_LOGGER = logging.getLogger(__name__)


class Safety:
    """Runaway- en stilte-bewaking; houdt de trip-status bij."""

    def __init__(self, c) -> None:
        self.c = c
        self.tripped: str | None = None    # None | "laden" | "ontladen"
        self.watch_error: str | None = None
        self._stop_grace_until = 0.0       # tot dit monotonic-moment is uitloop van
        self._stopped_richting: str | None = None  # ...deze richting geen runaway
        self._data_ok_at: datetime | None = None
        self._safe_stopped = False
        # Stiltestop tijdens ACTIEVE sturing (laden/ontladen/verkopen): dan
        # weten we echt niet wat het apparaat doet -> commando's blokkeren.
        # Stiltestop in rust is alleen idle-hardening (limieten dicht): de
        # write-on-change-telemetrie staat dan per definitie stil, en een nieuw
        # commando is juist het enige dat weer verse data kan opleveren.
        self._stopped_while_active = False
        self._safe_stop_at: datetime | None = None
        self._started_at = time.monotonic()
        # commando-verificatie (zie command_guard)
        self._ack_cmd_at: datetime | None = None
        self._ack_stage = 0
        self._ack_stage_at: datetime | None = None
        self._ack_muted_until: datetime | None = None
        self.ack_error: str | None = None

    @property
    def telemetry_blocked(self) -> bool:
        """Een stiltestop tijdens actieve sturing blijft gelden tot de
        stale-guard verse data ziet. Een stop in rust blokkeert niet (zie
        __init__): anders ontstaat een deadlock waarin het commando dat verse
        data zou opleveren zelf wordt geweigerd (15-09-2026: 3,5 uur geen
        verkoop bij €0,45 met een volle accu)."""
        return self._safe_stopped and self._stopped_while_active

    def note_own_stop(self, richting: str) -> None:
        """Eigen stopcommando geregistreerd: het apparaat loopt (cloud-latentie)
        nog even uit in de oude richting — dat is geen runaway."""
        self._stopped_richting = richting
        self._stop_grace_until = time.monotonic() + WATCH_STOP_GRACE_S

    def _expected_direction(self) -> tuple[bool, bool]:
        """(laden verwacht, ontladen verwacht) op basis van het actuele advies."""
        c = self.c
        # assist_active is de stuurwaarheid. Het advies kan tijdens een
        # gelijktijdige plan-tick kort veranderen en mag de watchdog dan niet
        # laten ingrijpen tegen een actie die Wattson zelf nog beheert.
        chg = c.mode.expects_charge or c.assist_active == "laden"
        dis = c.mode.expects_discharge or c.assist_active == "ontladen"
        return chg, dis

    async def watchdog(self) -> None:
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
        c = self.c
        if not c.control_enabled:
            return
        if getattr(c, "_decision_pending", False):
            # plan-tick onderweg: c.mode is een ruw tussenbesluit dat de
            # wisseldemping nog kan terugdraaien en dat nog niet is uitgevoerd.
            # Geen oordeel; de volgende bewakingstick ziet het echte besluit.
            return
        ent_chg, ent_dis = c.bat_flow_entities()
        dis = c.t.fresh_power_w(ent_dis)
        chg = c.t.fresh_power_w(ent_chg)
        if dis is None and chg is None:
            return  # geen vers bewijs: geen oordeel
        verwacht_chg, verwacht_dis = self._expected_direction()
        afwijking = None
        richting = None
        if chg is not None and chg > WATCH_RUNAWAY_W and not verwacht_chg:
            afwijking = f"accu laadt {chg:.0f} W terwijl '{c.advies}' gecommandeerd is"
            richting = "laden"
        elif dis is not None and dis > WATCH_RUNAWAY_W and not verwacht_dis:
            afwijking = f"accu ontlaadt {dis:.0f} W terwijl '{c.advies}' gecommandeerd is"
            richting = "ontladen"
        elif dis is not None and dis > c.params.p_discharge_max_w + 500:
            afwijking = f"ontlaadvermogen {dis:.0f} W ver boven limiet"
            richting = "ontladen"
        if (afwijking and self.c._last_action is None
                and time.monotonic() - self._started_at < STARTUP_GRACE_S):
            # (her)start-grace: er is nog geen eigen commando gegeven, dus dit
            # vermogen is geërfd van vóór de reload (de vorige instantie
            # stuurde rust bij unload; het apparaat loopt door cloud-latentie
            # nog uit). De stop-grace-status zelf overleeft een reload niet.
            _LOGGER.debug(
                "Wattson watchdog: %s genegeerd (startup-grace)", afwijking)
            return
        if afwijking and richting == self._stopped_richting and time.monotonic() < self._stop_grace_until:
            # uitloop van een zojuist zelf gestopte actie: het apparaat heeft
            # cloud-latentie en mag binnen de grace nog in die richting actief
            # zijn — geen runaway; na de grace geldt de normale bewaking weer
            _LOGGER.debug("Wattson watchdog: %s genegeerd (stop-grace na eigen stopcommando)", afwijking)
            return
        if afwijking:
            # eerst registreren en melden, dan pas stoppen: ook als het
            # stop-commando faalt is de ingreep zichtbaar
            self.tripped = richting
            c.assist_active = None
            self.watch_error = f"WATCHDOG: {afwijking}"
            _LOGGER.warning("Wattson watchdog: %s", afwijking)
            c.hass.bus.async_fire("logbook_entry", {
                "name": "Wattson", "message": f"WATCHDOG ingegrepen: {afwijking}",
                "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})
            await c.emergency_stop(richting)
        elif self.tripped:
            # alleen opheffen op vers bewijs dat de runaway-richting stil ligt
            gestopt = (self.tripped == "laden" and chg is not None and chg < 50) or (
                self.tripped == "ontladen" and dis is not None and dis < 50)
            if gestopt:
                self.tripped = None
                self.watch_error = None
                c.hass.bus.async_fire("logbook_entry", {
                    "name": "Wattson", "message": "WATCHDOG opgeheven, sturing hervat",
                    "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})
                # direct herplannen: tijdens de trip weigerde de adapter elk
                # commando, dus het staande advies is nooit uitgevoerd; zonder
                # her-tick blijft de sturing tot de volgende plan-tick dood
                c.hass.async_create_task(c._tick(None))

    async def stale_guard(self) -> None:
        """Telemetrie stil met sturing aan: na GEENDATA_STOP_S veilig stoppen
        met dichte limieten tot er weer data is."""
        c = self.c
        if not c.control_enabled:
            return
        ent_chg, ent_dis = c.bat_flow_entities()
        # Levend apparaat = elke entiteit van hetzelfde device die recent
        # meldde (rssi, spanning, temperatuur). Onveranderde SoC/vermogens bij
        # een levende integratie zijn dan de waarheid, geen stilte (16-09:
        # laadcommando bij 89% niet uitgevoerd -> 0 W bleef 0 W -> onterechte
        # blokkerende stop terwijl rssi elke 10 s binnenkwam).
        vers = (
            c.t.fresh(c.ent_soc, GEENDATA_STOP_S) is not None
            or c.t.fresh_power_w(ent_chg, GEENDATA_STOP_S) is not None
            or c.t.fresh_power_w(ent_dis, GEENDATA_STOP_S) is not None
            or c.t.device_alive(c.ent_soc, GEENDATA_STOP_S) is True
        )
        now = dt_util.utcnow()
        if vers:
            self._data_ok_at = now
            self._safe_stopped = False
            self._stopped_while_active = False
            return
        if self._data_ok_at is None:
            self._data_ok_at = now
            return
        actief = c._last_action in ("laden", "laden_overschot", "ontladen", "verkopen")
        cmd_at = getattr(c, "_last_command_at", None)
        # Na een stop in rust is een nieuw ACTIEF commando doorgelaten: dat
        # opent een nieuw meetvenster vanaf het commando. Levert het apparaat
        # binnen GEENDATA_STOP_S geen verse data, dan volgt opnieuw een stop —
        # nu wél als blokkerende actieve-stop.
        if (self._safe_stopped and actief and cmd_at is not None
                and self._safe_stop_at is not None and cmd_at >= self._safe_stop_at):
            self._safe_stopped = False
            self._stopped_while_active = False
            self._data_ok_at = cmd_at
        stil = (now - self._data_ok_at).total_seconds()
        # Herladen alleen als de integratie écht niets meer MELDT (last_reported),
        # niet als de waarden alleen constant zijn (last_updated staat dan ook
        # stil — bewuste idle-hardening hieronder blijft daarop werken).
        gemeld = [c.t.live_power_w(e, GEENDATA_RELOAD_S) for e in (c.ent_soc, ent_chg, ent_dis) if e]
        stil_melding = all(v is None for v in gemeld)
        # Alleen herladen als er een ACTIEF commando staat dat niet terugkomt:
        # in rust zijn constante waarden normaal (06-09 15:17/15:32: reload elke
        # 15 min in rust, elke reload = relaisklik).
        # ...en dat commando moet zelf al GEENDATA_RELOAD_S oud zijn: stilte uit de
        # rustperiode ervoor telt niet (06-09 18:00: reload op het startmoment van
        # de ontlading -> integratie 3 min weg, klik, late start)
        cmd_oud = cmd_at is not None and (now - cmd_at).total_seconds() > GEENDATA_RELOAD_S
        last_reload = getattr(self, "_last_reload_at", None)
        if (stil > GEENDATA_RELOAD_S and stil_melding and actief and cmd_oud and c.ent_soc
                and (last_reload is None or (now - last_reload).total_seconds() > GEENDATA_RELOAD_COOLDOWN_S)):
            await self._reload_battery_integration(f"accu-telemetrie {stil:.0f} s stil")
        if not self._safe_stopped and stil > GEENDATA_STOP_S:
            self._safe_stopped = True
            self._stopped_while_active = actief
            self._safe_stop_at = now
            c.assist_active = None
            await c.emergency_stop(None)
            if actief:
                c.reden = "telemetrie stil — veilig gestopt"
                bericht = f"telemetrie > {GEENDATA_STOP_S / 60:.0f} min stil: accu veilig gestopt"
            else:
                c.reden = "telemetrie stil in rust — limieten dicht"
                bericht = (f"telemetrie > {GEENDATA_STOP_S / 60:.0f} min stil in rust: "
                           "limieten dicht, nieuw commando blijft mogelijk")
            c.hass.bus.async_fire("logbook_entry", {
                "name": "Wattson", "message": bericht,
                "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})

    async def _reload_battery_integration(self, waarom: str) -> bool:
        """Herlaad de config-entry achter de SoC-sensor (met cooldown)."""
        c = self.c
        now = dt_util.utcnow()
        last_reload = getattr(self, "_last_reload_at", None)
        if not c.ent_soc or (last_reload is not None
                             and (now - last_reload).total_seconds() < GEENDATA_RELOAD_COOLDOWN_S):
            return False
        self._last_reload_at = now
        _LOGGER.warning("Wattson: %s — accu-integratie herladen via %s", waarom, c.ent_soc)
        try:
            await c.hass.services.async_call(
                "homeassistant", "reload_config_entry", {"entity_id": c.ent_soc}, blocking=False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Wattson: herladen accu-integratie faalde")
            return False
        return True

    def _ack_reset(self) -> None:
        if self._ack_stage:
            _LOGGER.info("Wattson commando-verificatie: accu voert het commando weer uit")
        self._ack_cmd_at = None
        self._ack_stage = 0
        self._ack_stage_at = None
        self.ack_error = None

    async def command_guard(self) -> None:
        """Niet-uitgevoerd commando: laden/ontladen/verkopen staat >= CMD_ACK_MIN_W
        gecommandeerd, maar het verse accuvermogen in die richting blijft < 50 W.

        25-09-2026: Wattson 'laadde' 4,5 u met 2000 W op papier terwijl de accu
        op 29 % bleef. Het apparaat leefde (rssi/spanning), dus de stale-guard
        zweeg; de watchdog kijkt alleen naar de VERKEERDE richting. Trappen,
        elk na CMD_ACK_S zonder bewijs: 1) commando opnieuw sturen (adapter
        reassert), 2) accu-integratie herladen, 3) veilig stoppen + logboek +
        persistente melding, daarna CMD_ACK_BACKOFF_S niet opnieuw escaleren.
        """
        c = self.c
        if not c.control_enabled or getattr(c, "_decision_pending", False):
            return
        now = dt_util.utcnow()
        if self._ack_muted_until is not None and now < self._ack_muted_until:
            return
        action = c._last_action
        cmd_at = getattr(c, "_last_command_at", None)
        if action not in ("laden", "ontladen", "verkopen") or cmd_at is None:
            self._ack_reset()
            return
        setpoint = c._last_charge_w if action == "laden" else c._last_discharge_w
        if setpoint < CMD_ACK_MIN_W:
            self._ack_reset()
            return
        if self._ack_cmd_at != cmd_at:
            # nieuw commando: nieuw meetvenster, trappen opnieuw
            self._ack_cmd_at = cmd_at
            self._ack_stage = 0
            self._ack_stage_at = cmd_at
            self.ack_error = None
        ent_chg, ent_dis = c.bat_flow_entities()
        ent = ent_chg if action == "laden" else ent_dis
        flow = c.t.live_power_w(ent, CMD_ACK_S)
        if flow is None:
            return  # geen vers bewijs: domein van de stale-guard
        if flow >= 50.0:
            if self._ack_stage:
                self._ack_reset()
                self._ack_cmd_at = cmd_at
                self._ack_stage_at = now
            return
        if (now - self._ack_stage_at).total_seconds() < CMD_ACK_S:
            return
        self._ack_stage += 1
        self._ack_stage_at = now
        omschrijving = f"{action} {setpoint:.0f} W gecommandeerd, accu levert {flow:.0f} W"
        if self._ack_stage == 1:
            _LOGGER.warning("Wattson commando-verificatie: %s — opnieuw sturen", omschrijving)
            c.hass.bus.async_fire("logbook_entry", {
                "name": "Wattson", "message": f"commando niet uitgevoerd ({omschrijving}) — opnieuw gestuurd",
                "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})
            reassert = getattr(c.adapter_impl, "reassert", None)
            try:
                if reassert is not None:
                    await reassert(action, setpoint)
                else:
                    await c.adapter_impl.apply(action, setpoint, p1_cap=False)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Wattson commando-verificatie: opnieuw sturen faalde")
            return
        if self._ack_stage == 2:
            c.hass.bus.async_fire("logbook_entry", {
                "name": "Wattson", "message": f"commando nog steeds niet uitgevoerd ({omschrijving}) — accu-integratie herladen",
                "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})
            await self._reload_battery_integration(f"commando niet uitgevoerd ({omschrijving})")
            return
        # trap 3: veilig stoppen en melden
        self.ack_error = f"accu voert commando niet uit: {omschrijving}"
        _LOGGER.error("Wattson commando-verificatie: %s — veilig gestopt", omschrijving)
        c.assist_active = None
        await c.emergency_stop(None)
        c.reden = "accu voert commando niet uit — veilig gestopt"
        c.hass.bus.async_fire("logbook_entry", {
            "name": "Wattson",
            "message": f"accu voert commando niet uit ({omschrijving}) na opnieuw sturen en herladen — veilig gestopt, volgende poging over {CMD_ACK_BACKOFF_S // 60} min",
            "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})
        try:
            await c.hass.services.async_call("persistent_notification", "create", {
                "notification_id": "wattson_commando",
                "title": "Wattson: accu reageert niet",
                "message": (f"{omschrijving}. Opnieuw sturen en herladen hielpen niet; accu veilig gestopt. "
                            "Check de Zendure-verbinding (zenSDK, 192.168.1.118) en of HA's HTTP-stack hangt "
                            "(zie automation 'P1 - Auto-heal')."),
            }, blocking=False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Wattson commando-verificatie: melding faalde")
        self._ack_muted_until = now + timedelta(seconds=CMD_ACK_BACKOFF_S)
        self._ack_stage = 0
        self._ack_cmd_at = None

    async def tick(self) -> None:
        """Lichte bewakingslus (elke WATCH_INTERVAL_S): watchdog + stale-guard.

        Los van de plan-tick zodat runaway-detectie en trip-opheffing niet op
        het her-plan-interval hoeven te wachten. Doet zelf geen planning.
        """
        c = self.c
        if not c.control_enabled:
            return
        prev_err = c.last_error
        try:
            await self.watchdog()
        except Exception:  # noqa: BLE001 - bewaking mag nooit zelf crashen
            _LOGGER.exception("Wattson safety-tick faalde")
        await self.stale_guard()
        try:
            await self.command_guard()
        except Exception:  # noqa: BLE001 - bewaking mag nooit zelf crashen
            _LOGGER.exception("Wattson commando-verificatie faalde")
        if c.last_error != prev_err:
            c.write_entities()
