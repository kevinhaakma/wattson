"""Regressiescenario's uit echte Home Assistant-data (14-17 juli 2026).

De historische P1-flow is eerst ontdaan van Wattsons eigen accuvermogen:

    bron_p1 = p1 - acculaden + accuontladen
    huislast = bron_p1 + pv - ev_thuis

Daardoor test de planner de oorspronkelijke woning/PV/prijssituatie en niet
zijn eigen eerdere ingrepen. De scenario's gebruiken een gestandaardiseerde
SoC; de werkelijk gemeten SoC is alleen bronmetadata en geen testinvoer.
"""
import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

spec = importlib.util.spec_from_file_location(
    "wattson_planner_ha_scenarios",
    ROOT / "custom_components" / "wattson_ems" / "planner.py",
)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

PARAMS = json.loads(
    (ROOT / "custom_components" / "wattson_ems" / "params.json").read_text(
        encoding="utf-8"
    )
)


def planner_params():
    battery = PARAMS["battery"]
    return P.Params(
        capacity_kwh=5.76,
        soc_min_kwh=0.576,
        soc_max_kwh=5.76,
        p_charge_max_w=2000.0,
        p_discharge_max_w=1400.0,
        charge_levels=(0.0, 500.0, 1000.0, 1500.0, 2000.0),
        discharge_levels=(0.0, 350.0, 700.0, 1050.0, 1400.0),
        eta_nom=battery["eta_nom"],
        p_fix_w=battery["p_fix_w"],
        standby_w=battery["standby_w"],
        deg_cost=0.02,
        alpha=0.02,
        beta=0.04,
        risk_k=0.05,
        risk_steps=tuple(PARAMS["risk_shape_steps"]),
        # Zelfde SoC-resolutie als de coordinator; het oude raster van
        # 0,08 kWh maskeert de kleine prijsverschillen van dit scenario.
        soc_step_kwh=PARAMS.get("soc_step_kwh", 0.02),
    )


def source_flow(p1_w, charge_w=0.0, discharge_w=0.0):
    return p1_w - max(charge_w, 0.0) + max(discharge_w, 0.0)


def house_load(p1_w, pv_w, charge_w=0.0, discharge_w=0.0, ev_home_w=0.0):
    source = source_flow(p1_w, charge_w, discharge_w)
    return max(source + pv_w - ev_home_w, 0.0)


def make_steps(rows, *, sell=False):
    return [P.Step(price, price, load, pv, sell_ok=sell)
            for price, load, pv in rows]


def run_plan(rows, soc_kwh, *, sell=False):
    params = planner_params()
    steps = make_steps(rows, sell=sell)
    terminal = P.terminal_value_from_prices([row[0] for row in rows], params)
    setpoints, _ = P.plan(steps, soc_kwh, params, terminal_value=terminal)
    return setpoints


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


# Live Wattson-plansnapshot 17-07-2026 13:04 CEST. Het eerste uur is uit
# actuele HA-metingen opgebouwd; de rest is de toen gepubliceerde prijs/PV/
# last-horizon van sensor.wattson_advies.
LIVE_17_JUL_13 = [
    (0.24967, 508, 2740),
    (0.2499725, 1071, 3520),
    (0.2561556, 970, 3345),
    (0.2694535, 1222, 2857),
    (0.2849990, 1008, 2151),
    (0.2979973, 825, 1368),
    (0.3145230, 527, 663),
    (0.3256308, 576, 174),
    (0.3334716, 421, 0),
    (0.3302651, 322, 0),
    (0.3224757, 273, 0),
]

# Uurstatistieken 16-07-2026 12:00-22:00 CEST. Huislast is vooraf
# gereconstrueerd uit P1, PV en de gemeten accuflow; EV-uren zijn uitgesloten.
HA_16_JUL_12 = [
    (0.24639, 365, 909),
    (0.23826, 259, 1375),
    (0.24768, 559, 754),
    (0.26230, 573, 892),
    (0.27474, 572, 788),
    (0.29643, 689, 1676),
    (0.31839, 867, 760),
    (0.36685, 665, 484),
    (0.41771, 504, 533),
    (0.41572, 455, 146),
    (0.38112, 281, 0),
]


def main():
    # Bewijs dat de fixture de accu werkelijk uit de vergelijking haalt:
    # 16 juli 13:00: P1 +499 W terwijl de accu 1615 W laadde. Zonder accu was
    # er dus 1116 W bronexport en 259 W huislast bij 1375 W PV.
    src = source_flow(499, charge_w=1615, discharge_w=0)
    load = house_load(499, 1375, charge_w=1615, discharge_w=0)
    check("HA-reconstructie verwijdert acculaden uit P1", src == -1116)
    check("HA-reconstructie levert de oorspronkelijke huislast", load == 259)

    # Vandaag: nu is goedkoper dan het volgende uur en beide uren hebben meer
    # zonneoverschot dan het benodigde laadvermogen. Uitstel is gedomineerd.
    live = run_plan(LIVE_17_JUL_13, soc_kwh=0.69)
    check("17 juli 13:00: goedkoopste zonne-uur laadt direct", live[0] > 50)
    check("17 juli 13:00: laden wordt niet naar duurder 14:00 geschoven",
          not (live[0] == 0 and live[1] > 50))

    # Tegenvoorbeeld: op 16 juli was 13:00 aantoonbaar goedkoper dan 12:00.
    # Wachten is hier dus juist, mits de volgende zonneproductie voldoende is.
    before_low = run_plan(HA_16_JUL_12, soc_kwh=0.69)
    check("16 juli 12:00: wacht op werkelijk goedkoper volgend uur",
          before_low[0] == 0 and before_low[1] > 50)

    at_low = run_plan(HA_16_JUL_12[1:], soc_kwh=0.69)
    check("16 juli 13:00: laad zodra het goedkoopste uur begint", at_low[0] > 50)

    # 20:00 had nog 29 W bronexport: zonder verkooptoestemming mag de accu
    # geen extra export veroorzaken, ondanks de recordprijs van 41,8 cent.
    evening_surplus = run_plan(HA_16_JUL_12[8:], soc_kwh=2.88, sell=False)
    check("16 juli 20:00: geen batterij-export zonder verkoopswitch",
          evening_surplus[0] == 0)

    # Om 21:00 was er na verwijderen van de accuflow 309 W echte netvraag.
    # Bij 41,6 cent en voldoende SoC hoort Wattson die huisvraag te dekken.
    evening_load = run_plan(HA_16_JUL_12[9:], soc_kwh=2.88, sell=False)
    check("16 juli 21:00: dure echte huisvraag wordt ontladen",
          evening_load[0] < -250)

    # Vol is vol: ook in het goedkoopste zonne-uur mag de planner niet meer
    # laden dan de fysieke resterende ruimte.
    full = run_plan(LIVE_17_JUL_13, soc_kwh=5.76)
    check("volle accu krijgt geen laadopdracht", full[0] <= 50)

    print("\n9/9 PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        sys.exit(1)
