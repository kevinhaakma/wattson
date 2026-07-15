"""Accu-planner: rolling-horizon DP over een SoC-grid.

Pure Python, geen dependencies — dit bestand draait zowel in de trainer
(deze PC) als ongewijzigd in de HA custom integration (thin client).

Conventies:
- vermogen in W op AC-zijde; + = laden, - = ontladen (geleverd aan huis)
- SoC in kWh (bruikbaar venster wordt door soc_min/soc_max begrensd)
- prijzen in €/kWh; import = incl. belasting, export = wat teruglevering oplevert
- de accu mag nooit naar het net exporteren (grid_reverse = forbidden):
  ontladen wordt per uur gemaximeerd op de netto huisvraag.
  UITZONDERING: een uur met sell_ok=True (verkoopprijs boven de drempel én
  verkopen expliciet aangezet) mag tot het maximale ontlaadvermogen leveren;
  het surplus boven de huisvraag gaat tegen de exportprijs het net op.
"""


class Params:
    def __init__(self, **kw):
        self.capacity_kwh = 5.76
        self.soc_min_kwh = 0.58        # 10%
        self.soc_max_kwh = 5.76
        self.p_charge_max_w = 1600.0
        self.p_discharge_max_w = 800.0
        # Verliesmodel gekalibreerd op de massabalans van het echte apparaat
        # (179 u, 2026-07-07..15: 37,3 kWh in / 33,6 kWh uit / ΔSoC −0,67 =
        # 4,36 kWh verlies; model met deze waarden voorspelt 4,34). De oude
        # aannames (0,92 / 25 W) overschatten het verlies 2,3× en lieten de
        # DP winstgevende cycli afwijzen. Een vermogensafhankelijke straf
        # (p_fix) is in de meetdata niet aantoonbaar; het vaste eigenverbruik
        # zit apart in standby_w en loopt ALTIJD door (apparaat staat aan).
        self.eta_nom = 0.955           # conversierendement één richting, nominaal
        self.p_fix_w = 0.0             # vermogensafhankelijke verliezen per richting
        self.standby_w = 0.0           # continu eigenverbruik uit de accu (W)
        self.deg_cost = 0.04           # €/kWh doorzet (accu-zijde)
        self.soc_step_kwh = 0.08
        self.charge_levels = (0.0, 400.0, 800.0, 1200.0, 1600.0)
        self.discharge_levels = (0.0, 200.0, 400.0, 600.0, 800.0)
        for k, v in kw.items():
            if not hasattr(self, k):
                raise TypeError("onbekende parameter: %s" % k)
            setattr(self, k, v)

    def to_dict(self):
        return {k: getattr(self, k) for k in vars(self)}


def eta_oneway(p_w, params):
    """Rendement één richting als functie van vermogen (lager bij druppelen)."""
    if p_w <= params.p_fix_w:
        return 0.0
    return params.eta_nom * (1.0 - params.p_fix_w / p_w)


class Step:
    """Eén planningsuur."""
    __slots__ = ("price_imp", "price_exp", "load_w", "pv_w", "ev_charging", "sell_ok")

    def __init__(self, price_imp, price_exp, load_w, pv_w, ev_charging=False, sell_ok=False):
        self.price_imp = price_imp
        self.price_exp = price_exp
        self.load_w = load_w
        self.pv_w = pv_w
        self.ev_charging = ev_charging
        self.sell_ok = sell_ok


def hour_result(step, action_w, soc_kwh, params):
    """Effect van één uur: (kosten €, nieuwe SoC kWh, actievermogen W, doorzet kWh).

    kosten = netkosten + params.deg_cost × doorzet (het planningsdoel);
    doorzet (accu-zijde kWh) wordt apart teruggegeven zodat de boekhouding
    slijtage tegen een uniforme 'echte' prijs kan herwaarderen.
    action_w > 0: laden met dat AC-vermogen (begrensd door ruimte in de accu)
    action_w < 0: ontladen, geleverd aan huis (begrensd door huisvraag en lading)
    """
    net_home = step.load_w - step.pv_w  # >0: huis vraagt van net, <0: overschot
    cost = 0.0
    thru = 0.0
    if action_w > 0.0:
        p = min(action_w, params.p_charge_max_w)
        eta = eta_oneway(p, params)
        room = params.soc_max_kwh - soc_kwh
        stored = min(p * eta / 1000.0, room)     # kWh in de accu
        if eta <= 0.0 or stored <= 0.0:
            p = 0.0
            stored = 0.0
        else:
            p = stored * 1000.0 / eta            # AC-vermogen dat echt nodig was
        grid = net_home + p
        soc = soc_kwh + stored
        cost += params.deg_cost * stored
        thru = stored
        action = p
    elif action_w < 0.0:
        if step.ev_charging:
            # harde eis: nooit de auto vanuit de accu laden
            p = 0.0
        elif step.sell_ok:
            # verkopen: surplus boven de huisvraag mag het net op (exportprijs)
            p = min(-action_w, params.p_discharge_max_w)
        else:
            p = min(-action_w, params.p_discharge_max_w, max(net_home, 0.0))
        eta = eta_oneway(p, params)
        avail = soc_kwh - params.soc_min_kwh
        drawn = (p / eta / 1000.0) if eta > 0.0 else 0.0  # kWh uit de accu
        if drawn > avail:
            drawn = max(avail, 0.0)
            p = drawn * eta * 1000.0
        grid = net_home - p
        soc = soc_kwh - drawn
        cost += params.deg_cost * drawn
        thru = drawn
        action = -p
    else:
        grid = net_home
        soc = soc_kwh
        action = 0.0

    # continu eigenverbruik (omvormer/BMS/wifi) teert altijd op de lading in,
    # ook in rust — "vasthouden" is dus niet gratis. Alleen aftoppen tot het
    # minimum: het apparaat schakelt daaronder zelf uit.
    if params.standby_w > 0.0:
        soc = max(soc - params.standby_w / 1000.0, min(soc, params.soc_min_kwh))

    kwh = grid / 1000.0
    if kwh >= 0.0:
        cost += kwh * step.price_imp
    else:
        cost += kwh * step.price_exp  # negatief: opbrengst teruglevering
    return cost, soc, action, thru


