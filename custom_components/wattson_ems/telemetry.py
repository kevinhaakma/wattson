"""Entity-IO: alle lees-toegang tot Home Assistant-states op één plek.

De rest van het systeem redeneert in getallen (W, kWh, €/kWh); alleen deze
laag weet dat die getallen uit HA-states met eenheden en leeftijden komen.
Schrijven gebeurt via adapters.py (set_number e.d.) — bewust gescheiden.
"""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import adapters as A
from .const import WATCH_FRESH_S


class Telemetry:
    """Leest en normaliseert meetwaarden (vermogen, energie, prijs)."""

    def __init__(self, hass: HomeAssistant, ent_price: str) -> None:
        self.hass = hass
        self.ent_price = ent_price

    # ---------- kale waarden ----------
    def f(self, entity: str) -> float | None:
        return A.read_f(self.hass, entity)

    def fresh(self, entity: str, max_age_s: float = WATCH_FRESH_S) -> float | None:
        """Als f, maar alleen als de waarde recent is bijgewerkt (vers bewijs)."""
        return A.read_fresh(self.hass, entity, max_age_s, dt_util.utcnow())

    def unit(self, entity: str) -> str:
        return A.unit_of(self.hass, entity)

    def power_w(self, entity: str) -> float | None:
        return A.read_power_w(self.hass, entity)

    def fresh_power_w(self, entity: str, max_age_s: float = WATCH_FRESH_S) -> float | None:
        return A.read_fresh_power_w(self.hass, entity, max_age_s, dt_util.utcnow())

    def energy_kwh(self, entity: str) -> float | None:
        """Lees een energie-forecast als kWh; accepteert Wh, kWh en MWh."""
        value = self.f(entity)
        if value is None:
            return None
        unit = self.unit(entity)
        if unit == "wh":
            return value / 1000.0
        if unit == "mwh":
            return value * 1000.0
        return value

    # ---------- prijzen ----------
    @staticmethod
    def price_eur_kwh(value: float, unit: str) -> float:
        """Normaliseer gangbare prijs-eenheden naar EUR/kWh."""
        unit = unit.replace(" ", "").lower()
        if "/mwh" in unit:
            return value / 1000.0
        if unit.startswith(("ct/", "c/")) or "cent/kwh" in unit:
            return value / 100.0
        return value

    def current_price(self) -> float | None:
        value = self.f(self.ent_price)
        return None if value is None else self.price_eur_kwh(value, self.unit(self.ent_price))

    def price_forecast(self) -> list[tuple[datetime, float]]:
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
                    price = self.price_eur_kwh(float(raw), self.unit(self.ent_price))
            except (KeyError, ValueError, TypeError):
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= now:
                out.append((dt, price))
        out.sort(key=lambda x: x[0])
        cur = self.current_price()
        if not out:
            if cur is not None:
                out = [(now, cur)]
        elif out[0][0] > now and cur is not None:
            # forecast begint pas volgend uur: huidig uur expliciet toevoegen
            out.insert(0, (now, cur))
        return out
