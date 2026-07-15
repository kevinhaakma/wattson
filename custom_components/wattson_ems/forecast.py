"""Voorspellingen: huislast-profiel en PV-curve.

LoadProfile: getraind (uur, weekend)-profiel uit params.json.
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
    """Verwachte huislast (W) per lokaal uur, uit het getrainde profiel."""

    DEFAULT_KWH = 0.35

    def __init__(self, profile: dict[tuple[int, int], float]) -> None:
        self.profile = profile

    def expected_w(self, dt: datetime) -> float:
        loc = dt_util.as_local(dt)
        weekend = loc.weekday() >= 5
        return self.profile.get((loc.hour, int(weekend)), self.DEFAULT_KWH) * 1000.0


class PvCurve:
    """PV-prognose (W per uur) uit de dagtotalen van de forecast-sensor."""

    def __init__(self, telemetry: Telemetry, ent_pv_now: str,
                 ent_pv_remain: str, ent_pv_tomorrow: str, bias: float) -> None:
        self.t = telemetry
        self.ent_pv_now = ent_pv_now
        self.ent_pv_remain = ent_pv_remain
        self.ent_pv_tomorrow = ent_pv_tomorrow
        self.bias = bias

    def remain_kwh(self) -> float:
        return (self.t.energy_kwh(self.ent_pv_remain) or 0.0) * self.bias

    def tomorrow_kwh(self) -> float:
        return (self.t.energy_kwh(self.ent_pv_tomorrow) or 0.0) * self.bias

    def curve(self, hours: list[datetime]) -> dict[datetime, float]:
        """Verdeel de PV-forecast dagtotalen over een daglicht-bel (W per uur)."""
        remain = self.remain_kwh()
        tomorrow = self.tomorrow_kwh()
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
        pv_now = self.t.power_w(self.ent_pv_now)
        if hours and pv_now is not None:
            out[hours[0]] = pv_now
        return out
