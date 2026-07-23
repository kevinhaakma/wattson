"""Trainingsrun: parameter-search + walk-forward validatie -> data/results.json.

Boekhouding is gesplitst: netkosten (grid) en slijtage worden apart bijgehouden.
Alle strategieën worden beoordeeld op: netkosten + DEG_TRUE × doorzet, met één
uniforme 'echte' degradatieprijs. Het deg-gewicht waarmee de planner intern
plant is een gedragsknop (grid-search); de hindsight-benchmark plant met
DEG_TRUE en is daarmee het echte plafond onder deze boekhouding.

Walk-forward: beste combo op 28 traindagen, beoordeeld op de 7 dagen erna —
post-hoc mogelijk omdat elke combo causaal over de hele tijdlijn is doorgerekend.
"""
import json
import os
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "wattson_ems"))
import planner as P  # noqa: E402
import backtest as B  # noqa: E402

# €/kWh doorzet: echte slijtage. Gebruik zit op ~0,85 cyclus/dag = precies op
# de 3000-cycli/80%-grens; marginale kost tussen kalender-gelimiteerd (~0) en
# cyclus-afschrijving (€1.980 / 3000c / 5,76 kWh ≈ 0,11). 0,03 is de best
# verdedigbare middenwaarde — zie docs/economie.md in de wattson-repo.
DEG_TRUE = 0.03
SCENARIOS = {
    # GEMETEN (8 dgn Zonneplan-sensordata): export ≈ vol uurtarief (spot +
    # €0,02 inkoopvergoeding + 10% Zonnebonus; belasting jaarlijks gesaldeerd)
    "saldering_2026": {"wedge": 0.00},
    # 2027+: belastingteruggave vervalt (~€0,111) minus Zonneplan-bonus
    "geen_saldering_2027": {"wedge": 0.10},
}
DEG = [0.02, 0.03]
LOAD_PCT = [0.5]  # won in alle eerdere folds; scheelt de helft van de runs
# zelfvoorzienings-voorkeur (alpha=beta, €/kWh): 0 = pure prijsarbitrage
PREF = [0.0, 0.02, 0.05]
# onzekerheids-discount (risk_k op de getrainde cumulatieve foutcurve):
# 0.05 geeft ~1,5% haircut op 5 uur vooruit ("bij twijfel het huis nú"),
# grote spreads overleven moeiteloos
RISK = [0.05]  # OOS-winnaar in beide scenario's; vastgezet
# extra export-korting bovenop pref (beta-asymmetrie): beprijst centen-trades
# (reserve verkopen om hem 4 u later terug te kopen) zonder huisdekking te raken
BETA_EXTRA = [0.0, 0.02, 0.04]


def daily(rows, costs, thrus, acts, deg_weight):
    """Per dag: netkosten (excl. plannings-deg), doorzet, import en huisvraag
    (voor zelfvoorzieningsgraad). costs zijn kas-euro's (alpha=beta=0)."""
    out = {}
    for r, c, th, a in zip(rows, costs, thrus, acts):
        d = out.setdefault(r["loc_date"], {"grid": 0.0, "thru": 0.0, "chg": 0.0,
                                           "dis": 0.0, "imp": 0.0, "exp": 0.0,
                                           "load": 0.0})
        d["grid"] += c - deg_weight * th
        d["thru"] += th
        if a > 0:
            d["chg"] += a / 1000.0
        else:
            d["dis"] -= a / 1000.0
        d["imp"] += max(r["house"] - r["pv"] + a / 1000.0, 0.0)
        d["exp"] += max(-(r["house"] - r["pv"] + a / 1000.0), 0.0)
        d["load"] += r["house"]
    for d in out.values():
        d["true"] = d["grid"] + DEG_TRUE * d["thru"]
    return out


def zelfvoorziening(res, ds):
    """Aandeel van de huisvraag dat NIET van het net kwam, over deze dagen."""
    imp = span(res, ds, "imp")
    load = span(res, ds, "load")
    return 1.0 - imp / max(load, 1e-9)


