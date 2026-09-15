"""Standalone tests voor de coordinator-kern en de realtime-lagen.

Draait zonder Home Assistant: de homeassistant-modules worden gestubd en het
wattson_ems-pakket wordt zonder __init__.py (dat de HA-setup importeert)
geladen. De adapter is een fake die commando's registreert; tijd (monotonic)
is een instelbare klok zodat dwell/grace/throttle deterministisch testbaar zijn.

Gedekt (het gat dat de losse suites lieten liggen):
- plan-tick: advies, sturing, verkopen-switch
- wissel-demping: debt-accumulatie, stale-stand-override, piek-override
- EV: house-share-pad, ev_blind bij stale wallbox, lastsprong-guard, EV-guard
- set_battery: laden_overschot-downgrade zonder surplus-modus
- realtime: surplus-peakmemory, discharge-guard, assist start/stop-grace,
  export-recovery
- safety: runaway-trip en stop-grace
"""
import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_DIR = HERE.parent / "custom_components" / "wattson_ems"

# Windows PowerShell gebruikt vaak cp1252; testnamen bevatten λ.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# homeassistant-stubs (alleen wat de geteste modules echt aanraken)
# ---------------------------------------------------------------------------

def _module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


_ha = _module("homeassistant")
_core = _module("homeassistant.core")
_core.HomeAssistant = type("HomeAssistant", (), {})
_core.callback = lambda fn: fn
_ce = _module("homeassistant.config_entries")
_ce.ConfigEntry = type("ConfigEntry", (), {})
_module("homeassistant.helpers")
_storage = _module("homeassistant.helpers.storage")


class FakeStore:
    def __init__(self, *args):
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


_storage.Store = FakeStore
_event = _module("homeassistant.helpers.event")
_event.async_call_later = lambda hass, delay, cb: (lambda: None)
_event.async_track_state_change_event = lambda *a, **k: (lambda: None)
_event.async_track_time_interval = lambda *a, **k: (lambda: None)
_util = _module("homeassistant.util")
_dt = _module("homeassistant.util.dt")
_dt.utcnow = lambda: datetime.now(timezone.utc)
_dt.now = lambda: datetime.now(timezone.utc).astimezone()
_dt.as_local = lambda value: value.astimezone()
_dt.as_utc = lambda value: value.astimezone(timezone.utc)
_util.dt = _dt

# pakket-anker: relatieve imports werken, __init__.py blijft ongeladen
_pkg = types.ModuleType("wattson_ems")
_pkg.__path__ = [str(PKG_DIR)]
sys.modules["wattson_ems"] = _pkg

C = importlib.import_module("wattson_ems.coordinator")
R = importlib.import_module("wattson_ems.realtime")
SAF = importlib.import_module("wattson_ems.safety")
A = importlib.import_module("wattson_ems.adapters")
P = importlib.import_module("wattson_ems.planner")
F = importlib.import_module("wattson_ems.forecast")
K = importlib.import_module("wattson_ems.const")
CTRL = importlib.import_module("wattson_ems.control")


class Clock:
    """Instelbare monotonic-klok voor dwell-, grace- en throttle-gedrag."""

    def __init__(self):
        self.t = 10_000.0

    def monotonic(self):
        return self.t


CLOCK = Clock()
C.time = CLOCK
R.time = CLOCK
SAF.time = CLOCK
# Dezelfde instelbare klok voor meetleeftijd en regeltijden. Losse Windows-
# klokmetingen kunnen identieke timestamps geven en samples laten verdwijnen.
_dt.utcnow = lambda: datetime(2026, 9, 15, tzinfo=timezone.utc) + timedelta(seconds=CLOCK.t)
_dt.now = _dt.utcnow
_dt.as_local = lambda value: value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeState:
    def __init__(self, state, attributes=None, age_s=0.0):
        self.state = state
        self.attributes = attributes or {}
        self.last_updated = _dt.utcnow() - timedelta(seconds=age_s)


class FakeStates(dict):
    def get(self, key, default=None):  # noqa: A003 - HA-contract
        return dict.get(self, key, default)


class FakeBus:
    def async_fire(self, *a, **k):
        pass


class FakeServices:
    async def async_call(self, *a, **k):
        pass


