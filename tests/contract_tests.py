"""Contract-tests voor de accu-adapters (adapters.py) — draait standalone.

Laadt adapters.py rechtstreeks (geen homeassistant nodig) en verifieert per
adapter hetzelfde contract met een fake hass/coordinator:
  - acties vertalen naar de juiste service-calls;
  - P1/huisvraag-begrenzing en verkopen-uitzondering;
  - eenheid-normalisatie (kW-numbers krijgen kW-waarden);
  - noodstop-maatregelen en limiet-heropening na een trip;
  - telemetrie: alleen verse waarden tellen.

Draaien:  python tests/contract_tests.py
"""
import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ADAPTERS_PATH = HERE.parent / "custom_components" / "wattson_ems" / "adapters.py"

spec = importlib.util.spec_from_file_location("wattson_adapters", ADAPTERS_PATH)
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeState:
    def __init__(self, state, attributes=None, age_s=0):
        self.state = state
        self.attributes = attributes or {}
        self.last_updated = NOW - timedelta(seconds=age_s)


class FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))


class FakeHass:
    def __init__(self, states=None):
        self.states = SimpleNamespace(get=lambda e: (states or {}).get(e))
        self._states = states or {}
        self.services = FakeServices()


class FakeCoordinator:
    """Minimale coordinator: entity-mapping + params + verse-data-helper."""

    def __init__(self, hass, **entities):
        self.hass = hass
        self.params = SimpleNamespace(p_charge_max_w=1600.0, p_discharge_max_w=800.0)
        self._tripped = None
        self.last_applied = None
        defaults = dict(
            ent_p1="", ent_zd_operation="", ent_zd_manual="", ent_zd_inlim="",
            ent_zd_outlim="", ent_zd_chg="", ent_zd_dis="", ent_ms_mode="",
            ent_ms_charge="", ent_ms_discharge="", ent_gen_power="",
            ent_gen_charge="", ent_gen_discharge="", ent_bat_chg="", ent_bat_dis="",
        )
        defaults.update(entities)
        for key, value in defaults.items():
            setattr(self, key, value)

    def _fresh_power_w(self, entity, max_age_s=180):
        return A.read_fresh_power_w(self.hass, entity, max_age_s, NOW)


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond)})
    print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""))


def calls_for(hass, entity):
    return [(d, s, data) for d, s, data in hass.services.calls
            if data.get("entity_id") == entity]


def last_value(hass, entity):
    matching = calls_for(hass, entity)
    return matching[-1][2].get("value") if matching else None


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# scenario's
# ---------------------------------------------------------------------------

def zendure_hass():
    return FakeHass({
        "select.zd_op": FakeState("off", {"options": []}),
        "number.zd_manual": FakeState("0", {"min": -2400, "max": 2400, "unit_of_measurement": "W"}),
        "number.zd_in": FakeState("0", {"min": 0, "max": 2400, "unit_of_measurement": "W"}),
        "number.zd_out": FakeState("0", {"min": 0, "max": 1400, "unit_of_measurement": "W"}),
        "sensor.zd_chg": FakeState("0", {"unit_of_measurement": "W"}),
        "sensor.zd_dis": FakeState("0", {"unit_of_measurement": "W"}),
    })


def make_zendure(hass):
    c = FakeCoordinator(
        hass, ent_zd_operation="select.zd_op", ent_zd_manual="number.zd_manual",
        ent_zd_inlim="number.zd_in", ent_zd_outlim="number.zd_out",
        ent_zd_chg="sensor.zd_chg", ent_zd_dis="sensor.zd_dis")
    return c, A.create_adapter("zendure", c)


