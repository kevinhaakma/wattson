"""Backtester: speelt de historie af tegen vier strategieën.

- none      : geen accu (nulmeting)
- naive     : goedkoopste 3 uur laden / duurste 4 uur ontladen (vaste heuristiek)
- planner   : rolling-horizon DP met alleen causaal beschikbare informatie
- hindsight : DP met volledige kennis — het theoretische plafond

Alle strategieën lopen door dezelfde accu-twin (planner.hour_result), dus de
boekhouding is identiek en de verschillen zijn puur strategie.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "wattson_ems"))
import planner as P  # noqa: E402

DATASET = os.path.join(HERE, "data", "dataset.json")


def load_rows():
    return json.load(open(DATASET, encoding="utf-8"))["rows"]


def mk_step(r, wedge):
    # sell_ok=True: exporteren is een DP-afweging (doelfunctie beslist),
    # geen vaste drempel — de verkoop-switch staat in werkelijkheid aan.
    return P.Step(
        price_imp=r["price"],
        price_exp=max(r["price"] - wedge, -0.5),
        load_w=r["house"] * 1000.0,
        pv_w=r["pv"] * 1000.0,
        ev_charging=r["ev"] > 0.5,
        sell_ok=True,
    )


def risk_steps():
    """Getrainde onzekerheids-curve (fit_risk.py); leeg als nog niet getraind."""
    path = os.path.join(HERE, "data", "risk.json")
    if not os.path.exists(path):
        return ()
    return tuple(json.load(open(path, encoding="utf-8"))["shape_steps"])


def mk_params(deg, pref=0.0, risk_k=0.0, beta_extra=0.0):
    """Accu-twin met apparaat-realistische grenzen (2400 AC: laden 2000 W via
    opties, ontladen 1400 W = inverse_max_power) en het gekalibreerde
    verliesmodel incl. standby. pref = zelfvoorzienings-voorkeur (alpha=beta);
    beta_extra = extra export-korting bovenop pref (asymmetrie: maakt
    centen-trades onaantrekkelijk zonder huisdekking te raken);
    risk_k = sterkte van de onzekerheids-discount."""
    return P.Params(
        deg_cost=deg, alpha=pref, beta=pref + beta_extra, standby_w=6.0,
        risk_k=risk_k, risk_steps=risk_steps() if risk_k > 0.0 else (),
        p_charge_max_w=2000.0, p_discharge_max_w=1400.0,
        charge_levels=(0.0, 500.0, 1000.0, 1500.0, 2000.0),
        discharge_levels=(0.0, 350.0, 700.0, 1050.0, 1400.0),
    )


def cash_params(params):
    """Kas-boekhouding: zelfde fysica en deg, maar zonder voorkeurs-euro's of
    onzekerheids-discount — die sturen alleen het plan, nooit de kosten."""
    q = P.Params(**params.to_dict())
    q.alpha = 0.0
    q.beta = 0.0
    q.risk_k = 0.0
    return q


class LoadForecaster:
    """Causale huislast-voorspelling: mediaan per (uur, weekend) over trailing 28 dagen."""

    def __init__(self, rows, percentile=0.5):
        self.rows = rows
        self.percentile = percentile

    def profile_at(self, idx):
        r0 = self.rows[idx]
        t_lo = r0["t"] - 28 * 24 * 3600 * 1000
        buckets = {}
        j = idx - 1
        while j >= 0 and self.rows[j]["t"] >= t_lo:
            r = self.rows[j]
            buckets.setdefault((r["loc_hour"], r["weekend"]), []).append(r["house"])
            j -= 1
        prof = {}
        for k, v in buckets.items():
            v.sort()
            prof[k] = v[min(len(v) - 1, int(len(v) * self.percentile))]
        return prof


class PvBias:
    """Causale bias-correctie op Forecast.Solar: trailing 14 dagen actual/forecast."""

    def __init__(self, rows):
        self.rows = rows

    def at(self, idx):
        r0 = self.rows[idx]
        t_lo = r0["t"] - 14 * 24 * 3600 * 1000
        fc = act = 0.0
        j = idx - 1
        while j >= 0 and self.rows[j]["t"] >= t_lo:
            fc += self.rows[j]["pvfc"]
            act += self.rows[j]["pv"]
            j -= 1
        if fc < 5.0:
            return 1.0
        return max(0.7, min(1.6, act / fc))


def known_horizon(rows, idx):
    """Indices met op dat moment bekende prijzen: rest van vandaag, plus morgen
    wanneer het al ná 15:00 lokaal is (day-ahead publicatie)."""
    r0 = rows[idx]
    today = r0["loc_date"]
    incl_tomorrow = r0["loc_hour"] >= 15
    out = []
    for j in range(idx, min(idx + 40, len(rows))):
        r = rows[j]
        if r["loc_date"] == today:
            out.append(j)
        elif incl_tomorrow and (r["t"] - r0["t"]) <= 34 * 3600 * 1000:
            out.append(j)
        else:
            break
    return out


def run_none(rows, params, wedge):
    cash = cash_params(params)
    costs = []
    for r in rows:
        c, _, _, _ = P.hour_result(mk_step(r, wedge), 0.0, cash.soc_min_kwh, cash)
        costs.append(c)
    return costs, [params.soc_min_kwh] * len(rows), [0.0] * len(rows), [0.0] * len(rows)


def run_naive(rows, params, wedge):
    by_day = {}
    for i, r in enumerate(rows):
        by_day.setdefault(r["loc_date"], []).append(i)
    plan_action = {}
    for day, idxs in by_day.items():
        prices = sorted(idxs, key=lambda i: rows[i]["price"])
        for i in prices[:3]:
            plan_action[i] = params.p_charge_max_w
        for i in sorted(idxs, key=lambda i: -rows[i]["price"])[:4]:
            if i not in plan_action:
                plan_action[i] = -params.p_discharge_max_w
    cash = cash_params(params)
    costs, socs, acts, thrus = [], [], [], []
    soc = cash.soc_min_kwh
    for i, r in enumerate(rows):
        c, soc, a, th = P.hour_result(mk_step(r, wedge), plan_action.get(i, 0.0), soc, cash)
        costs.append(c); socs.append(soc); acts.append(a); thrus.append(th)
    return costs, socs, acts, thrus


def run_hindsight(rows, params, wedge):
    cash = cash_params(params)
    steps = [mk_step(r, wedge) for r in rows]
    setpoints, _ = P.plan(steps, params.soc_min_kwh, params, terminal_value=0.0)
    costs, socs, acts, thrus = [], [], [], []
    soc = cash.soc_min_kwh
    for st, a in zip(steps, setpoints):
        c, soc, act, th = P.hour_result(st, a, soc, cash)
        costs.append(c); socs.append(soc); acts.append(act); thrus.append(th)
    return costs, socs, acts, thrus


def run_planner(rows, params, wedge, load_pct=0.5, term_frac=6):
    cash = cash_params(params)  # plannen met voorkeur, afrekenen in kasgeld
    lf = LoadForecaster(rows, load_pct)
    pb = PvBias(rows)
    costs, socs, acts, thrus = [], [], [], []
    soc = params.soc_min_kwh
    prof, bias = {}, 1.0
    for i, r in enumerate(rows):
        if i % 6 == 0:  # profiel/bias hoeven niet elk uur opnieuw
            prof = lf.profile_at(i)
            bias = pb.at(i)
        hz = known_horizon(rows, i)
        steps = []
        for k, j in enumerate(hz):
            rj = rows[j]
            if k == 0:
                steps.append(mk_step(rj, wedge))  # heden: actuele meting
            else:
                load = prof.get((rj["loc_hour"], rj["weekend"]), 0.35) * 1000.0
                steps.append(P.Step(
                    price_imp=rj["price"],
                    price_exp=max(rj["price"] - wedge, -0.5),
                    load_w=load,
                    pv_w=rj["pvfc"] * bias * 1000.0,
                    ev_charging=False,
                    sell_ok=True,
                ))
        future_prices = [s.price_imp for s in steps]
        tv = P.terminal_value_from_prices(future_prices, params) if term_frac else 0.0
        setpoints, _ = P.plan(steps, soc, params, terminal_value=tv)
        # voer alleen het eerste uur uit, op de werkelijkheid
        c, soc, a, th = P.hour_result(mk_step(r, wedge), setpoints[0], soc, cash)
        costs.append(c); socs.append(soc); acts.append(a); thrus.append(th)
    return costs, socs, acts, thrus


STRATEGIES = {
    "none": run_none,
    "naive": run_naive,
    "planner": run_planner,
    "hindsight": run_hindsight,
}


def evaluate(rows, params, wedge, **kw):
    out = {}
    for name, fn in STRATEGIES.items():
        costs, socs, acts, thrus = fn(rows, params, wedge) if name != "planner" else fn(rows, params, wedge, **kw)
        out[name] = {"costs": costs, "socs": socs, "acts": acts, "thrus": thrus, "total": sum(costs)}
    return out