class FakeHass:
    def __init__(self):
        self.states = FakeStates()
        self.bus = FakeBus()
        self.services = FakeServices()
        self.pending = []

    def async_create_task(self, coro):
        self.pending.append(coro)
        return coro

    async def async_add_executor_job(self, func, *args):
        return func(*args)


async def drain(hass):
    while hass.pending:
        await hass.pending.pop(0)


class FakeAdapter:
    """Registreert commando's; gedraagt zich als een braaf apparaat."""

    def __init__(self, c, caps):
        self.c = c
        self.caps = caps
        self.calls = []

    async def apply(self, action, power_w, *, p1_cap=True):
        self.calls.append((action, round(power_w), p1_cap))
        self.c.last_applied = f"{action} ({power_w:+.0f} W, fake)"
        return power_w if action in ("laden", "laden_overschot", "ontladen", "verkopen") else 0.0

    async def emergency_stop(self, richting):
        self.calls.append(("noodstop", richting, True))

    async def adjust_discharge_limit(self, power_w):
        self.calls.append(("ontlaadlimiet", round(power_w), False))
        return power_w

    async def enforce_rest(self):
        pass

    def telemetry_entities(self):
        return ("sensor.bat_chg", "sensor.bat_dis")


CAPS_SURPLUS = A.AdapterCaps(p1_matching=False, device_limits=True, surplus_mode=True,
                             control_latency_s=5.0, min_setpoint_w=50.0, feedback_ack=True)
CAPS_FIXED = A.AdapterCaps(p1_matching=False, device_limits=False, surplus_mode=False,
                           control_latency_s=1.0, min_setpoint_w=50.0)


def make_coordinator(*, caps=CAPS_SURPLUS, wallbox=False):
    hass = FakeHass()
    options = {
        K.CONF_ENT_PRICE: "sensor.prijs",
        K.CONF_ENT_SOC: "sensor.soc",
        K.CONF_ENT_P1: "sensor.p1",
    }
    if wallbox:
        options[K.CONF_ENT_WALLBOX_1] = "sensor.wb1"
    entry = types.SimpleNamespace(options=options)
    c = C.WattsonCoordinator(hass, entry, C.load_params())
    c.adapter_impl = FakeAdapter(c, caps)
    c.caps = caps
    # deterministische toekomst-huislast (het getrainde profiel verandert
    # per hertraining en hoort geen testuitkomsten te sturen)
    c.load_profile = F.LoadProfile({})
    return c


def set_state(c, entity, value, attributes=None, age_s=0.0):
    state = FakeState(value, attributes, age_s)
    previous = c.hass.states.get(entity)
    if age_s == 0 and previous and previous.last_updated >= state.last_updated:
        state.last_updated = previous.last_updated + timedelta(microseconds=1)
    c.hass.states[entity] = state


def set_prices(c, now_price, future_prices):
    """Prijssensor + forecast-attribuut (generiek {start, price}-contract)."""
    now = _dt.utcnow().replace(minute=0, second=0, microsecond=0)
    forecast = [{"start": now.isoformat(), "price": now_price}]
    for i, price in enumerate(future_prices, start=1):
        forecast.append({"start": (now + timedelta(hours=i)).isoformat(),
                         "price": price})
    set_state(c, c.ent_price, now_price, {"forecast": forecast})


def basis(c, *, soc_pct=50.0, p1_w=350.0):
    set_state(c, c.ent_soc, soc_pct)
    set_state(c, c.ent_p1, p1_w)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_plan_basis():
    """Goedkoop uur -> laden; sturing uit blijft schaduw, aan stuurt de adapter.

    SoC laag: met een volle accu dekt de bestaande lading de horizon al en is
    rust de (correcte) DP-uitkomst."""
    c = make_coordinator()
    basis(c, soc_pct=20.0)
    set_prices(c, 0.05, [0.40] * 6)
    run(c._tick(None))
    ok_advies = c.advies == "laden" and c.plan_error is None
    ok_schaduw = c.adapter_impl.calls == []

    c.control_enabled = True
    run(c._tick(None))
    laad_calls = [call for call in c.adapter_impl.calls if call[0] == "laden"]
    return {
        "goedkoop uur geeft laadadvies": ok_advies,
        "schaduwmodus stuurt niets": ok_schaduw,
        "sturing aan stuurt laden naar adapter": bool(laad_calls) and laad_calls[-1][1] > 0,
    }


