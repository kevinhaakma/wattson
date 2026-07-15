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

    check("ontladen op minimum-SoC is niet effectief",
          not P.action_is_effective(step(), -1000.0, 0.0, p))
    check("ontladen boven minimum-SoC is wel effectief",
          P.action_is_effective(step(), -1000.0, 5.0, p))

    end = P.plan_end_soc(s, [-1000.0, 0.0, -1000.0], 5.0, p)
    check("plan_end_soc simuleert de horizon", abs(end - 3.0) < 1e-9)
    # met standby-drain eindigt hetzelfde plan iets lager (3 uur × 6 W)
    p_sb = params()
    p_sb.standby_w = 6.0
    end_sb = P.plan_end_soc(s, [-1000.0, 0.0, -1000.0], 5.0, p_sb)
    check("plan_end_soc telt standby-drain mee", abs(end_sb - (3.0 - 0.018)) < 1e-9)

    check("assist-start ligt boven stopdrempel en track-deadband",
          C.ASSIST_STOP_W < C.ASSIST_IMPORT_W
          and C.TRACK_DEADBAND_W < C.ASSIST_IMPORT_W)

    # --- doelfunctie: alpha/beta ---
    def pstep(imp, load_w=0.0, sell=True, exp=None):
        return P.Step(imp, imp if exp is None else exp, load_w, 0.0, sell_ok=sell)

    small = P.Params(capacity_kwh=2.0, soc_min_kwh=0.0, soc_max_kwh=2.0,
                     p_charge_max_w=1000.0, p_discharge_max_w=1000.0,
                     eta_nom=1.0, p_fix_w=0.0, deg_cost=0.0,
                     charge_levels=(0.0, 1000.0), discharge_levels=(0.0, 1000.0))

    # beta = 0: exporteren op het dure uur loont; beta hoog: bewaren wint
    sp, _ = P.plan([pstep(0.10), pstep(0.40)], 2.0, small)
    check("zonder beta exporteert de DP op het dure uur", sp[1] < -900.0)
    small.beta = 0.50
    sp, _ = P.plan([pstep(0.10), pstep(0.40)], 2.0, small)
    check("hoge beta houdt eigen energie binnen", sp[1] == 0.0)
    small.beta = 0.0

    # alpha/beta samen: eigen vraag dekken (0.30) verslaat exporteren (0.35)
    steps_pref = [pstep(0.30, load_w=500.0, sell=False), pstep(0.35)]
    sp, _ = P.plan(steps_pref, 0.5, small)
    check("zonder voorkeur wint de duurdere export",
          sp[0] == 0.0 and sp[1] < -400.0)
    small.alpha = 0.04
    small.beta = 0.04
    sp, _ = P.plan(steps_pref, 0.5, small)
    check("met voorkeur wint zelfvoorziening", sp[0] < -400.0 and sp[1] == 0.0)
    small.alpha = 0.0
    small.beta = 0.0

    # --- marginale-waardetabel ---
    # één duur uur, ontlaadcap 1 kWh: onder de cap is een extra kWh de volle
    # exportprijs waard, erboven niets (vermogensgrens maakt hem waardeloos)
    sp, _, lam = P.plan_with_values([pstep(0.40)], 0.5, small)
    check("lambda ziet de komende piek", abs(lam.value(0, 0.5) - 0.40) < 0.02)
    check("lambda kent de vermogensgrens", lam.value(0, 1.5) < 0.02)
    check("lambda is niet-stijgend in SoC",
          lam.value(0, 0.1) >= lam.value(0, 1.9) - 1e-9)
    check("lambda klemt op horizon en grid",
          lam.value(99, 5.0) == lam.value(0, 5.0))

    # --- beslisdrempels ---
    pf = P.Params(eta_nom=0.955, p_fix_w=0.0, deg_cost=0.03)
    floor = P.discharge_price_floor(0.20, pf)
    ceil = P.charge_price_ceiling(0.20, pf)
    check("ontlaadvloer boven lambda (verliezen+slijtage)",
          abs(floor - (0.20 + 0.03) / 0.955) < 1e-9)
    check("laadplafond onder lambda", abs(ceil - (0.20 - 0.03) * 0.955) < 1e-9)
    check("drempels laten winstruimte tussen laden en ontladen", ceil < floor)

    print("\n16/16 PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        sys.exit(1)
