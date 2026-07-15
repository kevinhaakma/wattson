"""Veiligheid: watchdog (runaway-detectie) en stale-guard (stille telemetrie).

Los van de plan-tick: bewaking mag nooit op het her-plan-interval wachten.
De coordinator delegeert hierheen; alle trip- en grace-status leeft hier.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from homeassistant.util import dt as dt_util

from .const import GEENDATA_STOP_S, WATCH_RUNAWAY_W, WATCH_STOP_GRACE_S

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
        chg = c.advies in ("laden", "bijspringen: laden") or c.assist_active == "laden"
        dis = (c.advies in ("ontladen", "verkopen", "bijspringen: ontladen")
               or c.assist_active == "ontladen")
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
                # commando, dus het staande advies is nooit uitgevoerd. Zonder
                # her-tick blijft de sturing tot de volgende plan-tick (10 min)
                # dood staan (incident 2026-07-15: 2× na HA-herstart — apparaat
                # liep nog op het pre-restart-commando, watchdog tripte op
                # "laadt terwijl 'geen data'", opheffing volgde maar niets
                # voerde het plan alsnog uit).
                c.hass.async_create_task(c._tick(None))

    async def stale_guard(self) -> None:
        """Telemetrie stil met sturing aan: na GEENDATA_STOP_S veilig stoppen
        met dichte limieten tot er weer data is."""
        c = self.c
        if not c.control_enabled:
            return
        ent_chg, ent_dis = c.bat_flow_entities()
        vers = (
            c.t.fresh(c.ent_soc, GEENDATA_STOP_S) is not None
            or c.t.fresh_power_w(ent_chg, GEENDATA_STOP_S) is not None
            or c.t.fresh_power_w(ent_dis, GEENDATA_STOP_S) is not None
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
            c.assist_active = None
            await c.emergency_stop(None)
            c.reden = "telemetrie stil — veilig gestopt"
            c.hass.bus.async_fire("logbook_entry", {
                "name": "Wattson",
                "message": f"telemetrie > {GEENDATA_STOP_S / 60:.0f} min stil: accu veilig gestopt",
                "entity_id": "sensor.wattson_advies", "domain": "wattson_ems"})

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
        if c.last_error != prev_err:
            c.write_entities()