def future_reserve_kwh(steps, setpoints, soc0_kwh, params):
    """Minimale startenergie die een toekomstig actiepad werkelijk nodig heeft.

    Alleen het grootste cumulatieve energietekort telt als reserve. Toekomstig
    laden vult een latere ontlading dus eerst aan; alle ontlaaduren simpelweg
    optellen zou dezelfde kWh meermaals reserveren en realtime gebruik blokkeren.
    """
    soc = min(max(soc0_kwh, params.soc_min_kwh), params.soc_max_kwh)
    cumulative = 0.0
    lowest = 0.0
    for step, action in zip(steps, setpoints):
        _, soc_next, _, _ = hour_result(step, action, soc, params)
        cumulative += soc_next - soc
        lowest = min(lowest, cumulative)
        soc = soc_next
    return max(-lowest, 0.0)


def plan_end_soc(steps, setpoints, soc0_kwh, params):
    """SoC (kWh) waarmee het plan de horizon verlaat, gesimuleerd via hour_result."""
    soc = min(max(soc0_kwh, params.soc_min_kwh), params.soc_max_kwh)
    for step, action in zip(steps, setpoints):
        _, soc, _, _ = hour_result(step, action, soc, params)
    return soc


def conservative_solar_surplus_kwh(steps, confidence=0.75):
    """Conservatieve netto PV-energie na de verwachte huislast."""
    net = sum((step.pv_w * confidence - step.load_w) / 1000.0 for step in steps)
    return max(net, 0.0)


def solar_backed_budget_kwh(
        steps, soc_kwh, params, confidence=0.75, buffer_kwh=0.75,
        soc_margin_kwh=0.15):
    """Energie die nu mag worden gebruikt en later conservatief door PV hervult."""
    surplus = conservative_solar_surplus_kwh(steps, confidence)
    room = max(params.soc_max_kwh - soc_kwh, 0.0)
    available = max(soc_kwh - params.soc_min_kwh - soc_margin_kwh, 0.0)
    return min(max(surplus - room - buffer_kwh, 0.0), available)


def action_is_effective(step, action_w, soc_kwh, params, minimum_w=50.0):
    """Of een vastgehouden planactie fysiek nog uitvoerbaar is."""
    _, _, actual_w, _ = hour_result(step, action_w, soc_kwh, params)
    return abs(actual_w) >= minimum_w


def plan(steps, soc0_kwh, params, terminal_value=0.0):
    """Backward-induction DP.

    terminal_value: €/kWh voor lading die aan het eind van de horizon over is
    (de 'waarde van morgen'); voorkomt dat de planner leegdumpt op een matige piek.

    Retourneert (setpoints_w, expected_cost) — setpoints per uur, +laden/-ontladen.
    """
    n_soc = int(round((params.soc_max_kwh - params.soc_min_kwh) / params.soc_step_kwh)) + 1
    grid_soc = [params.soc_min_kwh + i * params.soc_step_kwh for i in range(n_soc)]
    actions = [-p for p in params.discharge_levels if p > 0.0] + list(params.charge_levels)

    def snap(soc):
        i = int(round((soc - params.soc_min_kwh) / params.soc_step_kwh))
        return max(0, min(n_soc - 1, i))

    # V[i] = minimale kosten vanaf dit punt bij SoC grid_soc[i]
    V = [-(s - params.soc_min_kwh) * terminal_value for s in grid_soc]
    best = []  # per stap: beste actie per SoC-index
    for step in reversed(steps):
        Vn = [0.0] * n_soc
        bn = [0.0] * n_soc
        for i, soc in enumerate(grid_soc):
            bc, ba = None, 0.0
            for a in actions:
                c, soc2, act, _ = hour_result(step, a, soc, params)
                tot = c + V[snap(soc2)]
                if bc is None or tot < bc - 1e-9:
                    bc, ba = tot, a
            Vn[i], bn[i] = bc, ba
        V = Vn
        best.append(bn)
    best.reverse()

    # forward pass: haal het pad op
    setpoints = []
    soc = min(max(soc0_kwh, params.soc_min_kwh), params.soc_max_kwh)
    total = 0.0
    for t, step in enumerate(steps):
        a = best[t][snap(soc)]
        c, soc, act, _ = hour_result(step, a, soc, params)
        setpoints.append(act)
        total += c
    total -= (soc - params.soc_min_kwh) * terminal_value
    return setpoints, total


def terminal_value_from_prices(future_prices, params):
    """Waarde (€/kWh) van restlading: wat kost het om diezelfde kWh morgen
    goedkoop te laden, gecorrigeerd voor round-trip — met plafond op wat
    ontladen straks oplevert. Conservatief en simpel."""
    if not future_prices:
        return 0.0
    cheap = sorted(future_prices)[: max(1, len(future_prices) // 6)]
    p_cheap = sum(cheap) / len(cheap)
    eta_rt = eta_oneway(800.0, params) * eta_oneway(800.0, params)
    return p_cheap * eta_rt
