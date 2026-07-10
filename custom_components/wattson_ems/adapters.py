"""Accumerk-adapters: vertalen Wattson-acties naar apparaat-commando's.

Architectuur:
- De coordinator (planner, watchdog, guards) is merk-onafhankelijk en praat
  uitsluitend via `BatteryAdapter` met het apparaat.
- Elke adapter declareert zijn `AdapterCaps`; veiligheids- en stuurgedrag
  in de coordinator stuurt op die capabilities, nooit op de merknaam.
- Deze module importeert bewust NIETS uit homeassistant (en niets relatiefs),
  zodat de contract-tests (tests/contract_tests.py) haar standalone kunnen
  laden en met een fake hass/coordinator het echte gedrag verifiëren.

Een nieuw accumerk toevoegen = één subclass met entity-mapping + caps,
registreren in `create_adapter`, en de contract-suite groen draaien.
"""
import logging

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# entity-IO: lezen/schrijven met eenheid-normalisatie (W/kW/MW)
# ---------------------------------------------------------------------------

def unit_of(hass, entity: str) -> str:
    """Eenheid van een entity, genormaliseerd voor eenvoudige conversies."""
    st = hass.states.get(entity) if entity else None
    return str(st.attributes.get("unit_of_measurement", "")).strip().lower() if st else ""


def read_f(hass, entity: str):
    """Lees een entity als float, of None (leeg/onbekend/unavailable)."""
    if not entity:
        return None
    st = hass.states.get(entity)
    if st is None or st.state in ("unknown", "unavailable", None):
        return None
    try:
        return float(st.state)
    except ValueError:
        return None


def _to_watt(value: float, unit: str) -> float:
    if unit == "kw":
        return value * 1000.0
    if unit == "mw":
        return value * 1_000_000.0
    return value


def read_power_w(hass, entity: str):
    """Lees een vermogensentity als watt; accepteert W, kW en MW."""
    value = read_f(hass, entity)
    return None if value is None else _to_watt(value, unit_of(hass, entity))


def read_fresh(hass, entity: str, max_age_s: float, now_utc):
    """Als read_f, maar alleen als de waarde recent is bijgewerkt.

    Een bevroren of unavailable sensor levert None: daarop mag geen
    noodstop worden gebaseerd, maar ook geen opheffing ervan.
    """
    if not entity:
        return None
    st = hass.states.get(entity)
    if st is None or st.state in ("unknown", "unavailable", None):
        return None
    age = (now_utc - st.last_updated).total_seconds()
    if age > max_age_s:
        return None
    try:
        return float(st.state)
    except ValueError:
        return None


def read_fresh_power_w(hass, entity: str, max_age_s: float, now_utc):
    """Als read_power_w, maar alleen voor verse telemetrie."""
    value = read_fresh(hass, entity, max_age_s, now_utc)
    return None if value is None else _to_watt(value, unit_of(hass, entity))


async def set_number(hass, entity: str, value) -> None:
    """Zet een number-entity, geclampt op haar eigen min/max.

    Het apparaat bepaalt zijn eigen grenzen (bv. output_limit max 1400 W);
    een waarde daarbuiten laat de hele service-call — en daarmee de tick —
    falen.
    """
    if not entity:
        return
    st = hass.states.get(entity)
    if st is not None:
        try:
            hi = st.attributes.get("max")
            lo = st.attributes.get("min")
            if hi is not None:
                value = min(float(value), float(hi))
            if lo is not None:
                value = max(float(value), float(lo))
        except (TypeError, ValueError):
            pass
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity, "value": value}, blocking=True)


async def set_power_number(hass, entity: str, watts: float) -> None:
    """Zet een number-vermogensentity, ongeacht of die W, kW of MW gebruikt."""
    unit = unit_of(hass, entity)
    native = watts
    if unit == "kw":
        native = watts / 1000.0
    elif unit == "mw":
        native = watts / 1_000_000.0
    await set_number(hass, entity, native)