def test_geen_data():
    c = make_coordinator()
    run(c._tick(None))
    ok_shadow = c.advies == "geen data"

    # Live restart-regressie 17 juli: de Zendure-select herstelde eerder dan de
    # prijsbron, waardoor 1999 W laden kort als runaway werd gezien. Vóór het
    # eerste geldige plan is dit een gecontroleerde startupfase: geen trip,
    # maar wel direct een expliciet rustcommando.
    c2 = make_coordinator()
    c2.control_enabled = True
    set_state(c2, "sensor.bat_chg", 1999.0)
    run(c2._tick(None))
    return {
        "zonder bronnen: advies 'geen data'": ok_shadow,
        "startup zonder plandata veroorzaakt geen valse watchdog-trip":
            c2.safety.tripped is None and c2.safety.watch_error is None,
        "startup zonder plandata stuurt de accu direct naar rust":
            any(call[0] == "rust" for call in c2.adapter_impl.calls),
    }


def test_verkopen_switch():
    """v3: export voorbij de huisvraag alleen met de verkoop-switch aan."""
    c = make_coordinator()
    basis(c, soc_pct=90.0, p1_w=350.0)
    set_prices(c, 0.60, [0.05] * 6)
    run(c._tick(None))
    ok_zonder = c.advies == "ontladen" and abs(c.setpoint_w) <= 450

    c2 = make_coordinator()
    basis(c2, soc_pct=90.0, p1_w=350.0)
    set_prices(c2, 0.60, [0.05] * 6)
    c2.sell_enabled = True
    run(c2._tick(None))
    ok_met = c2.advies == "verkopen" and abs(c2.setpoint_w) > 450
    return {
        "zonder verkoop-switch begrensd op huisvraag": ok_zonder,
        "met verkoop-switch exporteert het plan": ok_met,
    }


def test_wissel_demping():
    """Marginale wissel wordt vastgehouden; de gemiste marge telt op tot de
    drempel en dan volgt de wissel alsnog."""
    c = make_coordinator()
    basis(c, soc_pct=10.0)  # lege accu: het laadvolume (en dus de marge) is klein
    set_prices(c, 0.19, [0.19, 0.19])
    run(c._tick(None))
    ok_rust = c.advies == "rust"

    # kleine winstkans: laden nu is nipt voordelig, ver onder de drempel
    # (gekalibreerd: ~€0,006 gemiste marge per tick bij deze prijzen)
    set_prices(c, 0.10, [0.19, 0.19])
    run(c._tick(None))
    debt = c._switch_debt
    ok_hold = (c.advies == "rust" and 0.0 < debt < K.SWITCH_DEADBAND_EUR
               and "houdt rust vast" in c.reden)

    ticks = 0
    while c.advies == "rust" and ticks < 20:
        run(c._tick(None))
        ticks += 1
    ok_flip = c.advies == "laden" and 1 <= ticks < 20
    return {
        "vlakke prijzen geven rust": ok_rust,
        "marginale wissel vastgehouden met debt in band": ok_hold,
        "debt-accumulatie laat de wissel alsnog door": ok_flip,
    }


def test_demping_piek_override():
    """Een grote spread gaat direct door de demping heen."""
    c = make_coordinator()
    basis(c, soc_pct=20.0)
    set_prices(c, 0.30, [0.30] * 6)
    run(c._tick(None))
    ok_rust = c.advies == "rust"
    set_prices(c, 0.05, [0.60] * 6)
    run(c._tick(None))
    return {
        "vlak begin geeft rust": ok_rust,
        "grote spread wisselt direct": c.advies == "laden",
    }


