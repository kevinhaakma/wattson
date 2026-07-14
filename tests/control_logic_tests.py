"""Standalone regressietests voor gates en vaste-setpoint-terugkoppeling."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADAPTERS_PATH = HERE.parent / "custom_components" / "wattson_ems" / "adapters.py"

spec = importlib.util.spec_from_file_location("wattson_adapters_control", ADAPTERS_PATH)
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


def main():
    checks = {
        "EV-gate zonder staat is fail-safe thuis": A.ev_gate_allows(None),
        "EV-gate unknown is fail-safe thuis": A.ev_gate_allows("unknown"),
        "EV-gate unavailable is fail-safe thuis": A.ev_gate_allows("unavailable"),
        "EV-gate home telt mee": A.ev_gate_allows("home"),
        "EV-gate on telt mee": A.ev_gate_allows("ON"),
        "EV-gate thuis telt mee": A.ev_gate_allows(" thuis "),
        "EV-gate not_home sluit uit": not A.ev_gate_allows("not_home"),
        "EV-gate away sluit uit": not A.ev_gate_allows("away"),
        "EV-gate off sluit uit": not A.ev_gate_allows("off"),
        "setpoint exact bereikt": A.setpoint_feedback_settled(500, 500, 25),
        "setpoint binnen ack-band": A.setpoint_feedback_settled(500, 525, 25),
        "setpoint buiten ack-band": not A.setpoint_feedback_settled(500, 526, 25),
        "ontbrekende feedback is geen ack": not A.setpoint_feedback_settled(500, None, 25),
        "Zendure vereist fysieke ack": A.ZendureAdapter.caps.feedback_ack,
        "Marstek behoudt directe regeling": not A.MarstekAdapter.caps.feedback_ack,
        "Generic behoudt directe regeling": not A.GenericAdapter.caps.feedback_ack,
    }
    failed = []
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            failed.append(name)
    print(f"\n{len(checks) - len(failed)}/{len(checks)} PASS")
    if failed:
        raise AssertionError(", ".join(failed))


if __name__ == "__main__":
    try:
        main()
    except AssertionError:
        sys.exit(1)