# ---------------------------------------------------------------------------
# capabilities + adapter-interface
# ---------------------------------------------------------------------------

class AdapterCaps:
    """Wat kan dit accumerk? Veiligheids- en stuurgedrag stuurt hierop.

    p1_matching       apparaat volgt de huisvraag zelf (kan niet exporteren);
                      False = vast setpoint -> discharge-guard nodig
    device_limits     aparte in/uit-limiet-entiteiten op apparaatniveau
                      (noodstop kan de foute richting fysiek dichtzetten)
    surplus_mode      native PV-overschot-laadmodus (volgt P1 zelf)
    control_latency_s indicatie hoe snel een commando effect heeft
    min_setpoint_w    kleinste zinvolle setpoint van het apparaat
    """

    __slots__ = ("p1_matching", "device_limits", "surplus_mode",
                 "control_latency_s", "min_setpoint_w")

    def __init__(self, *, p1_matching, device_limits, surplus_mode,
                 control_latency_s, min_setpoint_w):
        self.p1_matching = p1_matching
        self.device_limits = device_limits
        self.surplus_mode = surplus_mode
        self.control_latency_s = control_latency_s
        self.min_setpoint_w = min_setpoint_w


class BatteryAdapter:
    """Interface. `c` is de coordinator (levert hass, params, opties, _tripped).

    Contract (zie tests/contract_tests.py):
    - apply() vertaalt laden/ontladen/verkopen/rust naar apparaat-commando's,
      begrenst op P1/huisvraag waar de caps dat eisen, en geeft het werkelijk
      gecommandeerde vermogen (W, >=0) terug;
    - emergency_stop() doet wat er BOVENOP 'rust' nodig is (bv. limieten
      dicht); de coordinator commandeert zelf altijd eerst rust;
    - enforce_rest() dwingt rust af op apparaatniveau als verse telemetrie
      bewijst dat de accu toch actief blijft;
    - telemetry_entities() -> (laad-, ontlaad-)sensor van het accuvermogen.
    """

    name = "?"
    caps: AdapterCaps

    def __init__(self, coordinator):
        self.c = coordinator

    async def apply(self, action: str, power_w: float, *, p1_cap: bool = True) -> float:
        raise NotImplementedError

    async def emergency_stop(self, richting) -> None:
        return None

    async def enforce_rest(self) -> None:
        return None

    def telemetry_entities(self):
        return (self.c.ent_bat_chg, self.c.ent_bat_dis)

    # gedeelde begrenzing: ontladen nooit boven de actuele netto-import
    def _p1_capped(self, power_w: float) -> float:
        p1 = read_power_w(self.c.hass, self.c.ent_p1)
        return min(power_w, max(p1 or 0.0, 0.0))


