"""Eerlijk duel: model-based DP-planner vs reinforcement learning (Q-learning).

Zelfde accu-twin (planner.hour_result), zelfde kas-boekhouding (deg @ DEG_TRUE),
zelfde data. De RL-agent krijgt causale features inclusief een day-ahead-
vooruitblik (die informatie is in werkelijkheid ook beschikbaar), traint op de
eerste 88 dagen en wordt geëvalueerd op de laatste 7 — ongezien. De DP draait
zoals live (rolling horizon met dezelfde causale forecasts).

Doel: kwantificeren wat 'leren' hier oplevert t.o.v. 'exact rekenen'.
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "custom_components", "wattson_ems"))
import planner as P  # noqa: E402
import backtest as B  # noqa: E402

DEG_TRUE = 0.03
WEDGE = 0.0
EVAL_DAYS = 7
ACTIONS = [-1400.0, -1050.0, -700.0, -350.0, 0.0, 500.0, 1000.0, 1500.0, 2000.0]
EPOCH_CHECKPOINTS = [10, 50, 150, 400, 1000]
GAMMA = 0.997
random.seed(42)


def cash():
    return B.cash_params(B.mk_params(DEG_TRUE, 0.0, 0.0))


def trailing_means(rows, window=24):
    out = []
    s = 0.0
    for i, r in enumerate(rows):
        s += r["price"]
        if i >= window:
            s -= rows[i - window]["price"]
        out.append(s / min(i + 1, window))
    return out


def features(rows, i, soc, params, trail):
    r = rows[i]
    hour = r["loc_hour"] // 3
    span = params.soc_max_kwh - params.soc_min_kwh
    socb = min(int((soc - params.soc_min_kwh) / span * 8), 7)
    rel = r["price"] / max(trail[i], 1e-6)
    relb = 0 if rel < 0.85 else 1 if rel < 1.0 else 2 if rel < 1.15 else 3
    fut = [rows[j]["price"] for j in range(i + 1, min(i + 9, len(rows)))]
    up = 0 if not fut else (0 if max(fut) < r["price"] * 1.05
                            else 1 if max(fut) < r["price"] * 1.25 else 2)
    dn = 0 if not fut else (0 if min(fut) > r["price"] * 0.95
                            else 1 if min(fut) > r["price"] * 0.75 else 2)
    return (hour, socb, relb, up, dn)


def run_greedy(Q, rows, idxs, params, trail):
    """Greedy policy afspelen; retourneert totale 'true' kosten over idxs."""
    soc = params.soc_min_kwh
    total = 0.0
    # continu: begin bij de eerste eval-rij, soc gedragen vanaf dagstart min
    for i in idxs:
        st = features(rows, i, soc, params, trail)
        qs = [Q.get((st, k), 0.0) for k in range(len(ACTIONS))]
        a = ACTIONS[qs.index(max(qs))]
        c, soc, _, _ = P.hour_result(B.mk_step(rows[i], WEDGE), a, soc, params)
        total += c
    return total


def main():
    rows = B.load_rows()
    days = sorted({r["loc_date"] for r in rows})
    eval_days = set(days[-EVAL_DAYS:])
    train_idx = [i for i, r in enumerate(rows) if r["loc_date"] not in eval_days]
    eval_idx = [i for i, r in enumerate(rows) if r["loc_date"] in eval_days]
    params = cash()
    trail = trailing_means(rows)
    print(f"train {len(train_idx)} uren ({len(days) - EVAL_DAYS} dgn) | "
          f"eval {len(eval_idx)} uren ({EVAL_DAYS} dgn, ongezien)")

    # ---------- RL: tabulaire Q-learning ----------
    Q = {}
    n_updates = 0
    curve = []
    max_epochs = EPOCH_CHECKPOINTS[-1]
    for epoch in range(1, max_epochs + 1):
        eps = max(0.30 * (1 - epoch / max_epochs), 0.02)
        lr = max(0.15 * (1 - epoch / max_epochs), 0.01)
        soc = params.soc_min_kwh
        for i in train_idx:
            st = features(rows, i, soc, params, trail)
            if random.random() < eps:
                k = random.randrange(len(ACTIONS))
            else:
                qs = [Q.get((st, j), 0.0) for j in range(len(ACTIONS))]
                k = qs.index(max(qs))
            c, soc2, _, _ = P.hour_result(
                B.mk_step(rows[i], WEDGE), ACTIONS[k], soc, params)
            st2 = features(rows, min(i + 1, len(rows) - 1), soc2, params, trail)
            best_next = max(Q.get((st2, j), 0.0) for j in range(len(ACTIONS)))
            old = Q.get((st, k), 0.0)
            Q[(st, k)] = old + lr * (-c + GAMMA * best_next - old)
            n_updates += 1
            soc = soc2
        if epoch in EPOCH_CHECKPOINTS:
            score = run_greedy(Q, rows, eval_idx, params, trail)
            curve.append((epoch, score))
            print(f"  RL na {epoch:4d} epochs ({n_updates / 1e6:.1f}M updates): "
                  f"eval-week €{score:.2f}")

    # ---------- benchmarks op dezelfde eval-dagen ----------
    def eval_cost(costs):
        return sum(c for i, c in enumerate(costs) if rows[i]["loc_date"] in eval_days)

    dp_params = B.mk_params(0.03, 0.0, 0.05)
    results = {
        "geen accu": eval_cost(B.run_none(rows, params, WEDGE)[0]),
        "naief (vaste uren)": eval_cost(B.run_naive(rows, params, WEDGE)[0]),
        "RL (Q-learning)": curve[-1][1],
        "DP-planner (live)": eval_cost(B.run_planner(rows, dp_params, WEDGE, load_pct=0.5)[0]),
        "hindsight-plafond": eval_cost(B.run_hindsight(rows, params, WEDGE)[0]),
    }
    print(f"\n=== eval-week ({EVAL_DAYS} ongeziene dagen, kas incl. slijtage) ===")
    none = results["geen accu"]
    for name, cost in results.items():
        print(f"{name:22s} €{cost:7.2f}   besparing €{none - cost:6.2f}")
    ceiling = none - results["hindsight-plafond"]
    for name in ("RL (Q-learning)", "DP-planner (live)"):
        cap = (none - results[name]) / max(ceiling, 1e-9) * 100
        print(f"{name}: capture {cap:.0f}% van het plafond")


if __name__ == "__main__":
    main()
