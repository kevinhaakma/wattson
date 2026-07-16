"""Getypeerde stuurtoestand en serialisatie van accucommando's.

Deze module is bewust Home Assistant-onafhankelijk. De coordinator, realtime-
regelaars en safety delen hierdoor hetzelfde vocabulaire zonder UI-teksten als
machinestatus te hoeven interpreteren.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable


class BatteryAction(str, Enum):
    """Fysieke actie die een adapter moet uitvoeren."""

    IDLE = "rust"
    CHARGE = "laden"
    SURPLUS_CHARGE = "laden_overschot"
    DISCHARGE = "ontladen"
    SELL = "verkopen"

    @classmethod
    def parse(cls, value: BatteryAction | str) -> BatteryAction:
        if isinstance(value, cls):
            return value
        return cls(value)

    @property
    def is_charge(self) -> bool:
        return self in (self.CHARGE, self.SURPLUS_CHARGE)

    @property
    def is_discharge(self) -> bool:
        return self in (self.DISCHARGE, self.SELL)


class AdviceMode(str, Enum):
    """Interne beslisstand; de waarde blijft het bestaande UI-label."""

    INIT = "init"
    NO_DATA = "geen data"
    IDLE = "rust"
    CHARGE = "laden"
    DISCHARGE = "ontladen"
    SELL = "verkopen"
    EV_CHECK = "rust (EV-check)"
    EV_GUARD = "rust (EV-guard)"
    ASSIST_CHARGE = "bijspringen: laden"
    ASSIST_DISCHARGE = "bijspringen: ontladen"

    @classmethod
    def parse(cls, value: AdviceMode | str) -> AdviceMode:
        if isinstance(value, cls):
            return value
        return cls(value)

    @property
    def is_idle(self) -> bool:
        return self in (self.IDLE, self.EV_CHECK, self.EV_GUARD)

    @property
    def expects_charge(self) -> bool:
        return self in (self.CHARGE, self.ASSIST_CHARGE)

    @property
    def expects_discharge(self) -> bool:
        return self in (self.DISCHARGE, self.SELL, self.ASSIST_DISCHARGE)


class CommandSource(str, Enum):
    """Herkomst van een fysiek commando, voor arbitrage en diagnose."""

    PLANNER = "planner"
    REALTIME = "realtime"
    SAFETY = "safety"
    USER = "user"
    LIFECYCLE = "lifecycle"


@dataclass(frozen=True)
class Decision:
    """Een volledig advies; dit is de enige semantische beslisstatus."""

    mode: AdviceMode
    setpoint_w: float = 0.0
    reason: str = ""
    next_action: str | None = None


@dataclass(frozen=True)
class BatteryCommand:
    """Een gevalideerd verzoek aan de hardwarelaag."""

    action: BatteryAction
    power_w: float
    p1_cap: bool
    source: CommandSource
    generation: int


@dataclass(frozen=True)
class CommandResult:
    """Uitkomst van arbitrage en adapter-aansturing."""

    command: BatteryCommand
    applied_w: float
    skipped: bool = False


class CommandArbiter:
    """Serialiseert adapter-writes en maakt verouderde wachters annuleerbaar.

    Een service-call die al bezig is kan niet veilig worden onderbroken. Een
    safety-stop verhoogt daarom eerst de generatie: alle oudere commando's die
    nog op het lock wachten worden daarna overgeslagen, waarna rust als eerste
    geldige nieuwe opdracht wordt uitgevoerd.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._generation = 0
        self.last_result: CommandResult | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def locked(self) -> bool:
        return self._lock.locked()

    def invalidate_pending(self) -> int:
        self._generation += 1
        return self._generation

    def command(
        self,
        action: BatteryAction | str,
        power_w: float,
        *,
        p1_cap: bool,
        source: CommandSource,
    ) -> BatteryCommand:
        return BatteryCommand(
            action=BatteryAction.parse(action),
            power_w=max(float(power_w), 0.0),
            p1_cap=bool(p1_cap),
            source=source,
            generation=self._generation,
        )

    async def execute(
        self,
        command: BatteryCommand,
        apply: Callable[[BatteryCommand], Awaitable[float]],
    ) -> CommandResult:
        async with self._lock:
            if command.generation != self._generation:
                result = CommandResult(command, 0.0, skipped=True)
            else:
                result = CommandResult(command, float(await apply(command)))
            self.last_result = result
            return result