class ZendureAdapter(BatteryAdapter):
    """Zendure SolarFlow via de Zendure-HA-integratie.

    Ontladen gebeurt met de P1-matching van het apparaat zelf (kan niet
    exporteren); de output-limiet wordt begrensd op het geplande setpoint.
    Noodstops zetten de limiet van de foute richting op apparaatniveau dicht.
    """

    name = "zendure"
    caps = AdapterCaps(p1_matching=True, device_limits=True, surplus_mode=True,
                       control_latency_s=5.0, min_setpoint_w=100.0)

    def telemetry_entities(self):
        return (self.c.ent_zd_chg, self.c.ent_zd_dis)

    async def apply(self, action, power_w, *, p1_cap=True):
        if action == "laden_overschot":
            # MATCHING_CHARGE: het apparaat volgt het overschot zelf op P1
            await self._set_mode("smart_charging", 0.0)
            return power_w
        if action == "laden":
            await self._set_mode("manual", -power_w)
            return power_w
        if action == "verkopen":
            # vast ontlaadvermogen; wat het huis niet opneemt gaat het net op
            p = min(power_w, self.c.params.p_discharge_max_w)
            await self._set_mode("manual", p)
            return p
        if action == "ontladen":
            # smart_discharging = P1-matching: volgt de huisvraag; de
            # output-limiet mag niet verder open dan het plan wil
            await self._set_mode("smart_discharging", 0.0, dis_limit_w=power_w)
            return power_w
        await self._set_mode("off", 0.0)
        # sluiplek dicht: met een open output-limiet blijft het apparaat in
        # 'off' ~50 W aan het huis leveren (±1,2 kWh/dag). De input-limiet
        # blijft open zodat PV-opslag via de smart-modes mogelijk blijft;
        # de eerstvolgende ontlaad-actie opent de output-limiet weer zelf.
        await set_power_number(self.c.hass, self.c.ent_zd_outlim, 0)
        await self.enforce_rest()
        return 0.0

    async def emergency_stop(self, richting):
        # limiet van de foute richting dicht (of allebei bij onbekend);
        # apparaat-commando: komt ook aan als de select al 'off' toont
        if richting in (None, "laden"):
            await set_power_number(self.c.hass, self.c.ent_zd_inlim, 0)
        if richting in (None, "ontladen"):
            await set_power_number(self.c.hass, self.c.ent_zd_outlim, 0)

    async def enforce_rest(self):
        """Meet verse activiteit terwijl rust gecommandeerd is -> limieten 0."""
        chg = self.c._fresh_power_w(self.c.ent_zd_chg)
        dis = self.c._fresh_power_w(self.c.ent_zd_dis)
        if (chg is not None and chg > 100) or (dis is not None and dis > 100):
            _LOGGER.warning(
                "Wattson: rust gecommandeerd maar accu is actief (laden %s W / ontladen %s W) — limieten naar 0",
                "?" if chg is None else f"{chg:.0f}", "?" if dis is None else f"{dis:.0f}")
            await set_power_number(self.c.hass, self.c.ent_zd_inlim, 0)
            await set_power_number(self.c.hass, self.c.ent_zd_outlim, 0)

    async def _set_mode(self, mode, manual_w, dis_limit_w=None):
        c = self.c
        # zorg dat de apparaatlimieten open staan voor de gevraagde richting
        # (een eerdere noodstop-0 blijft anders de sturing stil blokkeren);
        # na een noodstop opent alleen de niet-getripte richting
        if mode in ("manual", "smart_charging", "store_solar") and c._tripped != "laden":
            if manual_w <= 0:  # manual laden of matching-laden
                await set_power_number(c.hass, c.ent_zd_inlim, c.params.p_charge_max_w)
        if mode in ("smart_discharging", "smart") or (mode == "manual" and manual_w > 0):
            if c._tripped != "ontladen":
                cap = c.params.p_discharge_max_w
                if dis_limit_w is not None and dis_limit_w > 0:
                    cap = min(max(dis_limit_w, self.caps.min_setpoint_w), cap)
                await set_power_number(c.hass, c.ent_zd_outlim, cap)
        cur = c.hass.states.get(c.ent_zd_operation)
        if mode == "manual":
            await set_power_number(c.hass, c.ent_zd_manual, manual_w)
        if cur is None or cur.state != mode:
            await c.hass.services.async_call(
                "select", "select_option",
                {"entity_id": c.ent_zd_operation, "option": mode}, blocking=True)
        c.last_applied = f"{mode} ({manual_w:+.0f} W)" if mode == "manual" else mode