def test_zongedekte_start_volgt_euro_deadband():
    """Ook zonladen gebruikt de huidige economische wisseldemping.

    Een zonnige laadstart met verwaarloosbaar voordeel hoeft niet onmiddellijk
    het relais te schakelen; voldoende werkelijk planvoordeel gaat wel door.
    """
    c = make_coordinator()
    previous = CTRL.Decision(CTRL.AdviceMode.IDLE)
    c.set_decision(CTRL.Decision(
        CTRL.AdviceMode.CHARGE, 1500.0, "goedkoopste zonne-uur"
    ))
    steps = [
        P.Step(0.24967, 0.24967, 508.0, 2740.0),
        P.Step(0.2499725, 0.2499725, 1071.0, 3520.0),
    ]
    context = types.SimpleNamespace(
        steps=steps,
        ev_now=False,
        soc_kwh=0.69,
        terminal_value=0.228,
    )
    idle_cost, soc1, _, _ = P.hour_result(steps[0], 0, context.soc_kwh, c.params)
    _, rest = P.plan(steps[1:], soc1, c.params, context.terminal_value)
    evaluation = types.SimpleNamespace(setpoints=[1500.0, 0.0], cost=idle_cost + rest - 0.001)
    run(c._stabilize_decision(previous, context, evaluation))
    held = c.mode is CTRL.AdviceMode.IDLE and 0 < c._switch_debt < K.SWITCH_DEADBAND_EUR
    c.set_decision(CTRL.Decision(CTRL.AdviceMode.CHARGE, 1500))
    evaluation.cost = idle_cost + rest - K.DWELL_OVERRIDE_EUR - K.SWITCH_DEADBAND_EUR
    run(c._stabilize_decision(previous, context, evaluation))
    return {
        "marginale zonnestart wacht op voldoende voordeel": held,
        "zonnestart met voldoende voordeel gaat direct door":
            c.mode is CTRL.AdviceMode.CHARGE
            and c.setpoint_w == 1500.0
            and c._switch_debt == 0.0,
    }


def test_demping_stale_stand():
    """Een vastgehouden actieve stand die fysiek niets meer kan (SoC-bodem)
    mag nooit door de euro-demping blijven hangen."""
    c = make_coordinator()
    basis(c, soc_pct=10.0)  # op de bodem: ontladen kan fysiek niet meer
    set_prices(c, 0.60, [0.05] * 6)
    c.advies = "ontladen"
    c.setpoint_w = -700.0
    run(c._tick(None))
    return {"stale ontlaadstand wisselt direct naar rust":
            c.advies == "rust" and c._switch_debt == 0.0}


def test_ev_house_share():
    """EV laadt met verse wallbox-telemetrie: accu dekt exact het huisdeel
    via het vaste verkopen-pad (omzeilt de P1-cap)."""
    c = make_coordinator(wallbox=True)
    c.control_enabled = True
    basis(c, soc_pct=60.0, p1_w=7350.0)
    set_state(c, "sensor.wb1", 7000.0)  # vers
    set_prices(c, 0.60, [0.05] * 6)
    run(c._tick(None))
    calls = c.adapter_impl.calls
    share = [call for call in calls if call[0] == "verkopen"]
    return {
        "house-share stuurt vast verkopen-commando": bool(share),
        "house-share-vermogen is het huisdeel (350 W)": share and share[-1][1] == 350,
        "house-share-vlag gezet (EV-guard laat hem met rust)": c.ev.house_share_active,
        "setpoint toont het huisdeel": c.setpoint_w == -350,
    }


def test_ev_blind_zonder_verse_wallbox():
    """Wallbox-telemetrie niet vers: planner blokkeert ontladen (ev_blind)."""
    c = make_coordinator(wallbox=True)
    c.control_enabled = True
    basis(c, soc_pct=60.0, p1_w=7350.0)
    set_state(c, "sensor.wb1", 7000.0, age_s=400)  # stale
    set_prices(c, 0.60, [0.05] * 6)
    run(c._tick(None))
    rust = [call for call in c.adapter_impl.calls if call[0] == "rust"]
    return {
        "stale wallbox: geen ontladen tijdens EV": c.advies == "rust",
        "accu expliciet naar rust gestuurd": bool(rust),
    }


def test_lastsprong_guard():
    """Huisvraag springt hard omhoog zonder wallbox-bevestiging: één tick rust."""
    c = make_coordinator()
    c.control_enabled = True
    basis(c, soc_pct=60.0, p1_w=600.0)
    set_prices(c, 0.60, [0.05] * 6)
    run(c._tick(None))
    ok_eerst = c.advies == "ontladen"
    # De huidige regelaar filtert losse uitschieters; het oude 600 W-sample
    # moet uit het bronvenster zijn voordat dit als echte lastsprong telt.
    CLOCK.t += K.ASSIST_START_CONFIRM_S + 1
    set_state(c, c.ent_p1, 4800.0)
    run(c._tick(None))
    return {
        "eerste tick ontlaadt normaal": ok_eerst,
        "verdachte lastsprong parkeert op rust (EV-check)": c.advies == "rust (EV-check)",
        "setpoint naar nul tijdens de EV-check": c.setpoint_w == 0.0,
    }