def job(arg):
    kind, scen, wedge, deg, load_pct, pref, risk, bx = arg
    rows = B.load_rows()
    params = B.mk_params(deg, pref, risk, bx)
    if kind == "planner":
        costs, socs, acts, thrus = B.run_planner(rows, params, wedge, load_pct=load_pct)
    else:
        costs, socs, acts, thrus = B.STRATEGIES[kind](rows, params, wedge)
    return {"kind": kind, "scen": scen, "deg": deg, "load_pct": load_pct, "pref": pref, "risk": risk, "bx": bx,
            "daily": daily(rows, costs, thrus, acts, deg),
            "socs": socs if kind in ("planner", "hindsight") else None,
            "acts": acts if kind in ("planner", "hindsight") else None}


def span(res, ds, key="true"):
    return sum(res["daily"][d][key] for d in ds if d in res["daily"])


def main():
    rows = B.load_rows()
    days = sorted({r["loc_date"] for r in rows})

    jobs = []
    for scen, sc in SCENARIOS.items():
        jobs.append(("none", scen, sc["wedge"], 0.0, 0.5, 0.0, 0.0, 0.0))
        jobs.append(("naive", scen, sc["wedge"], 0.0, 0.5, 0.0, 0.0, 0.0))
        jobs.append(("hindsight", scen, sc["wedge"], DEG_TRUE, 0.5, 0.0, 0.0, 0.0))
        for deg in DEG:
            for lp in LOAD_PCT:
                for pref in PREF:
                    for risk in RISK:
                        for bx in BETA_EXTRA:
                            jobs.append(("planner", scen, sc["wedge"], deg, lp, pref, risk, bx))
    print(f"{len(jobs)} runs over {len(days)} dagen...")
    with Pool(min(len(jobs), max(os.cpu_count() - 2, 4))) as pool:
        results = pool.map(job, jobs)

    out = {"days": days, "deg_true": DEG_TRUE,
           "tests": json.load(open(os.path.join(HERE, "data", "test_results.json"))),
           "scenarios": {}}
    for scen, sc in SCENARIOS.items():
        rs = [r for r in results if r["scen"] == scen]
        base = {r["kind"]: r for r in rs if r["kind"] != "planner"}
        planners = [r for r in rs if r["kind"] == "planner"]

        folds = []
        i = 28
        while i + 7 <= len(days):
            train, test = days[i - 28:i], days[i:i + 7]
            best = min(planners, key=lambda r: span(r, train))
            folds.append({
                "test_start": test[0],
                "chosen": {"deg": best["deg"], "load_pct": best["load_pct"],
                           "pref": best["pref"], "risk": best["risk"], "bx": best["bx"]},
                "test_cost": span(best, test),
                "test_none": span(base["none"], test),
                "test_hindsight": span(base["hindsight"], test),
            })
            i += 7

        best_overall = min(planners, key=lambda r: span(r, days))
        tot = {k: round(span(base[k], days), 2) for k in ("none", "naive", "hindsight")}
        tot_planner = round(span(best_overall, days), 2)
        grid_only = {
            "none": round(span(base["none"], days, "grid"), 2),
            "planner": round(span(best_overall, days, "grid"), 2),
            "hindsight": round(span(base["hindsight"], days, "grid"), 2),
        }
        wf_cost = sum(f["test_cost"] for f in folds)
        wf_none = sum(f["test_none"] for f in folds)
        wf_hind = sum(f["test_hindsight"] for f in folds)
        # de voorkeursknop: per pref-stand de kas-beste combo + zelfvoorziening
        pref_curve = []
        for pref in PREF:
            for bx in BETA_EXTRA:
                cand = min((r for r in planners
                            if r["pref"] == pref and r["bx"] == bx),
                           key=lambda r: span(r, days))
                pref_curve.append({
                    "pref": pref, "bx": bx, "risk": cand["risk"], "deg": cand["deg"],
                    "load_pct": cand["load_pct"],
                    "total": round(span(cand, days), 2),
                    "zelfvoorziening_pct": round(zelfvoorziening(cand, days) * 100.0, 1),
                    "export_kwh_dag": round(span(cand, days, "exp") / len(days), 2),
                })
        out["scenarios"][scen] = {
            "wedge": sc["wedge"],
            "totals": {**tot, "planner": tot_planner},
            "grid_only": grid_only,
            "zelfvoorziening_none_pct": round(zelfvoorziening(base["none"], days) * 100.0, 1),
            "pref_curve": pref_curve,
            "planner_grid": [{"deg": r["deg"], "load_pct": r["load_pct"], "pref": r["pref"],
                              "risk": r["risk"],
                              "total": round(span(r, days), 2)} for r in planners],
            "best": {"deg": best_overall["deg"], "load_pct": best_overall["load_pct"],
                     "pref": best_overall["pref"], "risk": best_overall["risk"],
                     "bx": best_overall["bx"], "total": tot_planner},
            "daily": {
                "none": {d: round(v["true"], 4) for d, v in base["none"]["daily"].items()},
                "naive": {d: round(v["true"], 4) for d, v in base["naive"]["daily"].items()},
                "hindsight": {d: round(v["true"], 4) for d, v in base["hindsight"]["daily"].items()},
                "planner": {d: round(v["true"], 4) for d, v in best_overall["daily"].items()},
                "cycled": {d: round(v["thru"], 2) for d, v in best_overall["daily"].items()},
            },
            "walkforward": {
                "folds": folds,
                "oos_saving": round(wf_none - wf_cost, 2),
                "oos_ceiling": round(wf_none - wf_hind, 2),
                "oos_capture": round((wf_none - wf_cost) / max(wf_none - wf_hind, 1e-9), 3),
                "test_days": len(folds) * 7,
            },
        }
        tail_days = set(days[-14:])
        idxs = [i for i, r in enumerate(rows) if r["loc_date"] in tail_days]
        out["scenarios"][scen]["trace"] = {
            "hours": [{"iso": rows[i]["iso"], "price": rows[i]["price"], "pv": rows[i]["pv"],
                       "house": rows[i]["house"], "ev": rows[i]["ev"],
                       "soc": round(best_overall["socs"][i], 2), "act": round(best_overall["acts"][i]),
                       "soc_h": round(base["hindsight"]["socs"][i], 2),
                       "act_h": round(base["hindsight"]["acts"][i])} for i in idxs],
        }

    json.dump(out, open(os.path.join(HERE, "data", "results.json"), "w"))

    for scen, s in out["scenarios"].items():
        t = s["totals"]
        print(f"\n=== {scen} (wedge €{s['wedge']}) — incl. slijtage @ €{DEG_TRUE}/kWh ===")
        print(f"none €{t['none']}  naive €{t['naive']}  planner €{t['planner']}  hindsight €{t['hindsight']}")
        sav, ceil = t["none"] - t["planner"], t["none"] - t["hindsight"]
        print(f"besparing €{sav:.2f} van max €{ceil:.2f} (capture {sav/max(ceil,1e-9)*100:.0f}%) "
              f"over {len(out['days'])} dagen -> €{sav/len(out['days'])*365:.0f}/jaar")
        g = s["grid_only"]
        print(f"alleen netkosten (excl. slijtage): besparing €{g['none']-g['planner']:.2f} "
              f"-> €{(g['none']-g['planner'])/len(out['days'])*365:.0f}/jaar")
        wf = s["walkforward"]
        print(f"walk-forward OOS: €{wf['oos_saving']} van €{wf['oos_ceiling']} "
              f"(capture {wf['oos_capture']*100:.0f}%), combo deg={s['best']['deg']} "
              f"lp={s['best']['load_pct']} pref={s['best']['pref']} risk={s['best']['risk']}")
        print(f"zelfvoorziening zonder accu: {s['zelfvoorziening_none_pct']}%")
        for pc in s["pref_curve"]:
            sav_yr = (t["none"] - pc["total"]) / len(out["days"]) * 365
            print(f"  pref €{pc['pref']:.2f} +beta €{pc['bx']:.2f}: €{sav_yr:.0f}/jr  "
                  f"zelfvoorziening {pc['zelfvoorziening_pct']}%  "
                  f"export {pc['export_kwh_dag']} kWh/dag  (deg={pc['deg']})")


if __name__ == "__main__":
    main()
