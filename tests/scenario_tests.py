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
    sc = S.PriceScenario(wedge_saldering=0.02, wedge_post=0.10, sell_threshold=0.45)
    voor = date(2026, 7, 15)
    na = date(2027, 1, 1)

    check("wedge onder saldering", sc.wedge(voor) == 0.02)
    check("wedge na saldering-einde", sc.wedge(na) == 0.10)
    check("exportprijs wisselt op de einddatum",
          abs(sc.export_price(0.30, voor) - 0.28) < 1e-9
          and abs(sc.export_price(0.30, na) - 0.20) < 1e-9)
    check("exportprijs blijft boven de vloer", sc.export_price(-0.60, na) == -0.5)

    check("sell_ok vergt switch aan", not sc.sell_ok(0.50, voor, sell_enabled=False))
    check("sell_ok boven drempel", sc.sell_ok(0.50, voor, sell_enabled=True))
    # dezelfde importprijs haalt de drempel ná saldering niet meer: de kale
    # verkoopprijs is dan 0,10 lager
    check("sell_ok weegt de actuele wedge mee",
          sc.sell_ok(0.48, voor, sell_enabled=True)
          and not sc.sell_ok(0.48, na, sell_enabled=True))

    check("geen waarschuwing ver voor de overgang",
          sc.transition_warning(date(2026, 7, 15)) is None)
    check("waarschuwing in de aanloop",
          sc.transition_warning(date(2026, 12, 1)) is not None)
    check("waarschuwing vlak na de overgang",
          sc.transition_warning(date(2027, 1, 15)) is not None)
    check("waarschuwing dooft daarna",
          sc.transition_warning(date(2027, 6, 1)) is None)

    print("\n11/11 PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        sys.exit(1)