def test_ev_guard_direct():
    """EV begint te laden: ontladen stopt direct, behalve bij bewust huisdeel."""
    c = make_coordinator(wallbox=True)
    c.control_enabled = True
    set_state(c, "sensor.wb1", 7000.0)
    c.advies = "ontladen"
    c.ev.guard(c, None)
    run(drain(c.hass))
    ok_stop = (c.advies == "rust (EV-guard)"
               and any(call[0] == "rust" for call in c.adapter_impl.calls))

    c2 = make_coordinator(wallbox=True)
    c2.control_enabled = True
    set_state(c2, "sensor.wb1", 7000.0)
    c2.advies = "ontladen"
    c2.ev.house_share_active = True
    c2.ev.guard(c2, None)
    run(drain(c2.hass))
    return {
        "EV-guard stopt ontladen direct": ok_stop,
        "house-share overleeft de EV-guard": c2.advies == "ontladen"
                                             and c2.adapter_impl.calls == [],
    }


def test_overschot_downgrade():
    """Zonder native surplus-modus wordt laden_overschot een vast laden-
    commando en klopt de boekhouding met wat er echt is gestuurd."""
    c = make_coordinator(caps=CAPS_FIXED)
    c.control_enabled = True
    run(c.set_battery("laden_overschot", 500.0))
    call = c.adapter_impl.calls[-1]
    return {
        "downgrade naar vast laden op de adapter": call[0] == "laden",
        "boekhouding volgt de effectieve actie": c._last_action == "laden",
        "laadvermogen geregistreerd": c._last_charge_w == 500.0,
    }


def test_surplus_peakmemory():
    """surplus_carried onthoudt de piek over het venster (anti-flapping)."""
    c = make_coordinator()
    c.track.note_applied("laden_overschot", 800.0)
    ok_seed = c.track.surplus_carried(800.0)
    ok_hoger = not c.track.surplus_carried(1200.0)
    CLOCK.t += K.SURPLUS_DEMOTE_WINDOW_S + 1
    return {
        "vers gepromoveerd geldt als gedragen": ok_seed,
        "boven piek + marge niet gedragen": ok_hoger,
        "buiten het venster vervalt het geheugen": not c.track.surplus_carried(800.0),
    }


def test_discharge_guard():
    """De huidige volglus verlaagt pas na latentie en aanhoudende export."""
    c = make_coordinator(caps=CAPS_FIXED)
    c.control_enabled = True
    c.advies = "ontladen"
    run(c.set_battery("ontladen", 800.0, p1_cap=False))
    c.adapter_impl.calls.clear()
    set_state(c, c.ent_p1, -200.0)
    c.discharge_ctl.on_p1(None)
    before_latency = not c.hass.pending
    CLOCK.t += K.DIS_LOOP_LATENCY_S
    c.discharge_ctl.on_p1(None)
    before_window = not c.hass.pending
    CLOCK.t += K.DIS_LOOP_WINDOW_S
    set_state(c, c.ent_p1, -200.0)
    c.discharge_ctl.on_p1(None)
    run(drain(c.hass))
    calls = [call for call in c.adapter_impl.calls if call[0] == "ontladen"]
    return {
        "volglus wacht op latentie en bewijsvenster": before_latency and before_window,
        "volglus verlaagt naar setpoint + aanhoudende P1": calls and calls[-1][1] == 600,
        "guard slaat de P1-cap over (delta is al berekend)":
            calls and calls[-1][2] is False,
    }


