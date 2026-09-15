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
    def replay_recovery(samples):
        since = None
        for elapsed_s, p1_w, command_w, measured_w in samples:
            source_p1 = A.conservative_source_p1(p1_w, command_w, measured_w)
            since, ready = A.export_recovery_state(
                source_p1,
                threshold_w=150,
                now_s=elapsed_s,
                since_s=since,
                hold_s=15,
            )
            if ready:
                return elapsed_s
        return None

    first_since, first_ready = A.export_recovery_state(
        -314, threshold_w=150, now_s=100, since_s=None, hold_s=15)
    held_since, held_ready = A.export_recovery_state(
        -314, threshold_w=150, now_s=110, since_s=first_since, hold_s=15)
    ready_since, is_ready = A.export_recovery_state(
        -314, threshold_w=150, now_s=115, since_s=held_since, hold_s=15)
    reset_since, reset_ready = A.export_recovery_state(
        -149, threshold_w=150, now_s=116, since_s=ready_since, hold_s=15)

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
        "12:24 bronexport blijft conservatief zichtbaar":
            A.conservative_source_p1(-808, 494, 49) == -314,
        "13:54 bronexport blijft conservatief zichtbaar":
            A.conservative_source_p1(-1198, 609, 50) == -589,
        "13:58 bronexport blijft conservatief zichtbaar":
            A.conservative_source_p1(-1213, 1035, 50) == -178,
        "accu-veroorzaakte export is geen bronexport":
            A.conservative_source_p1(-100, 500, 500) == 400,
        "exporttimer start maar vuurt niet direct":
            first_since == 100 and not first_ready,
        "exporttimer wacht over tweede P1-update":
            held_since == 100 and not held_ready,
        "exporttimer vuurt na bevestigingsduur":
            ready_since == 100 and is_ready,
        "exporttimer reset onder de drempel":
            reset_since is None and not reset_ready,
        "12:24 incident herstelt binnen 21 seconden": replay_recovery([
            (0, -808, 494, 49),
            (11, -855, 0, 494),
            (21, -907, 0, 0),
        ]) == 21,
        "13:54 incident herstelt binnen 20 seconden": replay_recovery([
            (0, -1198, 609, 50),
            (10, -1189, 0, 50),
            (20, -1223, 0, 0),
        ]) == 20,
        "13:58 incident herstelt binnen 20 seconden": replay_recovery([
            (0, -1213, 1035, 50),
            (10, -1214, 0, 50),
            (20, -1262, 0, 0),
        ]) == 20,
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
