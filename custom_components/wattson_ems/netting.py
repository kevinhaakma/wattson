"""Jaarsaldering-positie: hoeveel netto-importruimte is er dit jaar nog.

Onder saldering is een geëxporteerde kWh het volle uurtarief waard — maar
alleen zolang de jaarsom export onder de jaarsom import blijft. Daarboven
telt alleen de kale prijs (+ leveranciersbonus). Een systeem dat met wedge 0
blijft rekenen terwijl de ruimte opraakt, overwaardeert export structureel
(validatie-finding 2026-07-16). Deze monitor meet de positie via de
recorder-statistieken van de totaaltellers; de scenario-laag laat de wedge
geleidelijk naar wedge_post schuiven zodra de ruimte onder de blend-marge
komt — glijdend, dus zonder gedragsflappen rond een harde grens.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

REFRESH_S = 3600  # jaarpositie verschuift langzaam; 1x/uur is ruim voldoende


class NettingMonitor:
    """Houdt de salderingsruimte (import − export, jaar tot nu) bij."""

    def __init__(self, hass, ent_import: list[str], ent_export: list[str]) -> None:
        self.hass = hass
        self.ent_import = [e for e in (ent_import or []) if e]
        self.ent_export = [e for e in (ent_export or []) if e]
        self.headroom_kwh: float | None = None
        self._checked = None

    @property
    def configured(self) -> bool:
        return bool(self.ent_import and self.ent_export)

    async def refresh(self) -> None:
        """Ververs de jaarpositie (throttled). Faalt stil naar None: zonder
        meting blijft de statische wedge gelden — nooit een plan blokkeren."""
        if not self.configured:
            return
        now = dt_util.utcnow()
        if self._checked and now - self._checked < timedelta(seconds=REFRESH_S):
            return
        self._checked = now
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )

            jan1 = dt_util.now().replace(month=1, day=1, hour=0, minute=0,
                                         second=0, microsecond=0)
            ids = set(self.ent_import + self.ent_export)
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, dt_util.as_utc(jan1),
                None, ids, "month", None, {"change"},
            )

            def total(ents: list[str]) -> float:
                return sum((p.get("change") or 0.0)
                           for e in ents for p in stats.get(e, []))

            self.headroom_kwh = total(self.ent_import) - total(self.ent_export)
        except Exception:  # noqa: BLE001 - bewaking mag het plannen nooit breken
            _LOGGER.exception("Wattson: saldering-positie ophalen faalde")
            self.headroom_kwh = None
