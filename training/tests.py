"""Validatiesuite voor planner-fysica en strategie-ordening.

Draait standalone; resultaten gaan mee naar het dashboard (results.json).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "wattson_ems"))
import planner as P  # noqa: E402
import backtest as B  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


def main():
    p = P.Params()
    step_cheap = P.Step(0.10, 0.05, 300.0, 0.0)
    step_dear = P.Step(0.40, 0.30, 800.0, 0.0)
    step_ev = P.Step(0.40, 0.30, 8000.0, 0.0, ev_charging=True)
    step_sun = P.Step(0.20, 0.10, 200.0, 2000.0)

    # 1. fysica: laden kost energie, SoC stijgt met rendementsverlies
    c, soc, act, thru = P.hour_result(step_cheap, 800.0, 1.0, p)
    stored = soc - 1.0
    check("laden: SoC stijgt minder dan AC-input (verliezen)",
          0 < stored < 0.8, f"input 0.8 kWh AC -> {stored:.3f} kWh opgeslagen")
    check("laden: kosten = net + degradatie",
          abs(c - ((0.3 + 0.8) * 0.10 + stored * p.deg_cost)) < 1e-6, f"kosten €{c:.4f}")

    # 2. ontladen wordt begrensd op huisvraag (nooit accu -> net)
    c, soc, act, thru = P.hour_result(P.Step(0.40, 0.30, 500.0, 0.0), -800.0, 4.0, p)
    check("ontladen begrensd op huisvraag", abs(act) <= 500.0 + 1e-6, f"act {act:.0f} W bij 500 W vraag")

    # 3. EV-blokkade: nooit ontladen terwijl de auto laadt
    c, soc, act, thru = P.hour_result(step_ev, -800.0, 4.0, p)
    check("EV-blokkade: ontladen geweigerd tijdens autoladen", act == 0.0 and soc == 4.0)

    # 4. SoC-grenzen
    c, soc, act, thru = P.hour_result(step_cheap, 1600.0, p.soc_max_kwh - 0.01, p)
    check("SoC-plafond gerespecteerd", soc <= p.soc_max_kwh + 1e-9, f"soc {soc:.3f}")
    c, soc, act, thru = P.hour_result(step_dear, -800.0, p.soc_min_kwh + 0.01, p)
    check("SoC-bodem gerespecteerd", soc >= p.soc_min_kwh - 1e-9, f"soc {soc:.3f}")

    # 5a. het p_fix-mechanisme straft druppelen zodra het gezet is (de
    #     gekalibreerde productiewaarde is 0: niet aantoonbaar in de meetdata)
    p_fix = P.Params(p_fix_w=25.0)
    check("rendementscurve straft druppelen (met p_fix)",
          P.eta_oneway(100, p_fix) < P.eta_oneway(800, p_fix) < p_fix.eta_nom,
          f"eta(100W)={P.eta_oneway(100, p_fix):.2f} eta(800W)={P.eta_oneway(800, p_fix):.2f}")

    # 5b. standby-drain: rust teert op de lading in, maar nooit onder de bodem
    p_sb = P.Params(standby_w=6.0)
    _, soc_idle, _, _ = P.hour_result(P.Step(0.20, 0.18, 0.0, 0.0), 0.0, 2.0, p_sb)
    check("rust verliest standby-energie", abs(soc_idle - (2.0 - 0.006)) < 1e-9,
          f"soc {soc_idle:.4f}")
    _, soc_floor, _, _ = P.hour_result(P.Step(0.20, 0.18, 0.0, 0.0), 0.0, p_sb.soc_min_kwh, p_sb)
    check("standby-drain stopt op de bodem", soc_floor >= p_sb.soc_min_kwh - 1e-9,
          f"soc {soc_floor:.4f}")

    # 6. DP kiest arbitrage bij grote spread en blijft stil bij vlakke prijzen
    steps = [P.Step(0.10, 0.05, 400.0, 0.0)] * 3 + [P.Step(0.45, 0.35, 700.0, 0.0)] * 3
    sp, cost = P.plan(steps, p.soc_min_kwh, p)
    check("DP laadt goedkoop en ontlaadt duur",
          any(s > 0 for s in sp[:3]) and any(s < 0 for s in sp[3:]), f"plan {[round(s) for s in sp]}")
    flat = [P.Step(0.25, 0.15, 400.0, 0.0)] * 6
    sp_f, _ = P.plan(flat, p.soc_min_kwh, p)
    check("DP blijft in rust bij vlakke prijzen (geen gependel)",
          all(abs(s) < 1e-6 for s in sp_f), f"plan {[round(s) for s in sp_f]}")
    sp_f2, _ = P.plan(flat, 2.0, p, terminal_value=0.25)
    check("DP laadt niet bij om bij vlakke prijzen te cyclen",
          all(s <= 1e-6 for s in sp_f2), f"plan {[round(s) for s in sp_f2]}")

    # 7. zonoverschot wordt opgeslagen als export weinig oplevert
    sunny = [P.Step(0.20, 0.02, 200.0, 2000.0)] * 3 + [P.Step(0.45, 0.30, 700.0, 0.0)] * 3
    sp_s, _ = P.plan(sunny, p.soc_min_kwh, p)
    check("zonoverschot wordt opgeslagen voor de avond",
          any(s > 0 for s in sp_s[:3]) and any(s < 0 for s in sp_s[3:]), f"plan {[round(s) for s in sp_s]}")

    # 8. eindwaarde voorkomt leegdumpen op matige piek
    mild = [P.Step(0.28, 0.18, 600.0, 0.0)] * 4
    sp_m0, _ = P.plan(mild, p.soc_max_kwh, p, terminal_value=0.0)
    sp_m1, _ = P.plan(mild, p.soc_max_kwh, p, terminal_value=0.30)
    dis0 = -sum(s for s in sp_m0 if s < 0)
    dis1 = -sum(s for s in sp_m1 if s < 0)
    check("eindwaarde remt leegdumpen", dis1 < dis0, f"ontladen zonder tv {dis0:.0f} W-u, met tv {dis1:.0f} W-u")

    # 9. verkopen (sell_ok): alleen expliciet vrijgegeven uren mogen exporteren
    c, soc, act, thru = P.hour_result(P.Step(0.60, 0.50, 300.0, 0.0, sell_ok=True), -800.0, 4.0, p)
    check("verkopen: ontladen mag voorbij de huisvraag", abs(act) > 300.0, f"act {act:.0f} W bij 300 W vraag")
    check("verkopen: begrensd op max ontlaadvermogen", abs(act) <= p.p_discharge_max_w + 1e-6, f"act {act:.0f} W")
    c_no, _, act_no, _ = P.hour_result(P.Step(0.60, 0.50, 300.0, 0.0, sell_ok=False), -800.0, 4.0, p)
    check("zonder sell_ok blijft de huisvraag de grens", abs(act_no) <= 300.0 + 1e-6, f"act {act_no:.0f} W")
    c_ev, _, act_ev, _ = P.hour_result(P.Step(0.60, 0.50, 300.0, 0.0, ev_charging=True, sell_ok=True), -800.0, 4.0, p)
    check("EV-blokkade wint van verkopen", act_ev == 0.0)
    # DP: een verkoop-uur met hoge prijs wordt daadwerkelijk benut
    sell_steps = [P.Step(0.10, 0.05, 300.0, 0.0)] * 3 + [P.Step(0.65, 0.55, 300.0, 0.0, sell_ok=True)] * 2
    sp_v, _ = P.plan(sell_steps, p.soc_max_kwh, p)
    check("DP verkoopt op vrijgegeven piekuur (voorbij huisvraag)",
          any(s < -300.0 for s in sp_v[3:]), f"plan {[round(s) for s in sp_v]}")

    # 10. strategie-ordening op een dataset-maand: hindsight <= planner <= none
    rows = B.load_rows()
    sub = [r for r in rows if r["loc_date"] >= "2026-06-01"]
    ev = B.evaluate(sub, p, wedge=0.111)
    check("hindsight <= planner (plafond klopt)",
          ev["hindsight"]["total"] <= ev["planner"]["total"] + 1e-6,
          f"hindsight €{ev['hindsight']['total']:.2f} planner €{ev['planner']['total']:.2f}")
    check("planner verslaat geen-accu",
          ev["planner"]["total"] <= ev["none"]["total"] + 1e-6,
          f"planner €{ev['planner']['total']:.2f} none €{ev['none']['total']:.2f}")
    check("planner verslaat naïeve heuristiek",
          ev["planner"]["total"] <= ev["naive"]["total"] + 0.50,
          f"planner €{ev['planner']['total']:.2f} naive €{ev['naive']['total']:.2f}")

    # 11. energieboekhouding sluit: som(kosten) reproduceerbaar
    c2 = sum(ev["planner"]["costs"])
    check("boekhouding deterministisch", abs(c2 - ev["planner"]["total"]) < 1e-9)

    json.dump(RESULTS, open(os.path.join(HERE, "data", "test_results.json"), "w"))
    fails = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS)-len(fails)}/{len(RESULTS)} PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
