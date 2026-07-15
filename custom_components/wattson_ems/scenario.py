"""Prijsscenario: wat is teruggeleverde stroom waard, en wanneer verandert dat.

Onder saldering (t/m 2026) is een geëxporteerde kWh vrijwel evenveel waard
als een geïmporteerde (wedge ~0,02). Per 2027-01-01 stopt saldering en zakt
de terugleverwaarde fors. Tot deze laag bestond stond de wedge hardcoded en
zou het systeem per 1-1-2027 stilzwijgend met een veel te optimistische
exportprijs blijven rekenen (finding 2026-07-15); nu wisselt hij op datum
en waarschuwt hij van tevoren.
"""
from __future__ import annotations

from datetime import date

SALDERING_EINDE = date(2027, 1, 1)
WAARSCHUW_DAGEN = 60


class PriceScenario:
    """Datum-bewuste exportprijs-logica (saldering -> post-saldering)."""

    def __init__(self, wedge_saldering: float, wedge_post: float,
                 sell_threshold: float) -> None:
        self.wedge_saldering = wedge_saldering
        self.wedge_post = wedge_post
        self.sell_threshold = sell_threshold

    def wedge(self, today: date) -> float:
        return self.wedge_saldering if today < SALDERING_EINDE else self.wedge_post

    def label(self, today: date) -> str:
        return "saldering" if today < SALDERING_EINDE else "geen saldering"

    def export_price(self, import_price: float, today: date) -> float:
        return max(import_price - self.wedge(today), -0.5)

    def sell_ok(self, import_price: float, today: date, sell_enabled: bool) -> bool:
        """Mag dit uur boven de huisvraag uit verkocht worden?"""
        return sell_enabled and (import_price - self.wedge(today)) >= self.sell_threshold

    def transition_warning(self, today: date) -> str | None:
        """Waarschuwing in de aanloop naar (en vlak na) het saldering-einde."""
        dagen = (SALDERING_EINDE - today).days
        if 0 < dagen <= WAARSCHUW_DAGEN:
            return (f"saldering stopt over {dagen} dagen — controleer de "
                    f"post-saldering-wedge (nu €{self.wedge_post:.2f}/kWh) en "
                    "overweeg agressiviteit 'gebalanceerd'/'agressief'")
        if -WAARSCHUW_DAGEN <= dagen <= 0:
            return (f"saldering is gestopt — planner rekent nu met wedge "
                    f"€{self.wedge_post:.2f}/kWh; hertrainen aanbevolen")
        return None
