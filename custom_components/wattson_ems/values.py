"""Marginale-waardelaag: wat is een kWh in de accu nú waard.

Vervangt de vroegere budget-heuristieken (planreserve, zon-gedekt budget,
gestrand restant, frontrun-vloer). Al die special cases benaderden hetzelfde
getal: de marginale waarde λ(t, SoC) die de DP toch al uitrekent. Deze laag
bewaart die tabel per plan-tick en beantwoordt de realtime-vragen ermee:

- ontlaadvloer(soc)  minimale opbrengst waarbij nú ontladen beter is dan
                     bewaren — stijgt vanzelf als de lading zakt (impliciete
                     reserve) en daalt naar de restwaarde als het plan met
                     surplus eindigt (impliciet gestrand-restant)
- laadplafond(soc)   maximale kostprijs waarbij nú opslaan loont — hoog
                     zolang er dure uren of eigen vraag vóór ons liggen,
                     nul zodra de accu de horizon toch al vol doorkomt
                     (impliciet zon-gedekt budget)

Alle waarden zijn doel-euro's: de zelfvoorzienings-voorkeur (alpha/beta) zit
in de DP-prijzen en dus ook in λ. Realtime vergelijkt met dezelfde
voorkeursprijzen, zodat plan en realtime exact dezelfde afweging maken.
"""
from __future__ import annotations

from . import planner as P


class PlanValues:
    """Wordt elke plan-tick opnieuw gevuld; realtime-lagen lezen alleen."""

    def __init__(self, params) -> None:
        self.params = params
        self.lam: P.LambdaTable | None = None
        self.terminal_value = 0.0
        self.end_soc_kwh = 0.0
        self.expected_load_kwh = 0.0
        self.expected_import_kwh = 0.0
        self.zelfvoorziening_pct = 0.0

    def compute(self, steps, setpoints, soc_kwh, terminal_value,
                lam: P.LambdaTable) -> None:
        p = self.params
        self.lam = lam
        self.terminal_value = terminal_value
        self.end_soc_kwh = P.plan_end_soc(steps, setpoints, soc_kwh, p)
        self.expected_load_kwh = sum(s.load_w for s in steps) / 1000.0

        # verwachte zelfvoorziening over de horizon: welk deel van de eigen
        # vraag komt volgens dit plan NIET van het net (PV + accu samen)
        soc = min(max(soc_kwh, p.soc_min_kwh), p.soc_max_kwh)
        imp = 0.0
        for st, a in zip(steps, setpoints):
            _, soc, act, _ = P.hour_result(st, a, soc, p)
            imp += max(st.load_w - st.pv_w + act, 0.0) / 1000.0
        self.expected_import_kwh = imp
        load = max(self.expected_load_kwh, 1e-9)
        self.zelfvoorziening_pct = max(0.0, min(1.0, 1.0 - imp / load)) * 100.0

    # ---------- realtime-vragen ----------
    def lam_now(self, soc_kwh: float) -> float:
        """Marginale waarde van de accu-inhoud in het huidige planuur."""
        if self.lam is None:
            return self.terminal_value
        return self.lam.value(0, soc_kwh)

    def discharge_floor(self, soc_kwh: float) -> float:
        """Minimale opbrengst (€/kWh, doel-euro's) waarbij ontladen nú loont.

        Ontladen haalt energie uit het grid-vak ÓNDER de actuele SoC; λ van
        het vak op de SoC zelf meet de waarde van toevoegen. Aan de bovenrand
        lopen die sterk uiteen: bij een volle accu past er niets meer bij
        (λ-boven ≈ 0) terwijl de opgeslagen energie de piekwaarde draagt
        (gemeten 2026-07-15: λ 0,21 op vol vs 0,28-0,34 één vak lager —
        de assist sprong daardoor bij tijdens het koken terwijl bewaren
        voor de avondpiek strikt beter was)."""
        step = self.lam.soc_step_kwh if self.lam else 0.0
        return P.discharge_price_floor(self.lam_now(soc_kwh - step), self.params)

    def charge_ceiling(self, soc_kwh: float) -> float:
        """Maximale kostprijs (€/kWh, doel-euro's) waarbij laden nú loont."""
        return P.charge_price_ceiling(self.lam_now(soc_kwh), self.params)

    def as_inputs(self, soc_kwh: float) -> dict:
        """Attributen voor sensor.wattson_advies (berekend_met)."""
        return {
            "marginale_waarde_eur_kwh": round(self.lam_now(soc_kwh), 3),
            "ontlaadvloer_eur_kwh": round(self.discharge_floor(soc_kwh), 3),
            "laadplafond_eur_kwh": round(self.charge_ceiling(soc_kwh), 3),
            "verwachte_vraag_horizon_kwh": round(self.expected_load_kwh, 1),
            "verwacht_restant_einde_kwh": round(
                max(self.end_soc_kwh - self.params.soc_min_kwh, 0.0), 2),
            "zelfvoorziening_horizon_pct": round(self.zelfvoorziening_pct, 1),
        }