def test_assist_start_en_stopgrace():
    """Assist start op een piek als dekken meer waard is dan bewaren, en
    stopt pas na opwarmvenster + stop-grace."""
    c = make_coordinator()
    c.control_enabled = True
    c.assist_enabled = True
    basis(c, soc_pct=60.0, p1_w=350.0)
    set_prices(c, 0.30, [0.30] * 6)
    run(c._tick(None))  # vult values/λ; vlakke prijzen -> advies rust
    ok_rust = c.advies == "rust"

    set_state(c, c.ent_price, 0.60, {"forecast": []})  # actuele prijs boven de vloer
    CLOCK.t += K.ASSIST_START_CONFIRM_S + 1
    set_state(c, c.ent_p1, 500.0)
    c.source.sample()
    run(c.assist.apply())
    ok_single = c.assist_active is None
    CLOCK.t += K.ASSIST_START_CONFIRM_S
    set_state(c, c.ent_p1, 500.0)
    run(c.assist.apply())
    ok_start = (c.assist_active == "ontladen"
                and c.advies == "bijspringen: ontladen"
                and any(call[0] == "ontladen" for call in c.adapter_impl.calls))

    # piek "voorbij" binnen het opwarmvenster is geen stopbewijs
    # (bron-P1 = p1 + gemeten ontladen = 20 W, onder ASSIST_STOP_W)
    set_state(c, c.ent_p1, 10.0)
    set_state(c, "sensor.bat_dis", 10.0)
    CLOCK.t += K.ASSIST_THROTTLE_S + 1
    run(c.assist.apply())
    ok_warmup = c.assist_active == "ontladen"

    # voorbij opwarmvenster: einde gemeten, maar de stop-grace moet nog lopen
    CLOCK.t += K.ASSIST_MIN_RUN_S
    set_state(c, c.ent_p1, 10.0)
    set_state(c, "sensor.bat_dis", 10.0)
    run(c.assist.apply())
    ok_grace = c.assist_active == "ontladen" and c.assist.end_since is not None

    CLOCK.t += K.ASSIST_STOP_GRACE_S + 1
    set_state(c, c.ent_p1, 10.0)
    set_state(c, "sensor.bat_dis", 10.0)
    run(c.assist.apply())
    ok_stop = (c.assist_active is None and c.advies == "rust"
               and c.adapter_impl.calls[-1][0] == "rust")
    return {
        "vlakke prijzen: plan blijft rust": ok_rust,
        "losse piekmeting start geen bijspringen": ok_single,
        "piek boven de λ-vloer start bijspringen": ok_start,
        "opwarmvenster negeert vals piek-einde": ok_warmup,
        "stop-grace houdt de assist nog vast": ok_grace,
        "na de grace stopt de assist netjes": ok_stop,
    }


def test_export_recovery():
    """Aanhoudende bevestigde bronexport breekt vastgelopen ontladen af."""
    c = make_coordinator()
    c.control_enabled = True
    basis(c, soc_pct=60.0, p1_w=350.0)
    set_prices(c, 0.60, [0.60] * 6)
    run(c._tick(None))  # λ hoog: opslaan van de export is niet economisch
    c.advies = "ontladen"
    c._last_action = "ontladen"
    c._last_discharge_w = K.DIS_LOOP_FLOOR_W
    c.adapter_impl.calls = []
    set_state(c, c.ent_p1, -400.0)
    set_state(c, "sensor.bat_dis", K.DIS_LOOP_FLOOR_W)
    c.export_recovery.check(None)
    ok_timer = c.export_recovery.since is not None and not c.export_recovery.pending
    CLOCK.t += K.DISCHARGE_EXPORT_ABORT_HOLD_S + 1
    c.export_recovery.check(None)
    run(drain(c.hass))
    return {
        "exporttimer start zonder direct te vuren": ok_timer,
        "bevestigde export breekt ontladen af": any(
            call[0] == "rust" for call in c.adapter_impl.calls),
        "reden meldt het afbreken": "afgebroken" in c.reden,
    }


def test_watchdog():
    """Ongecommandeerd ontladen tript; een eigen stop krijgt eerst grace."""
    c = make_coordinator()
    c.control_enabled = True
    c.advies = "rust"
    set_state(c, "sensor.bat_dis", 900.0)
    CLOCK.t += K.STARTUP_GRACE_S + 1
    run(c.safety.watchdog())
    ok_trip = (c.safety.tripped == "ontladen"
               and any(call[0] == "noodstop" for call in c.adapter_impl.calls))

    c2 = make_coordinator()
    c2.control_enabled = True
    c2.advies = "rust"
    set_state(c2, "sensor.bat_dis", 900.0)
    CLOCK.t += K.STARTUP_GRACE_S + 1
    c2.safety.note_own_stop("ontladen")
    run(c2.safety.watchdog())
    ok_grace = c2.safety.tripped is None
    CLOCK.t += K.WATCH_STOP_GRACE_S + 1
    run(c2.safety.watchdog())
    return {
        "runaway zonder opdracht tript de watchdog": ok_trip,
        "uitloop na eigen stop krijgt grace": ok_grace,
        "na de grace geldt de bewaking weer": c2.safety.tripped == "ontladen",
    }


