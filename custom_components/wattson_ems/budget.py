"""Energie-budgetten uit het actuele plan.

Eén plek waar per plan-tick wordt uitgerekend wat de realtime-laag mag
gebruiken en wat gereserveerd blijft:

- reserve_kwh       wat het plan later nog nodig heeft (grootste cumulatieve
                    tekort — toekomstig laden vult een latere ontlading aan)
- solar_backed_kwh  conservatief PV-overschot dat anders tóch niet in de accu
                    past en dus actuele netimport mag verdringen
- stranded_kwh      voorzien RESTANT: lading waarmee het plan de horizon
                    verlaat en die binnen de horizon geen betere bestemming
                    heeft — mag bij elke prijs boven de restwaarde worden
                    ingezet (finding 2026-07-15: accu eindigde elke ochtend
                    met 34-76% restant terwijl hij vóór de middag alweer vol
                    zat; dat restant verdrong gratis PV naar export)
- expected_load_kwh verwachte huisvraag over de horizon (transparantie)
- dis_floor         goedkoopste geplande ontlaaduurprijs (frontrun-referentie)
"""
from __future__ import annotations

from homeassistant.util import dt as dt_util

from . import planner as P
from .const import (
    ASSIST_SOC_MARGE_KWH,
    SOLAR_BUFFER_KWH,
    SOLAR_FORECAST_CONFIDENCE,
)


class PlanBudgets:
    """Wordt elke plan-tick opnieuw gevuld; realtime-lagen lezen alleen."""

    def __init__(self, params) -> None:
        self.params = params
        self.reserve_kwh = 0.0
        self.solar_backed_kwh = 0.0
        self.stranded_kwh = 0.0
        self.end_soc_kwh = 0.0
        self.expected_load_kwh = 0.0
        self.dis_floor: float | None = None
        self.cheap_future = 0.0
        self.max_future = 0.0
        self.terminal_value = 0.0

    def compute(self, prices, steps, setpoints, soc_kwh, terminal_value) -> None:
        p = self.params
        prices_only = [s.price_imp for s in steps]
        self.cheap_future = min(prices_only)
        self.max_future = max(prices_only)
        self.terminal_value = terminal_value

        # Reserveer alleen het grootste cumulatieve tekort van het toekomstige
        # plan. Toekomstig laden mag een latere ontlading dus aanvullen; dezelfde
        # kWh wordt niet langer voor ieder ontlaaduur opnieuw gereserveerd.
        _, soc_after_now, _, _ = P.hour_result(steps[0], setpoints[0], soc_kwh, p)
        self.reserve_kwh = min(
            P.future_reserve_kwh(steps[1:], setpoints[1:], soc_after_now, p),
            max(soc_kwh - p.soc_min_kwh, 0.0),
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
            solar_steps, soc_kwh, p,
            confidence=SOLAR_FORECAST_CONFIDENCE,
            buffer_kwh=SOLAR_BUFFER_KWH,
            soc_margin_kwh=ASSIST_SOC_MARGE_KWH,
        )

        # Voorzien restant: waarmee verlaat het plan de horizon? Dat is per
        # definitie energie waarvoor de DP géén beter geprijsde bestemming
        # vond (het huis slaapt 's nachts en exporteren onder de verkoop-
        # drempel mag niet) — de assist mag dit deel dus opmaken zodra de
        # actuele prijs boven de restwaarde uitkomt.
        self.end_soc_kwh = P.plan_end_soc(steps, setpoints, soc_kwh, p)
        self.stranded_kwh = min(
            max(self.end_soc_kwh - p.soc_min_kwh - ASSIST_SOC_MARGE_KWH, 0.0),
            max(soc_kwh - p.soc_min_kwh - ASSIST_SOC_MARGE_KWH, 0.0),
        )
        self.expected_load_kwh = sum(s.load_w for s in steps) / 1000.0

        # goedkoopste uur waarin het plan nog wil ontladen: een piek NU met een
        # prijs daarboven mag de assist "voordringend" bedienen (frontrun) —
        # dezelfde energie, alleen eerder en waardevoller; de replan verdeelt
        # de rest daarna opnieuw
        dis_prices = [s2.price_imp for s2, sp2 in zip(steps[1:], setpoints[1:]) if sp2 < 0]
        self.dis_floor = min(dis_prices) if dis_prices else None

    # ---------- realtime-vragen ----------
    def stranded_price_floor(self) -> float:
        """Minimale prijs waarbij het inzetten van restant strikt loont:
        de restwaarde + slijtage, gecorrigeerd voor round-trip-verlies."""
        p = self.params
        eta_rt = max(P.eta_oneway(800.0, p) ** 2, 0.5)
        return (self.terminal_value + p.deg_cost) / eta_rt

    def stranded_allowed(self, price: float, min_kwh: float) -> bool:
        return self.stranded_kwh > min_kwh and price >= self.stranded_price_floor()

    def as_inputs(self) -> dict:
        """Attributen voor sensor.wattson_advies (berekend_met)."""
        return {
            "planreserve_kwh": round(self.reserve_kwh, 2),
            "zon_gedekt_beschikbaar_kwh": round(self.solar_backed_kwh, 2),
            "zon_prognose_zekerheid_pct": round(SOLAR_FORECAST_CONFIDENCE * 100),
            "verwachte_vraag_horizon_kwh": round(self.expected_load_kwh, 1),
            "verwacht_restant_einde_kwh": round(
                max(self.end_soc_kwh - self.params.soc_min_kwh, 0.0), 2),
            "restant_inzetbaar_kwh": round(self.stranded_kwh, 2),
        }