class MarstekAdapter(BatteryAdapter):
    """Marstek Venus (ESP32/RS485-modbus): force-mode + forcible powers.

    De mode-entity is een select (opties met 'stop/charge/discharge' of
    NL-labels, zoals de LilyGO-ESPHome-config) of een number (register 42010:
    0=stop, 1=charge, 2=discharge, zoals de HA-modbus-config).
    """

    name = "marstek"
    caps = AdapterCaps(p1_matching=False, device_limits=False, surplus_mode=False,
                       control_latency_s=1.0, min_setpoint_w=50.0)

    async def apply(self, action, power_w, *, p1_cap=True):
        c = self.c
        if action == "ontladen" and p1_cap:
            power_w = self._p1_capped(power_w)
        if action == "verkopen":
            # verkopen = ontladen zonder P1-cap
            power_w = min(power_w, c.params.p_discharge_max_w)
        # eerst het vermogen zetten, dan de mode (volgorde die het apparaat verwacht)
        if action == "laden" and c.ent_ms_charge:
            await set_power_number(c.hass, c.ent_ms_charge, power_w)
        if action in ("ontladen", "verkopen") and c.ent_ms_discharge:
            await set_power_number(c.hass, c.ent_ms_discharge, power_w)
        mode_idx = {"laden": 1, "ontladen": 2, "verkopen": 2}.get(action, 0)
        if c.ent_ms_mode.startswith("select."):
            st = c.hass.states.get(c.ent_ms_mode)
            options = (st.attributes.get("options") if st else None) or []
            want = {
                0: ("stop", "none", "off", "idle", "uit"),
                1: ("charge", "charging", "laden"),
                2: ("discharge", "discharging", "ontladen"),
            }[mode_idx]

            # 'discharge' bevat 'charge': een laadoptie mag daarom nooit een
            # ontlaadlabel matchen. Nederlands en Engels worden ondersteund.
            def matches(option_value: str) -> bool:
                label = option_value.lower()
                if mode_idx == 1 and ("discharg" in label or "ontlad" in label):
                    return False
                return any(token == label or token in label for token in want)

            option = next((candidate for candidate in options if matches(candidate)), None)
            if option is None:
                raise RuntimeError(f"geen passende optie voor '{action}' in {options}")
            await c.hass.services.async_call(
                "select", "select_option",
                {"entity_id": c.ent_ms_mode, "option": option}, blocking=True)
        else:
            await c.hass.services.async_call(
                "number", "set_value",
                {"entity_id": c.ent_ms_mode, "value": mode_idx}, blocking=True)
        c.last_applied = f"{action} ({power_w:.0f} W, marstek)"
        return power_w if action in ("ontladen", "verkopen", "laden") else 0.0


class GenericAdapter(BatteryAdapter):
    """Elk merk met number-bediening: één signed vermogen-number, of losse
    laad-/ontlaad-numbers. Ontladen wordt begrensd op de actuele netto-import
    (de discharge-guard verlaagt daarna realtime mee); verkopen is expliciet
    onbegrensd tot het maximum."""

    name = "generic"
    caps = AdapterCaps(p1_matching=False, device_limits=False, surplus_mode=False,
                       control_latency_s=2.0, min_setpoint_w=0.0)

    async def apply(self, action, power_w, *, p1_cap=True):
        c = self.c
        if action == "ontladen" and p1_cap:
            power_w = self._p1_capped(power_w)
        if action == "verkopen":
            power_w = min(power_w, c.params.p_discharge_max_w)
        signed = (
            power_w if action == "laden"
            else (-power_w if action in ("ontladen", "verkopen") else 0.0)
        )
        if c.ent_gen_power:
            await set_power_number(c.hass, c.ent_gen_power, signed)
        else:
            if c.ent_gen_charge:
                await set_power_number(c.hass, c.ent_gen_charge, max(signed, 0.0))
            if c.ent_gen_discharge:
                await set_power_number(c.hass, c.ent_gen_discharge, max(-signed, 0.0))
        c.last_applied = f"{action} ({signed:+.0f} W, generiek)"
        return abs(signed)


_ADAPTERS = {
    "zendure": ZendureAdapter,
    "marstek": MarstekAdapter,
    "generic": GenericAdapter,
}


def create_adapter(kind: str, coordinator) -> BatteryAdapter:
    cls = _ADAPTERS.get(kind, GenericAdapter)
    return cls(coordinator)
