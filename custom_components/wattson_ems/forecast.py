"""Voorspellingen: huislast-profiel en PV-curve.

LoadProfile: (uur, weekend)-profiel. Het getrainde profiel uit params.json is
de BASELINE (prior); elke echt gemeten uurlast trekt het betreffende slot
daarna via een exponentieel gemiddelde naar de werkelijkheid toe (observe).
Het profiel volgt zo vanzelf seizoenen en gedragsveranderingen (airco-zomers,
stookwinters, nieuwe apparaten) zonder handmatige hertraining. De geleerde
waarden overleven een herstart via een HA-Store (zie coordinator).
PvCurve: verdeelt de dag-prognoses over een daglicht-bel; het huidige uur
komt van de echte PV-meting (geen bias — dat is een meting, geen forecast).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from .const import DAGLICHT
from .telemetry import Telemetry


class LoadProfile:
    """Verwachte huislast (W) per lokaal uur: baseline + adaptieve laag."""

    DEFAULT_KWH = 0.35
    # Gewicht van één nieuwe uurmeting in het lopende gemiddelde. Elk slot
    # wordt 1x per dag waargenomen (doordeweekse slots 5x/week): met 0,25
    # draagt een week verse metingen ~76% van de slotwaarde — snel genoeg om
    # een hittegolf te volgen, traag genoeg om één feestje te negeren.
    ALPHA = 0.25
    # Fysieke grenzen op een uurgemiddelde; daarbuiten is het meetfout.
    MAX_W = 10000.0

    def __init__(self, profile: dict[tuple[int, int], float]) -> None:
        self.baseline = profile
        self.learned: dict[tuple[int, int], float] = {}

    @staticmethod
    def _slot(dt: datetime) -> tuple[int, int]:
        loc = dt_util.as_local(dt)
        return (loc.hour, int(loc.weekday() >= 5))

    def expected_w(self, dt: datetime) -> float:
        slot = self._slot(dt)
        kwh = self.learned.get(slot, self.baseline.get(slot, self.DEFAULT_KWH))
        return kwh * 1000.0

    # ---------- adaptieve laag ----------
    def observe(self, hour_dt: datetime, mean_w: float) -> None:
        """Verwerk één afgerond klokuur echte, EV/accu-gecorrigeerde huislast."""
        if not 0.0 <= mean_w <= self.MAX_W:
            return
        slot = self._slot(hour_dt)
        prev = self.learned.get(slot, self.baseline.get(slot, self.DEFAULT_KWH))
        self.learned[slot] = round(prev + (mean_w / 1000.0 - prev) * self.ALPHA, 4)

    def as_stored(self) -> dict[str, float]:
        return {f"{h}|{w}": v for (h, w), v in self.learned.items()}

    def restore(self, data: dict | None) -> None:
        for k, v in (data or {}).items():
            try:
                h, w = (int(x) for x in k.split("|"))
                v = float(v)
            except (ValueError, TypeError):
                continue
            if 0 <= h <= 23 and w in (0, 1) and 0.0 <= v <= self.MAX_W / 1000.0:
                self.learned[(h, w)] = v


class PvCurve:
    """PV-prognose (W per uur) uit de dagtotalen van de forecast-sensor."""

    def __init__(self, telemetry: Telemetry, ent_pv_now: str,
                 ent_pv_remain: str, ent_pv_tomorrow: str, bias: float,
                 ent_hour_now: str = "", ent_hour_next: str = "") -> None:
        self.t = telemetry
        self.ent_pv_now = ent_pv_now
        self.ent_pv_remain = ent_pv_remain
        self.ent_pv_tomorrow = ent_pv_tomorrow
        self.bias = bias
        # optionele uur-forecast van de bron zelf (Forecast.Solar): die weet
        # van scheve dagen (heiige ochtend, klare middag) wat de bel niet weet
        self.ent_hour_now = ent_hour_now
        self.ent_hour_next = ent_hour_next

    def remain_kwh(self) -> float:
        return (self.t.energy_kwh(self.ent_pv_remain) or 0.0) * self.bias

    def tomorrow_kwh(self) -> float:
        return (self.t.energy_kwh(self.ent_pv_tomorrow) or 0.0) * self.bias

    def curve(self, hours: list[datetime],
              dt_h: list[float] | None = None) -> dict[datetime, float]:
        """Verdeel de PV-forecast dagtotalen over een daglicht-bel (W per slot).

        hours zijn de slot-starttijden, dt_h hun lengte in uren (default 1)."""
        dts = dt_h or [1.0] * len(hours)
        remain = self.remain_kwh()
        tomorrow = self.tomorrow_kwh()
        lo, hi = DAGLICHT
        # HA-tijdzone, niet de host-OS-tijdzone (docker draait vaak op UTC)
        today = dt_util.now().date()

        def bell(loc):  # gewicht per lokaal tijdstip
            h = loc.hour + loc.minute / 60.0
            if h < lo or h >= hi:
                return 0.0
            return math.sin((h - lo) / (hi - lo) * math.pi) ** 2

        out = {}
        for day, budget in ((today, remain), (today + timedelta(days=1), tomorrow)):
            idx = [i for i, dt in enumerate(hours) if dt_util.as_local(dt).date() == day]
            weights = [bell(dt_util.as_local(hours[i])) for i in idx]
            tot = sum(w * dts[i] for w, i in zip(weights, idx))
            for i, w in zip(idx, weights):
                out[hours[i]] = (budget * w / tot * 1000.0) if tot > 0 else 0.0
        # huidig en volgend klokuur aanscherpen met de uur-forecast van de bron
        # zelf (kWh per klokuur, geldt voor elke slot in dat uur) — hierop rust
        # de eerstvolgende beslissing en daar timet de bel het slechtst.
        # Forecast, dus mét bias.
        if hours:
            hour0 = hours[0].replace(minute=0, second=0, microsecond=0)
            for ent, hour in ((self.ent_hour_next, hour0 + timedelta(hours=1)),
                              (self.ent_hour_now, hour0)):
                if not ent:
                    continue
                kwh = self.t.energy_kwh(ent)
                if kwh is None:
                    continue
                for dt in hours:
                    if dt.replace(minute=0, second=0, microsecond=0) == hour:
                        out[dt] = kwh * 1000.0 * self.bias
        # het huidige uur weten we nóg beter dan elke forecast: de échte
        # meting, dus zonder bias
        pv_now = self.t.power_w(self.ent_pv_now)
        if hours and pv_now is not None:
            out[hours[0]] = pv_now
        return out
