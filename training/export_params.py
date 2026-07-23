"""Exporteert getrainde parameters naar de custom integration (params.json).

Bevat: gekozen gedragsknoppen (huidig scenario), verbruiksprofiel per
(uur, weekend) uit de laatste 28 dagen, en de PV-forecast-bias.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "wattson_ems"))
import backtest as B  # noqa: E402

SCENARIO = "saldering_2026"  # huidig prijsregime


def main():
    rows = B.load_rows()
    results = json.load(open(os.path.join(HERE, "data", "results.json"), encoding="utf-8"))
    best = results["scenarios"][SCENARIO]["best"]
    wedge = results["scenarios"][SCENARIO]["wedge"]

    lf = B.LoadForecaster(rows, best["load_pct"])
    prof = lf.profile_at(len(rows) - 1)
    profile = {f"{h}|{int(we)}": round(v, 3) for (h, we), v in prof.items()}
    # PV-bias NIET uit de trainer-kolom pvfc (power_production_now) halen: de
    # live integratie gebruikt energy_production_today_remaining/_tomorrow —
    # een andere, beter gekalibreerde bron. Validatie 2026-07-15 op 9 dagen
    # live data: MAPE 18%, netto +5,5% onderschatting -> bias 1.05. De
    # trainer-bron gaf 1.4 en zou de live prognose fors overdrijven.
    bias = 1.05

    risk = {}
    risk_path = os.path.join(HERE, "data", "risk.json")
    if os.path.exists(risk_path):
        risk = json.load(open(risk_path, encoding="utf-8"))

    params = {
        "trained_at": results["days"][-1],
        "scenario": SCENARIO,
        "wedge": wedge,
        "deg_cost": best["deg"],
        "load_pct": best["load_pct"],
        # getrainde onzekerheids-curve (fit_risk.py): per-uur-increment van de
        # cumulatieve prognosefout; risk_k komt live uit de agressiviteit-select
        "risk_shape_steps": risk.get("shape_steps", []),
        "risk_k": best.get("risk", 0.0),
        # de agressiviteit-select overschrijft alpha/beta/deg live met de
        # AGGRO_LEVELS-mapping (const.py); dit zijn de kas-optimale defaults
        "alpha": best.get("pref", 0.0),
        "beta": best.get("pref", 0.0),
        "pref_curve": results["scenarios"][SCENARIO].get("pref_curve", []),
        "pv_bias": round(bias, 3),
        "load_profile": profile,
        "battery": {
            "capacity_kwh": 5.76, "soc_min_kwh": 0.58, "soc_max_kwh": 5.76,
            # apparaat-realiteit: opties laden 2000 W, inverse_max_power 1400 W
            "p_charge_max_w": 2000.0, "p_discharge_max_w": 1400.0,
            # gekalibreerd op massabalans 2026-07-07..15 (zie planner.Params)
            "eta_nom": 0.955, "p_fix_w": 0.0, "standby_w": 6.0,
        },
    }
    # canoniek doel: de actieve Wattson-integratie (hus_battery_ems is bevroren legacy)
    out = os.path.join(HERE, "..", "custom_components", "wattson_ems", "params.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(params, open(out, "w", encoding="utf-8"), indent=1)
    print(f"params.json -> wattson_ems: deg={best['deg']} lp={best['load_pct']} bias={bias:.2f} "
          f"profiel {len(profile)} slots")


if __name__ == "__main__":
    main()