def test_zendure():
    # laden: manual + negatief vermogen, input-limiet open
    hass = zendure_hass()
    c, ad = make_zendure(hass)
    run(ad.apply("laden", 800.0))
    check("zendure: laden -> manual -800 W", last_value(hass, "number.zd_manual") == -800.0)
    check("zendure: laden opent input-limiet", last_value(hass, "number.zd_in") == 1600.0)
    sel = [d for d in hass.services.calls if d[1] == "select_option"]
    check("zendure: operation -> manual", sel and sel[-1][2]["option"] == "manual")

    # ontladen: P1-matching, output-limiet begrensd op setpoint
    hass = zendure_hass()
    c, ad = make_zendure(hass)
    run(ad.apply("ontladen", 600.0))
    check("zendure: ontladen -> smart_discharging",
          any(d[2].get("option") == "smart_discharging" for d in hass.services.calls if d[1] == "select_option"))
    check("zendure: output-limiet = gepland setpoint", last_value(hass, "number.zd_out") == 600.0)

    # klein setpoint krijgt de min_setpoint-vloer
    hass = zendure_hass()
    c, ad = make_zendure(hass)
    run(ad.apply("ontladen", 40.0))
    check("zendure: output-limiet minimaal min_setpoint",
          last_value(hass, "number.zd_out") == ad.caps.min_setpoint_w)

    # verkopen: manual met positief vermogen, gemaximeerd op p_discharge_max
    hass = zendure_hass()
    c, ad = make_zendure(hass)
    applied = run(ad.apply("verkopen", 1200.0))
    check("zendure: verkopen -> manual +800 W (max)",
          last_value(hass, "number.zd_manual") == 800.0 and applied == 800.0)

    # noodstop: alleen de foute richting dicht
    hass = zendure_hass()
    c, ad = make_zendure(hass)
    run(ad.emergency_stop("ontladen"))
    check("zendure: noodstop ontladen -> outlim 0, inlim ongemoeid",
          last_value(hass, "number.zd_out") == 0.0 and last_value(hass, "number.zd_in") is None)
    run(ad.emergency_stop(None))
    check("zendure: noodstop onbekende richting -> beide limieten 0",
          last_value(hass, "number.zd_in") == 0.0)

    # na een trip: geopende richting blijft dicht
    hass = zendure_hass()
    c, ad = make_zendure(hass)
    c._tripped = "ontladen"
    run(ad.apply("ontladen", 600.0))
    check("zendure: getripte richting wordt niet heropend",
          last_value(hass, "number.zd_out") is None)
    run(ad.apply("laden", 400.0))
    check("zendure: niet-getripte richting werkt wel", last_value(hass, "number.zd_in") == 1600.0)

    # enforce_rest: verse activiteit -> beide limieten 0
    hass = zendure_hass()
    hass._states["sensor.zd_dis"] = FakeState("450", {"unit_of_measurement": "W"}, age_s=10)
    c, ad = make_zendure(hass)
    run(ad.enforce_rest())
    check("zendure: enforce_rest bij verse activiteit -> limieten 0",
          last_value(hass, "number.zd_in") == 0.0 and last_value(hass, "number.zd_out") == 0.0)

    # enforce_rest: bevroren telemetrie is geen bewijs
    hass = zendure_hass()
    hass._states["sensor.zd_dis"] = FakeState("450", {"unit_of_measurement": "W"}, age_s=600)
    c, ad = make_zendure(hass)
    run(ad.enforce_rest())
    check("zendure: enforce_rest negeert bevroren telemetrie",
          not calls_for(hass, "number.zd_in") and not calls_for(hass, "number.zd_out"))

    # kW-number: watt-waarde wordt naar kW omgerekend
    hass = zendure_hass()
    hass._states["number.zd_manual"] = FakeState("0", {"min": -2.4, "max": 2.4, "unit_of_measurement": "kW"})
    c, ad = make_zendure(hass)
    run(ad.apply("laden", 800.0))
    check("zendure: kW-number krijgt kW-waarde", last_value(hass, "number.zd_manual") == -0.8)

    # number-clamp: waarde boven max wordt geclampt (out max 1400 < 1600)
    hass = zendure_hass()
    hass._states["number.zd_out"] = FakeState("0", {"min": 0, "max": 700, "unit_of_measurement": "W"})
    c, ad = make_zendure(hass)
    run(ad.apply("ontladen", 800.0))
    check("zendure: limiet geclampt op entity-max", last_value(hass, "number.zd_out") == 700.0)


def test_marstek():
    def hass_select(options):
        return FakeHass({
            "select.ms_mode": FakeState("Stop", {"options": options}),
            "number.ms_chg": FakeState("0", {"min": 0, "max": 2500, "unit_of_measurement": "W"}),
            "number.ms_dis": FakeState("0", {"min": 0, "max": 2500, "unit_of_measurement": "W"}),
            "sensor.p1": FakeState("300", {"unit_of_measurement": "W"}),
        })

    def make(hass):
        c = FakeCoordinator(hass, ent_ms_mode="select.ms_mode", ent_ms_charge="number.ms_chg",
                            ent_ms_discharge="number.ms_dis", ent_p1="sensor.p1")
        return c, A.create_adapter("marstek", c)

    # ontladen met P1-cap: 800 gevraagd, 300 import -> 300
    hass = hass_select(["Stop", "Charge", "Discharge"])
    c, ad = make(hass)
    applied = run(ad.apply("ontladen", 800.0))
    check("marstek: ontladen gecapt op P1-import",
          applied == 300.0 and last_value(hass, "number.ms_dis") == 300.0)
    sel = [d for d in hass.services.calls if d[1] == "select_option"]
    check("marstek: mode -> Discharge", sel and sel[-1][2]["option"] == "Discharge")

    # NL-labels + laden matcht nooit een ontlaadoptie
    hass = hass_select(["Uit", "Ontladen", "Laden"])
    c, ad = make(hass)
    run(ad.apply("laden", 500.0))
    sel = [d for d in hass.services.calls if d[1] == "select_option"]
    check("marstek: NL-label 'Laden' correct gekozen (niet 'Ontladen')",
          sel and sel[-1][2]["option"] == "Laden")

    # verkopen: geen P1-cap, wel p_discharge_max
    hass = hass_select(["Stop", "Charge", "Discharge"])
    c, ad = make(hass)
    applied = run(ad.apply("verkopen", 1200.0))
    check("marstek: verkopen zonder P1-cap, max 800", applied == 800.0)

    # number-mode (register 42010)
    hass = FakeHass({
        "number.ms_mode": FakeState("0", {"min": 0, "max": 2}),
        "number.ms_dis": FakeState("0", {"min": 0, "max": 2500, "unit_of_measurement": "W"}),
        "sensor.p1": FakeState("500", {"unit_of_measurement": "W"}),
    })
    c = FakeCoordinator(hass, ent_ms_mode="number.ms_mode", ent_ms_discharge="number.ms_dis",
                        ent_p1="sensor.p1")
    ad = A.create_adapter("marstek", c)
    run(ad.apply("ontladen", 400.0))
    check("marstek: number-mode krijgt 2 (discharge)", last_value(hass, "number.ms_mode") == 2)

    # rust: mode 0
    run(ad.apply("rust", 0.0))
    check("marstek: rust -> mode 0", last_value(hass, "number.ms_mode") == 0)


