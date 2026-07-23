"""Traint het onzekerheids-model: hoe onbetrouwbaar is de eigen prognose
per vooruitkijk-afstand — het 'gevoel' waarmee de DP toekomstwaarde
verdisconteert (zekere zelfvoorziening nú verslaat een onzeker plan straks).

Meting: voor elk uur in de historie voorspellen we causaal (zelfde
forecasters als de backtest: mediaan-lastprofiel + bias-gecorrigeerde
Forecast.Solar) wat de netto huisflow (last − PV) over lead 1..24 uur zou
zijn, en vergelijken met de realisatie. De MAE per lead, genormaliseerd op
zijn maximum, is de shape-curve; de sterkte (risk_k) is een gedragsknop
voor de grid-search.

Output: data/risk.json -> export_params.py neemt hem op in params.json.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "wattson_ems"))
import backtest as B  # noqa: E402

MAX_LEAD = 24


def main():
    rows = B.load_rows()
    lf = B.LoadForecaster(rows, 0.5)
    pb = B.PvBias(rows)

    # CUMULATIEVE netto-fout per lead: fouten per uur zijn vlak (~0,74 kWh,
    # huishoudruis), maar over een plan stapelen ze — en dát bepaalt hoe
    # onzeker de waarde van 'straks' is t.o.v. 'nu'.
    abs_cum = [0.0] * (MAX_LEAD + 1)
    cnt = [0] * (MAX_LEAD + 1)
    prof, bias = {}, 1.0
    for i in range(len(rows) - MAX_LEAD):
        if i % 6 == 0:
            prof = lf.profile_at(i)
            bias = pb.at(i)
        if not prof:
            continue
        cum = 0.0
        for lead in range(1, MAX_LEAD + 1):
            r = rows[i + lead]
            load_pred = prof.get((r["loc_hour"], r["weekend"]), 0.35)
            pv_pred = r["pvfc"] * bias
            cum += (load_pred - pv_pred) - (r["house"] - r["pv"])
            abs_cum[lead] += abs(cum)
            cnt[lead] += 1

    cmae = [abs_cum[k] / cnt[k] if cnt[k] else 0.0 for k in range(MAX_LEAD + 1)]
    top = max(cmae[1:]) or 1.0
    # per-stap-increment van de genormaliseerde cumulatieve onzekerheid:
    # de planner vermenigvuldigt V per stap met (1 - k*step); het product
    # benadert dan k * cumulatieve-shape(lead) — de gemeten curve zelf
    steps = [0.0] + [round(max(cmae[k] - cmae[k - 1], 0.0) / top, 4)
                     for k in range(1, MAX_LEAD + 1)]

    out = {"max_lead": MAX_LEAD, "cum_mae_kwh": [round(m, 3) for m in cmae],
           "shape_steps": steps}
    json.dump(out, open(os.path.join(HERE, "data", "risk.json"), "w"), indent=1)
    print("lead  cumMAE(kWh)  stap")
    for k in range(1, MAX_LEAD + 1):
        print(f"{k:4d}  {cmae[k]:7.3f}     {steps[k]:.4f}")
    print(f"\nrisk.json geschreven ({cnt[1]} meetpunten per lead)")


if __name__ == "__main__":
    main()