def test_typed_decision():
    """UI-labels blijven compatibel, maar machinestatus is een enum."""
    c = make_coordinator()
    c.set_decision(CTRL.Decision(
        CTRL.AdviceMode.ASSIST_DISCHARGE,
        -450.0,
        "getypeerde test",
    ))
    typed = (c.mode is CTRL.AdviceMode.ASSIST_DISCHARGE
             and c.mode.expects_discharge)
    ui = (c.advies == "bijspringen: ontladen"
          and c.setpoint_w == -450.0
          and c.reden == "getypeerde test")
    c.advies = "rust"  # bestaand extern/testcontract
    return {
        "beslisstatus is getypeerd": typed,
        "UI-contract behoudt bestaande labels": ui,
        "compat-setter vertaalt naar enum": c.mode is CTRL.AdviceMode.IDLE,
    }


def test_command_arbitrage():
    """Writes overlappen nooit; safety maakt een wachtend commando ongeldig."""

    class BlockingAdapter(FakeAdapter):
        def __init__(self, c, caps):
            super().__init__(c, caps)
            self.active = 0
            self.max_active = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.block_first = True

        async def apply(self, action, power_w, *, p1_cap=True):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                if self.block_first:
                    self.block_first = False
                    self.started.set()
                    await self.release.wait()
                await asyncio.sleep(0)
                return await super().apply(action, power_w, p1_cap=p1_cap)
            finally:
                self.active -= 1

    async def scenario():
        c = make_coordinator()
        c.control_enabled = True
        adapter = BlockingAdapter(c, CAPS_FIXED)
        c.adapter_impl = adapter
        first = asyncio.create_task(c.set_battery("laden", 500.0))
        await adapter.started.wait()
        stale = asyncio.create_task(c.set_battery(
            "ontladen", 400.0, source=CTRL.CommandSource.REALTIME))
        await asyncio.sleep(0)
        c.command_arbiter.invalidate_pending()
        stop = asyncio.create_task(c.set_battery(
            "rust", 0.0, source=CTRL.CommandSource.SAFETY))
        adapter.release.set()
        first_result, stale_result, stop_result = await asyncio.gather(
            first, stale, stop)
        actions = [call[0] for call in adapter.calls]
        return {
            "adapterwrites zijn strikt geserialiseerd": adapter.max_active == 1,
            "wachtend realtimecommando is geïnvalideerd": stale_result is None,
            "safety-rust volgt als eerstvolgende geldige opdracht":
                actions == ["laden", "rust"] and stop_result == 0.0,
            "boekhouding eindigt in veilige rust":
                first_result == 500.0 and c._last_action == "rust",
        }

    return run(scenario())


def test_tick_serialisatie():
    """Gelijktijdige plantriggers overlappen niet en worden samengevoegd."""

    async def scenario():
        c = make_coordinator()
        started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum = 0
        calls = 0

        async def fake_plan():
            nonlocal active, maximum, calls
            calls += 1
            active += 1
            maximum = max(maximum, active)
            try:
                c.set_decision(CTRL.Decision(CTRL.AdviceMode.IDLE))
                if calls == 1:
                    started.set()
                    await release.wait()
            finally:
                active -= 1

        c._plan_and_apply = fake_plan
        first = asyncio.create_task(c._tick(None))
        await started.wait()
        second = asyncio.create_task(c._tick(None))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        return {
            "planticks overlappen niet": maximum == 1,
            "trigger tijdens plantick geeft precies één rerun": calls == 2,
            "planlock komt weer vrij": not c._tick_lock.locked(),
        }

    return run(scenario())


