"""Bouwt uit raw_stats.json een schoon uurlijks dataset.json voor de trainer.

Per uur (UTC-start): prijs, import/export kWh, PV kWh, EV-laden kWh (thuis),
PV-forecast kWh, afgeleide huislast kWh (excl. EV en accu) + kwaliteitsflag.
"""
import json
import os
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw_stats.json")
OUT = os.path.join(HERE, "data", "dataset.json")

# accu geïnstalleerd ~2026-07-07; daarna vervuilt hij de P1-huislast-afleiding
CUTOFF_END = "2026-07-07T00:00:00+00:00"

S_PRICE = "sensor.zonneplan_current_electricity_tariff"
S_IMP = "sensor.p1_meter_energy_import"
S_EXP = "sensor.p1_meter_energy_export"
S_PV = "sensor.growatt_total_energy_production"
S_KEBA = "sensor.keba_p20_charging_power"
S_JIMMY = "sensor.jimmy_charger_power"
S_PVFC = "sensor.power_production_now"


def main():
    raw = json.load(open(RAW, encoding="utf-8"))["stats"]
    idx = {k: {r["start"]: r for r in rows} for k, rows in raw.items()}
    cutoff_ms = datetime.fromisoformat(CUTOFF_END).timestamp() * 1000

    hours = sorted(idx[S_PRICE].keys())
    keba_start = min(idx[S_KEBA].keys()) if idx.get(S_KEBA) else None

    def keba_at(t):
        return idx.get(S_KEBA, {}).get(t, {}).get("mean")

    def keba_actief_nabij(t):
        """Keba-activiteit in dit uur of een buuruur (sessie-continuïteit)."""
        for dt_ms in (-3600_000, 0, 3600_000):
            k = keba_at(t + dt_ms)
            if k is not None and k > 0.1:
                return True
        return False

    # zonder EV-telemetrie voor een uur is dat uur alleen bruikbaar als
    # EV-laden op meterfysica uit te sluiten is: bij uren MET voertuig-
    # telemetrie (Jimmy meldt 0) komt import boven deze grens niet voor
    # (validatie in de print hieronder)
    IMP_ZONDER_EV_MAX = 3.0

    rows, dropped = [], {"no_p1": 0, "glitch": 0, "post_battery": 0, "ev_onbekend": 0}
    imp_check = []  # gedekte uren zonder EV: valideert IMP_ZONDER_EV_MAX
    for t in hours:
        if t >= cutoff_ms:
            dropped["post_battery"] += 1
            continue
        price = idx[S_PRICE].get(t, {}).get("mean")
        imp = idx[S_IMP].get(t, {}).get("change")
        exp = idx[S_EXP].get(t, {}).get("change")
        if price is None or imp is None or exp is None:
            dropped["no_p1"] += 1
            continue
        pv = idx[S_PV].get(t, {}).get("change") or 0.0
        # Thuisladen bepalen uit twee onafhankelijke bewijzen:
        # 1. Keba (de wallbox zelf) toont activiteit in dit of een buuruur —
        #    maar de integratie mist ook HELE sessies (17-25 juni 2026: nachten
        #    met 7 kWh import terwijl Keba niets meldde), dus Keba-stilte is
        #    geen bewijs van niet-thuis-laden.
        # 2. Meterfysica: Jimmy (voertuigtelemetrie) meet ook laden elders,
        #    maar een thuissessie MOET door de aansluiting (import + PV −
        #    export) gedragen worden. Dekt de levering het voertuigvermogen,
        #    dan laadt hij thuis; blijft de levering er ver onder, dan kan de
        #    sessie niet thuis zijn (huis-baseline is ~0,2-1,5 kWh/u). De
        #    smalle tussenband is onbeslisbaar en valt uit de dataset.
        # Ongegate Jimmy-aftrek is fout de andere kant op: die drukte de
        # avond-huisvraag 50-90% (finding 2026-07-15).
        # pre-accu kan alleen PV exporteren: bruto export ver boven de eigen
        # productie is een recorder-glitch (26/04 20:00: 14,05 in én 6,79 uit
        # in één uur bij 0,8 PV) — die haalde de netto-caps niet
        if exp > pv + 0.3:
            dropped["glitch"] += 1
            continue
        keba = keba_at(t)
        jimmy = idx[S_JIMMY].get(t, {}).get("mean")
        supply = imp + pv - exp
        keba_ok = keba_start is not None and t >= keba_start and keba_actief_nabij(t)
        vehicle_idle = False
        if jimmy is not None and jimmy > 0.1:
            if keba_ok or supply >= jimmy - 0.3:
                ev = max(keba or 0.0, jimmy)
            elif supply < jimmy - 1.0:
                ev = keba or 0.0  # elders geladen: huislast onaangetast
            else:
                dropped["ev_onbekend"] += 1
                continue
        else:
            vehicle_idle = jimmy is not None  # voertuig meldt "laadt niet"
            ev = max(keba or 0.0, jimmy or 0.0) if keba_ok else (keba or 0.0)
            if ev <= 0.1 and jimmy is None and imp > IMP_ZONDER_EV_MAX:
                # geen voertuigtelemetrie voor dit uur (sensor nog niet actief
                # of gat) en de import kan een verborgen sessie bevatten:
                # maart/april- en 3-mei-nachten met 4-7 kWh om 02:00 bleken
                # exact het juni-sessiepatroon en vervuilden het getrainde
                # nachtprofiel tot 7,076 kWh voor het 02:00-slot
                dropped["ev_onbekend"] += 1
                continue
        pvfc = (idx[S_PVFC].get(t, {}).get("mean") or 0.0) / 1000.0
        house = imp - exp + pv - ev
        if house < -1.5 or house > 12.0 or imp > 25 or exp > 25 or pv > 10:
            dropped["glitch"] += 1
            continue
        if vehicle_idle:
            imp_check.append(imp)  # ijk-uur voor IMP_ZONDER_EV_MAX
        dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        loc = dt + timedelta(hours=2)  # Europe/Amsterdam zomertijd; hele dataset valt in CEST
        rows.append({
            "t": t,
            "iso": dt.isoformat(),
            "loc_date": loc.strftime("%Y-%m-%d"),
            "loc_hour": loc.hour,
            "weekend": loc.weekday() >= 5,
            "price": round(price, 5),
            "imp": round(imp, 4),
            "exp": round(exp, 4),
            "pv": round(pv, 4),
            "ev": round(ev, 4),
            "pvfc": round(pvfc, 4),
            "house": round(max(house, 0.0), 4),
        })
    json.dump({"rows": rows, "dropped": dropped}, open(OUT, "w", encoding="utf-8"))

    days = sorted({r["loc_date"] for r in rows})
    per_day = {}
    for r in rows:
        per_day.setdefault(r["loc_date"], []).append(r)
    complete = [d for d in days if len(per_day[d]) >= 22]
    print(f"{len(rows)} uren, {len(days)} dagen ({len(complete)} compleet), "
          f"{days[0]} → {days[-1]}, dropped={dropped}")
    tot = lambda k: sum(r[k] for r in rows)
    print(f"totalen: import {tot('imp'):.0f} kWh, export {tot('exp'):.0f} kWh, "
          f"pv {tot('pv'):.0f} kWh, ev-thuis {tot('ev'):.0f} kWh, huis {tot('house'):.0f} kWh")
    print(f"gem prijs €{tot('price')/len(rows):.3f}, huis {tot('house')/len(days):.1f} kWh/dag")
    if imp_check:
        boven = sum(1 for v in imp_check if v > IMP_ZONDER_EV_MAX)
        print(f"validatie IMP_ZONDER_EV_MAX={IMP_ZONDER_EV_MAX}: {boven}/{len(imp_check)} "
              f"uren met voertuig-bevestigd 'laadt niet' erboven (max {max(imp_check):.2f} kWh)")


if __name__ == "__main__":
    main()
