"""Prijsscenario: wat is teruggeleverde stroom waard, en wanneer verandert dat.

Onder saldering (t/m 2026) is een geëxporteerde kWh evenveel waard als een
geïmporteerde: GEMETEN wedge ≈ €0,00 (Zonneplan keert spot + inkoopvergoeding
+ Zonnebonus uit; belasting wordt jaarlijks gesaldeerd — zie docs/economie.md).
Per 2027-01-01 stopt saldering en zakt de terugleverwaarde met ~€0,10/kWh.
Deze laag levert alleen de prijscurves; óf er verkocht wordt beslist de DP
zelf op basis van de doelfunctie — er is geen vaste verkoopdrempel meer.
"""
from __future__ import annotations

from datetime import date

SALDERING_EINDE = date(2027, 1, 1)
WAARSCHUW_DAGEN = 60
# glijdende overgang: onder deze resterende jaarruimte (import − export, kWh)
# schuift de wedge lineair van saldering naar post-saldering — export boven de
# jaarsom import krijgt immers maar de kale prijs, niet het volle tarief
NETTING_BLEND_KWH = 300.0


class PriceScenario:
    """Datum-bewuste exportprijs-logica (saldering -> post-saldering).

    Houdt ook de jaarsaldering-positie bij (via netting.NettingMonitor,
    gezet door de coordinator): raakt de netto-importruimte op, dan is de
    marginale geëxporteerde kWh feitelijk al een post-salderings-kWh."""

    def __init__(self, wedge_saldering: float, wedge_post: float) -> None:
        self.wedge_saldering = wedge_saldering
        self.wedge_post = wedge_post
        self.netting_headroom_kwh: float | None = None

    def wedge(self, today: date) -> float:
        if today >= SALDERING_EINDE:
            return self.wedge_post
        h = self.netting_headroom_kwh
        if h is None:
            return self.wedge_saldering
        factor = max(0.0, min(h / NETTING_BLEND_KWH, 1.0))
        return self.wedge_post + (self.wedge_saldering - self.wedge_post) * factor

    def label(self, today: date) -> str:
        return "saldering" if today < SALDERING_EINDE else "geen saldering"

    def export_price(self, import_price: float, today: date) -> float:
        return max(import_price - self.wedge(today), -0.5)

    def transition_warning(self, today: date) -> str | None:
        """Waarschuwing in de aanloop naar (en vlak na) het saldering-einde."""
        dagen = (SALDERING_EINDE - today).days
        if 0 < dagen <= WAARSCHUW_DAGEN:
            return (f"saldering stopt over {dagen} dagen — controleer de "
                    f"post-saldering-wedge (nu €{self.wedge_post:.2f}/kWh); "
                    "zelfvoorziening wordt daarna ook financieel dominant")
        if -WAARSCHUW_DAGEN <= dagen <= 0:
            return (f"saldering is gestopt — planner rekent nu met wedge "
                    f"€{self.wedge_post:.2f}/kWh; hertrainen aanbevolen")
        return None
