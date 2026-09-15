"""Applicatielaag tussen Home Assistant-inputs en de zuivere DP-planner.

De coordinator verzamelt alleen de actuele bronnen. Deze module evalueert het
plan, maakt de UI-planning en vertaalt het eerste setpoint naar een getypeerde
beslissing. Daardoor blijven kasberekening en beslislabels uit de I/O-klasse.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from . import planner as P
from .control import AdviceMode, Decision


@dataclass
class PlanningContext:
    """Alle invoer van één consistente planningscyclus."""

    prices: list[tuple[datetime, float]]
    steps: list[P.Step]
    soc_kwh: float
    soc_pct: float
    pv_w: dict[datetime, float]
    today: date
    wedge: float
    ev_now: bool
    terminal_value: float
    p1_now_w: float | None
    battery_charge_w: float
    battery_discharge_w: float


@dataclass(frozen=True)
class PlanEvaluation:
    """Uitkomst van de DP plus de daarvan afgeleide kasbesparing."""

    setpoints: list[float]
    cost: float
    lambda_table: P.LambdaTable
    expected_saving: float


def evaluate(
    steps: list[P.Step],
    soc_kwh: float,
    params: P.Params,
    terminal_value: float,
) -> PlanEvaluation:
    """Draai de voorkeurs-DP en rapporteer daarnaast echte kasbesparing."""
    setpoints, cost, lam = P.plan_with_values(
        steps, soc_kwh, params, terminal_value=terminal_value)

    cash = P.Params(**params.to_dict())
    cash.alpha = 0.0
    cash.beta = 0.0
    cash.risk_k = 0.0
    base = 0.0
    planned = 0.0
    soc_base = soc_plan = min(max(soc_kwh, cash.soc_min_kwh), cash.soc_max_kwh)
    for step, action in zip(steps, setpoints):
        idle_cost, soc_base, _, _ = P.hour_result(step, 0.0, soc_base, cash)
        plan_cost, soc_plan, _, _ = P.hour_result(step, action, soc_plan, cash)
        base += idle_cost
        planned += plan_cost
    base -= (soc_base - cash.soc_min_kwh) * terminal_value
    planned -= (soc_plan - cash.soc_min_kwh) * terminal_value
    return PlanEvaluation(setpoints, cost, lam, round(base - planned, 2))


def slot_hours(prices: list[tuple[datetime, float]]) -> list[float]:
    """Lengte (uur) per prijsslot: afstand tot de volgende; de laatste erft die van de voorlaatste."""
    times = [when for when, _ in prices]
    out = [(b - a).total_seconds() / 3600.0 for a, b in zip(times, times[1:])]
    out.append(out[-1] if out else 1.0)
    return out


def plan_hours(
    prices: list[tuple[datetime, float]],
    steps: list[P.Step],
    setpoints: list[float],
    soc_kwh: float,
    params: P.Params,
    format_time: Callable[[datetime], str],
    limit: int = 16,
) -> list[dict]:
    """Compacte, vooruit gesimuleerde planning per klokuur voor de HA-sensor.

    Kwartierstappen worden per uur samengevoegd (tijdgewogen gemiddelden,
    SoC aan het eind van het uur), zodat de uur-kaarten ongewijzigd werken.
    """
    buckets = []
    soc = soc_kwh
    for (when, price), action, step in zip(prices, setpoints, steps):
        _, soc, _, _ = P.hour_result(step, action, soc, params)
        hour = when.replace(minute=0, second=0, microsecond=0)
        if not buckets or buckets[-1]["hour"] != hour:
            if len(buckets) >= limit:
                break
            buckets.append({"hour": hour, "dt": 0.0, "prijs": 0.0, "sp": 0.0,
                            "last": 0.0, "pv": 0.0})
        b = buckets[-1]
        d = step.dt_h
        b["dt"] += d
        b["prijs"] += price * d
        b["sp"] += action * d
        b["last"] += step.load_w * d
        b["pv"] += step.pv_w * d
        b["soc"] = soc
    return [{
        "tijd": format_time(b["hour"]),
        "prijs": round(b["prijs"] / b["dt"], 3),
        "setpoint_w": round(b["sp"] / b["dt"]),
        "soc_na_kwh": round(b["soc"], 2),
        "verwachte_last_w": round(b["last"] / b["dt"]),
        "verwachte_pv_w": round(b["pv"] / b["dt"]),
    } for b in buckets]


def plan_slots(
    prices: list[tuple[datetime, float]],
    steps: list[P.Step],
    setpoints: list[float],
    soc_kwh: float,
    params: P.Params,
    format_time: Callable[[datetime], str],
) -> list[dict]:
    """Planning per stap (kwartier bij kwartierprijzen), over de hele horizon."""
    result = []
    soc = soc_kwh
    for (when, price), action, step in zip(prices, setpoints, steps):
        _, soc, _, _ = P.hour_result(step, action, soc, params)
        result.append({
            "tijd": format_time(when),
            "prijs": round(price, 3),
            "setpoint_w": round(action),
            "soc_na_kwh": round(soc, 2),
        })
    return result


def decision_from_plan(
    setpoint_w: float,
    first_step: P.Step,
    wedge: float,
    lambda_now: float,
    schedule: list[dict],
) -> Decision:
    """Vertaal het eerste DP-setpoint naar de semantische stuurtoestand."""
    setpoint_w = round(setpoint_w)
    slot = "kwartier" if first_step.dt_h < 1.0 else "uur"
    if setpoint_w > 50:
        return Decision(
            AdviceMode.CHARGE,
            setpoint_w,
            f"goedkoop {slot} (€{first_step.price_imp:.3f})",
        )
    if setpoint_w < -50:
        net_home = max(first_step.load_w - first_step.pv_w, 0.0)
        if first_step.sell_ok and -setpoint_w > net_home + 100:
            return Decision(
                AdviceMode.SELL,
                setpoint_w,
                f"exportprijs €{first_step.price_imp - wedge:.3f} "
                f"> waarde van bewaren (λ €{lambda_now:.3f}/kWh)",
            )
        return Decision(
            AdviceMode.DISCHARGE,
            setpoint_w,
            f"duur {slot} (€{first_step.price_imp:.3f}), "
            f"huis vraagt {first_step.load_w:.0f} W",
        )

    upcoming = next((item for item in schedule[1:]
                     if abs(item["setpoint_w"]) > 50), None)
    if upcoming:
        action = "laden" if upcoming["setpoint_w"] > 0 else "ontladen"
        return Decision(
            AdviceMode.IDLE,
            0.0,
            f"wacht: {action} om {upcoming['tijd']} (€{upcoming['prijs']:.3f})",
            f"{action} om {upcoming['tijd']} ({upcoming['setpoint_w']:+d} W)",
        )
    return Decision(AdviceMode.IDLE, 0.0, "spread te klein binnen de horizon")
