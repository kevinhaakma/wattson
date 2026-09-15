"""Entity-IO: alle lees-toegang tot Home Assistant-states op één plek.

De rest van het systeem redeneert in getallen (W, kWh, €/kWh); alleen deze
laag weet dat die getallen uit HA-states met eenheden en leeftijden komen.
Schrijven gebeurt via adapters.py (set_number e.d.) — bewust gescheiden.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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

    def live_power_w(self, entity: str, max_age_s: float = WATCH_FRESH_S) -> float | None:
        """Vermogen als de bron LEEFT: last_reported (elke schrijfactie) in
        plaats van last_updated (alleen bij waardeverandering). Voor
        write-on-change-telemetrie zoals de Zendure-sensoren is een lang
        constante waarde (bv. 0 W) via fresh_power_w onterecht 'oud'."""
        if not entity:
            return None
        st = self.hass.states.get(entity)
        if st is None or st.state in ("unknown", "unavailable"):
            return None
        reported = getattr(st, "last_reported", None) or st.last_updated
        if (dt_util.utcnow() - reported).total_seconds() > max_age_s:
            return None
        return A.read_power_w(self.hass, entity)

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
        """Prijs-forecast per slot (uur of kwartier) uit het forecast-attribuut.

        Ondersteunde contracten per forecast-item:
        - Zonneplan uur:      {"datetime": iso, "electricity_price": prijs x 1e7}
        - Zonneplan kwartier: {"start_date": iso, "price_tax_included": {"amount": prijs x 1e7}}
        - generiek:           {"datetime"|"start"|"from": iso, "price"|"value": €/kWh}
        De slotlengte volgt uit de afstand tussen de items. De lopende slot telt
        mee; ontbreekt die, dan vult de actuele sensorwaarde hem aan — anders
        zou het setpoint van de volgende slot nu al uitgevoerd worden.
        """
        st = self.hass.states.get(self.ent_price)
        if st is None:
            return []
        items = []
        for item in st.attributes.get("forecast", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                t = (item.get("datetime") or item.get("start_date")
                     or item.get("start") or item.get("from"))
                dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                incl = item.get("price_tax_included")
                if isinstance(incl, dict) and incl.get("amount") is not None:
                    price = float(incl["amount"]) / 1e7  # zonneplan-schaal
                elif item.get("electricity_price") is not None:
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
            items.append((dt, price))
        items.sort(key=lambda x: x[0])
        diffs = [b[0] - a[0] for a, b in zip(items, items[1:]) if b[0] > a[0]]
        slot = min(diffs) if diffs else timedelta(hours=1)
        now = dt_util.utcnow()
        out = [(dt, price) for dt, price in items if dt + slot > now]
        cur = self.current_price()
        s = slot.total_seconds()
        start = datetime.fromtimestamp(now.timestamp() // s * s, tz=timezone.utc)
        if not out:
            if cur is not None:
                out = [(start, cur)]
        elif out[0][0] > now and cur is not None:
            # forecast begint pas bij de volgende slot: lopende slot toevoegen
            out.insert(0, (start, cur))
        return out
