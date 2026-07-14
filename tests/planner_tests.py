"""Regressietests voor reserve, zonbudget en onuitvoerbare planacties."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLANNER_PATH = HERE.parent / "custom_components" / "wattson_ems" / "planner.py"
CONST_PATH = HERE.parent / "custom_components" / "wattson_ems" / "const.py"

spec = importlib.util.spec_from_file_location("wattson_planner", PLANNER_PATH)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

const_spec = importlib.util.spec_from_file_location("wattson_const", CONST_PATH)
C = importlib.util.module_from_spec(const_spec)
const_spec.loader.exec_module(C)


def step(load_w=2000.0, pv_w=0.0):
    return P.Step(0.20, 0.18, load_w, pv_w)


def params():
    return P.Params(
        capacity_kwh=10.0,
        soc_min_kwh=0.0,
        soc_max_kwh=10.0,
        p_charge_max_w=2000.0,
        p_discharge_max_w=2000.0,
        eta_nom=1.0,
        p_fix_w=0.0,
    )


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def main():
    p = params()
    s = [step(), step(), step()]

    reserve = P.future_reserve_kwh(s, [-1000.0, 1000.0, -1000.0], 5.0, p)
    check("reserve telt hergebruikte kWh niet dubbel", abs(reserve - 1.0) < 1e-9)

    reserve = P.future_reserve_kwh(s, [-1000.0, -1000.0, -1000.0], 5.0, p)
    check("reserve bewaart cumulatief tekort", abs(reserve - 3.0) < 1e-9)

    sun = [step(load_w=500.0, pv_w=2000.0) for _ in range(4)]
    budget = P.solar_backed_budget_kwh(
        sun, 6.0, p, confidence=0.75, buffer_kwh=0.75, soc_margin_kwh=0.15)
    # 4 × (1.5 - 0.5) = 4 kWh conservatief surplus; 4 kWh vrije
    # accuruimte + 0,75 kWh buffer laat terecht nog niets vrij.
    check("zonbudget houdt vrije ruimte en buffer apart", budget == 0.0)

    budget = P.solar_backed_budget_kwh(
        sun, 8.0, p, confidence=0.75, buffer_kwh=0.75, soc_margin_kwh=0.15)
    check("zonbudget geeft alleen verwacht overlopende energie vrij",
          abs(budget - 1.25) < 1e-9)

    check("ontladen op minimum-SoC is niet effectief",
          not P.action_is_effective(step(), -1000.0, 0.0, p))
    check("ontladen boven minimum-SoC is wel effectief",
          P.action_is_effective(step(), -1000.0, 5.0, p))

    check("zon-assist start binnen uitvoerbare 50 W stap",
          C.SOLAR_ASSIST_IMPORT_W <= 50)
    check("zon-assist stop ligt onder start en track-deadband",
          C.ASSIST_STOP_W < C.SOLAR_ASSIST_IMPORT_W
          and C.ASSIST_STOP_W <= C.TRACK_DEADBAND_W)

    print("\n8/8 PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        sys.exit(1)
