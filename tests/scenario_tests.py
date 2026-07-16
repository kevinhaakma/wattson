"""Regressietests voor de saldering-overgang (scenario.py)."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIO_PATH = HERE.parent / "custom_components" / "wattson_ems" / "scenario.py"

spec = importlib.util.spec_from_file_location("wattson_scenario", SCENARIO_PATH)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def main():
    sc = S.PriceScenario(wedge_saldering=0.00, wedge_post=0.10)
    voor = date(2026, 7, 15)
    na = date(2027, 1, 1)

    check("wedge onder saldering (gemeten: 0)", sc.wedge(voor) == 0.00)
    check("wedge na saldering-einde", sc.wedge(na) == 0.10)
    check("exportprijs wisselt op de einddatum",
          abs(sc.export_price(0.30, voor) - 0.30) < 1e-9
          and abs(sc.export_price(0.30, na) - 0.20) < 1e-9)
    check("exportprijs blijft boven de vloer", sc.export_price(-0.60, na) == -0.5)

    # jaarsaldering-bewaking: wedge schuift mee met de resterende ruimte
    sc.netting_headroom_kwh = 500.0
    check("ruime jaarpositie: volle saldering-wedge", sc.wedge(voor) == 0.00)
    sc.netting_headroom_kwh = 150.0
    check("halve blend-marge: wedge halverwege",
          abs(sc.wedge(voor) - 0.05) < 1e-9)
    sc.netting_headroom_kwh = 0.0
    check("ruimte op: post-saldering-wedge", sc.wedge(voor) == 0.10)
    sc.netting_headroom_kwh = -50.0
    check("netto-exporteur: wedge klemt op post", sc.wedge(voor) == 0.10)
    sc.netting_headroom_kwh = 0.0
    check("na saldering-einde telt de positie niet meer", sc.wedge(na) == 0.10)
    sc.netting_headroom_kwh = None

    check("geen waarschuwing ver voor de overgang",
          sc.transition_warning(date(2026, 7, 15)) is None)
    check("waarschuwing in de aanloop",
          sc.transition_warning(date(2026, 12, 1)) is not None)
    check("waarschuwing vlak na de overgang",
          sc.transition_warning(date(2027, 1, 15)) is not None)
    check("waarschuwing dooft daarna",
          sc.transition_warning(date(2027, 6, 1)) is None)

    print("\n13/13 PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        sys.exit(1)
