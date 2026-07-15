"""EV-bewaking: wallbox-metingen, thuis-gates en de directe EV-guard.

Voertuigtelemetrie (bijv. sensor.<auto>_charger_power) meet óók laden elders
(openbare lader); met een geconfigureerde thuis-gate telt zo'n meting alleen
mee als het voertuig thuis is (incident 2026-07-14: accu geblokkeerd terwijl
de auto niet eens thuis stond).
"""
from __future__ import annotations

from . import adapters as A
from .const import EV_THRESHOLD_KW
from .telemetry import Telemetry


class EvMonitor:
    """Bewaakt EV-laadsessies en stopt ontladen zodra de auto thuis laadt."""

    def __init__(self, telemetry: Telemetry, wallboxes: list[tuple[str, str]]) -> None:
        """wallboxes: lijst van (vermogens-entiteit, thuis-gate-entiteit)."""
        self.t = telemetry
        self.wallboxes = [(ent, gate) for ent, gate in wallboxes if ent]
        # bewust huisdeel-ontladen tijdens een EV-sessie: door de plantick
        # zelf gezet en dan géén reden voor de guard om in te grijpen
        self.house_share_active = False

    def entities(self) -> list[str]:
        """Alle entiteiten waarop de guard moet luisteren (metingen + gates)."""
        out = []
        for ent, gate in self.wallboxes:
            out.append(ent)
            if gate:
                out.append(gate)
        return list(dict.fromkeys(out))

    def _at_home(self, gate: str) -> bool:
        if not gate:
            return True
        st = self.t.hass.states.get(gate)
        return A.ev_gate_allows(None if st is None else st.state)

    def _wallbox_w(self, ent: str, gate: str, fresh_s: float | None = None) -> float:
        if not ent or not self._at_home(gate):
            return 0.0
        w = self.t.power_w(ent) if fresh_s is None else self.t.fresh_power_w(ent, fresh_s)
        return w or 0.0

    def charging(self) -> bool:
        threshold_w = EV_THRESHOLD_KW * 1000.0
        return any(self._wallbox_w(ent, gate) > threshold_w
                   for ent, gate in self.wallboxes)

    def max_w(self, fresh_s: float | None = None) -> float:
        """Grootste thuis-meting; max voorkomt dubbel tellen wanneer wallbox
        en voertuigtelemetrie dezelfde sessie meten."""
        return max((self._wallbox_w(ent, gate, fresh_s)
                    for ent, gate in self.wallboxes), default=0.0)

    def sum_fresh_w(self, fresh_s: float) -> float:
        """Som van verse thuis-metingen (voor de versheids-drempel)."""
        return sum(self._wallbox_w(ent, gate, fresh_s)
                   for ent, gate in self.wallboxes)

    def guard(self, c, _event) -> None:
        """Auto begint thuis te laden -> ontladen/verkopen direct stoppen.

        Uitzondering: bewust huisdeel-ontladen tijdens een EV-sessie
        (house_share_active) is door de plantick zelf gezet en mag blijven
        staan — het setpoint staat dan op huislast, niet op vol vermogen.
        """
        if self.house_share_active:
            return
        if c.control_enabled and self.charging() and (
                c.advies in ("ontladen", "verkopen") or c.assist_active == "ontladen"):
            prev = (c.advies, c.last_applied)
            c.assist_active = None
            c.hass.async_create_task(c.set_battery("rust", 0.0))
            c.advies = "rust (EV-guard)"
            c.setpoint_w = 0.0
            c.reden = "EV begon te laden — ontladen direct gestopt"
            c.log_decision(prev)  # ingreep zichtbaar in het historie-attribuut
            c.write_entities()