def test_generic():
    def make(**kw):
        hass = FakeHass({
            "number.gp": FakeState("0", {"min": -3000, "max": 3000, "unit_of_measurement": "W"}),
            "number.gc": FakeState("0", {"min": 0, "max": 3000, "unit_of_measurement": "W"}),
            "number.gd": FakeState("0", {"min": 0, "max": 3000, "unit_of_measurement": "W"}),
            "sensor.p1": FakeState("-150", {"unit_of_measurement": "W"}),
        })
        c = FakeCoordinator(hass, ent_p1="sensor.p1", **kw)
        return hass, c, A.create_adapter("generic", c)

    # signed number: ontladen bij export (P1 negatief) -> 0
    hass, c, ad = make(ent_gen_power="number.gp")
    applied = run(ad.apply("ontladen", 800.0))
    check("generic: ontladen bij export -> 0 W", applied == 0.0 and last_value(hass, "number.gp") == 0.0)

    # p1_cap=False (discharge-guard-pad): geen momentane cap
    hass, c, ad = make(ent_gen_power="number.gp")
    applied = run(ad.apply("ontladen", 650.0, p1_cap=False))
    check("generic: p1_cap=False laat delta-waarde door",
          applied == 650.0 and last_value(hass, "number.gp") == -650.0)

    # losse laad/ontlaad-numbers: laden zet ontladen op 0
    hass, c, ad = make(ent_gen_charge="number.gc", ent_gen_discharge="number.gd")
    run(ad.apply("laden", 900.0))
    check("generic: losse numbers -> laden 900, ontladen 0",
          last_value(hass, "number.gc") == 900.0 and last_value(hass, "number.gd") == 0.0)

    # verkopen: max ontlaadvermogen, geen P1-cap
    hass, c, ad = make(ent_gen_power="number.gp")
    applied = run(ad.apply("verkopen", 2000.0))
    check("generic: verkopen -> -800 W (max), geen P1-cap",
          applied == 800.0 and last_value(hass, "number.gp") == -800.0)

    # rust: signed number naar 0
    hass, c, ad = make(ent_gen_power="number.gp")
    run(ad.apply("rust", 0.0))
    check("generic: rust -> 0 W", last_value(hass, "number.gp") == 0.0)


def test_telemetry_and_caps():
    hass = FakeHass({
        "sensor.chg_kw": FakeState("0.45", {"unit_of_measurement": "kW"}, age_s=30),
        "sensor.dis_old": FakeState("500", {"unit_of_measurement": "W"}, age_s=400),
    })
    c = FakeCoordinator(hass, ent_bat_chg="sensor.chg_kw", ent_bat_dis="sensor.dis_old")
    ad = A.create_adapter("generic", c)
    chg_e, dis_e = ad.telemetry_entities()
    check("telemetrie: kW-sensor vers -> 450 W", c._fresh_power_w(chg_e) == 450.0)
    check("telemetrie: bevroren sensor -> None", c._fresh_power_w(dis_e) is None)

    z = A.create_adapter("zendure", FakeCoordinator(FakeHass({})))
    m = A.create_adapter("marstek", FakeCoordinator(FakeHass({})))
    g = A.create_adapter("generic", FakeCoordinator(FakeHass({})))
    check("caps: alleen zendure heeft p1_matching/device_limits/surplus",
          z.caps.p1_matching and z.caps.device_limits and z.caps.surplus_mode
          and not (m.caps.p1_matching or m.caps.device_limits or m.caps.surplus_mode)
          and not (g.caps.p1_matching or g.caps.device_limits or g.caps.surplus_mode))
    check("caps: onbekend merk valt terug op generic",
          isinstance(A.create_adapter("nieuwmerk", FakeCoordinator(FakeHass({}))), A.GenericAdapter))


def main():
    test_zendure()
    test_marstek()
    test_generic()
    test_telemetry_and_caps()
    fails = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