def test_lifecycle_listeners():
    """Start registreert boekhouding en regellussen; stop meldt alles af."""

    async def scenario():
        c = make_coordinator()
        c.control_enabled = True
        intervals = []
        states = []
        cancelled = []

        def cancel(kind):
            return lambda: cancelled.append(kind)

        old_interval = C.async_track_time_interval
        old_state = C.async_track_state_change_event
        old_later = C.async_call_later
        C.async_track_time_interval = lambda hass, cb, delta: (
            intervals.append(round(delta.total_seconds())) or cancel("interval"))
        C.async_track_state_change_event = lambda hass, entities, cb: (
            states.append(tuple(entities)) or cancel("state"))
        C.async_call_later = lambda hass, delay, cb: cancel("retry")
        try:
            await c.async_start()
            listener_count = len(c.listeners)
            await c.async_stop()
        finally:
            C.async_track_time_interval = old_interval
            C.async_track_state_change_event = old_state
            C.async_call_later = old_later
        return {
            "lifecycle registreert plan/safety/track-intervallen":
                intervals == [60, K.UPDATE_MINUTES * 60,
                              K.WATCH_INTERVAL_S,
                              K.TRACK_INTERVAL_S],
            "lifecycle registreert bronlisteners": bool(states),
            "stop meldt alle listeners af":
                len([x for x in cancelled if x != "retry"]) == listener_count,
            "stop stuurt de accu naar rust":
                any(call[0] == "rust" for call in c.adapter_impl.calls),
        }

    return run(scenario())


def test_aflopend_commando_verversen():
    """Marstek-passive-achtige adapters: aflopend commando wordt via de
    arbiter herhaald zolang sturing aan staat; na stop/trip verloopt het."""

    class ExpiringAdapter(FakeAdapter):
        def __init__(self, c, caps):
            super().__init__(c, caps)
            self.expiring = False

        def needs_refresh(self):
            return self.expiring

    async def scenario():
        c = make_coordinator(caps=CAPS_FIXED)
        adapter = ExpiringAdapter(c, CAPS_FIXED)
        c.adapter_impl = adapter
        c.control_enabled = True
        await c.set_battery("ontladen", 300.0, p1_cap=False)
        adapter.calls.clear()

        await c._refresh_expiring_command()
        geen_refresh_vers = adapter.calls == []

        adapter.expiring = True
        await c._refresh_expiring_command()
        herhaald = adapter.calls == [("ontladen", 300, False)]
        adapter.calls.clear()

        # rust wordt ook ververst: de accu moet stil blijven zolang het plan dat wil
        await c.set_battery("rust", 0.0)
        adapter.calls.clear()
        await c._refresh_expiring_command()
        rust_herhaald = adapter.calls == [("rust", 0, False)]
        adapter.calls.clear()

        # sturing uit -> niets herhalen; het commando verloopt op het apparaat
        c.control_enabled = False
        await c._refresh_expiring_command()
        uit_niets = adapter.calls == []

        # na unload ook niet, zelfs met sturing 'aan' in de oude boekhouding
        c.control_enabled = True
        c._stopped = True
        await c._refresh_expiring_command()
        gestopt_niets = adapter.calls == []

        # trip: ontladen wordt niet herhaald (stuurpoort dicht), rust wel toegestaan
        c._stopped = False
        c.safety.tripped = "ontladen"
        c._last_action = CTRL.BatteryAction.DISCHARGE
        c._last_discharge_w = 300.0
        await c._refresh_expiring_command()
        trip_niets = adapter.calls == []
        return {
            "vers commando wordt niet herhaald": geen_refresh_vers,
            "aflopend ontladen wordt via de arbiter herhaald": herhaald,
            "aflopende rust wordt herhaald": rust_herhaald,
            "sturing uit: commando verloopt op het apparaat": uit_niets,
            "na unload wordt niets meer herhaald": gestopt_niets,
            "na trip wordt actief sturen niet herhaald": trip_niets,
        }

    return run(scenario())


def main():
    suites = [
        test_plan_basis, test_geen_data, test_verkopen_switch,
        test_wissel_demping, test_demping_piek_override,
        test_zongedekte_start_volgt_euro_deadband,
        test_demping_stale_stand,
        test_ev_house_share, test_ev_blind_zonder_verse_wallbox,
        test_lastsprong_guard, test_ev_guard_direct, test_overschot_downgrade,
        test_surplus_peakmemory, test_discharge_guard,
        test_assist_start_en_stopgrace, test_export_recovery, test_watchdog,
        test_typed_decision, test_command_arbitrage, test_tick_serialisatie,
        test_lifecycle_listeners, test_aflopend_commando_verversen,
    ]
    failed = []
    total = 0
    for suite in suites:
        for name, ok in suite().items():
            total += 1
            print(("PASS " if ok else "FAIL ") + name)
            if not ok:
                failed.append(name)
    print(f"\n{total - len(failed)}/{total} PASS")
    if failed:
        raise AssertionError(", ".join(failed))


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        sys.exit(1)
