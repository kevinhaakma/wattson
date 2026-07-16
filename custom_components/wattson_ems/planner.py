"""Accu-planner: rolling-horizon DP over een SoC-grid.

Pure Python, geen dependencies — dit bestand draait zowel in de trainer
(deze PC) als ongewijzigd in de HA custom integration (thin client).

Conventies:
- vermogen in W op AC-zijde; + = laden, - = ontladen (geleverd aan huis)
- SoC in kWh (bruikbaar venster wordt door soc_min/soc_max begrensd)
- prijzen in €/kWh; import = incl. belasting, export = wat teruglevering oplevert

Doelfunctie: geld én zelfvoorziening, beide expliciet. Naast de kale prijzen
weegt de planner een voorkeur mee: import telt als prijs + alpha (elke van het
net gekochte kWh is de gebruiker alpha extra 'waard' om te vermijden), export
als prijs - beta (eigen energie weggeven is beta minder waard dan de kale
opbrengst). alpha = beta = 0 geeft pure prijsarbitrage; hogere waarden geven
zelfvoorziening voorrang. Eigenverbruik is daarmee een gevolg van de
doelfunctie, geen structurele aanname.

Export: een uur met sell_ok=True (verkopen door de gebruiker aangezet) mag tot
het maximale ontlaadvermogen leveren; het surplus boven de huisvraag gaat tegen
de exportprijs het net op. Of dat loont beslist de DP zelf — er is geen vaste
prijsdrempel. sell_ok=False begrenst ontladen op de netto huisvraag.
"""


class Params:
    def __init__(self, **kw):
        # Defaults spiegelen het battery-blok in params.json (de canonieke,
        # door de trainer geëxporteerde apparaatgrenzen); dit bestand blijft
        # bewust dependency-vrij, dus de waarden staan hier als literal.
        # Coordinator en trainer overschrijven ze altijd expliciet.
        self.capacity_kwh = 5.76
        self.soc_min_kwh = 0.58        # 10%
        self.soc_max_kwh = 5.76
        self.p_charge_max_w = 2000.0   # opties-laadlimiet Zendure 2400 AC
        self.p_discharge_max_w = 1400.0  # inverse_max_power
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
        # Zelfvoorzienings-voorkeur (€/kWh, geen kasgeld): alpha maakt import
        # in het planningsdoel duurder, beta maakt export minder waard. De
        # kas-boekhouding (trainer) rekent altijd met alpha = beta = 0.
        self.alpha = 0.0
        self.beta = 0.0
        # Onzekerheids-discount ("bij twijfel wint het huis nú"): risk_steps
        # is de op eigen data getrainde per-uur-toename van de cumulatieve
        # prognosefout (genormaliseerd, zie training/fit_risk.py); risk_k de
        # sterkte. De DP vermenigvuldigt toekomstwaarde per stap met
        # (1 - risk_k * stap): een zeker voordeel nu verslaat daardoor een
        # even groot maar onzeker voordeel later, terwijl grote spreads
        # (avondpiek) de kleine haircut moeiteloos overleven.
        self.risk_k = 0.0
        self.risk_steps = ()
        self.soc_step_kwh = 0.08
        self.charge_levels = (0.0, 500.0, 1000.0, 1500.0, 2000.0)
        self.discharge_levels = (0.0, 350.0, 700.0, 1050.0, 1400.0)
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
        cost += kwh * (step.price_imp + params.alpha)
    else:
        # negatief: opbrengst teruglevering, verminderd met de
        # zelfvoorzienings-korting (eigen energie weggeven kost voorkeur)
        cost += kwh * (step.price_exp - params.beta)
    return cost, soc, action, thru


def plan_end_soc(steps, setpoints, soc0_kwh, params):
    """SoC (kWh) waarmee het plan de horizon verlaat, gesimuleerd via hour_result."""
    soc = min(max(soc0_kwh, params.soc_min_kwh), params.soc_max_kwh)
    for step, action in zip(steps, setpoints):
        _, soc, _, _ = hour_result(step, action, soc, params)
    return soc


def action_is_effective(step, action_w, soc_kwh, params, minimum_w=50.0):
    """Of een vastgehouden planactie fysiek nog uitvoerbaar is."""
    _, _, actual_w, _ = hour_result(step, action_w, soc_kwh, params)
    return abs(actual_w) >= minimum_w


class LambdaTable:
    """Marginale waarde van opgeslagen energie: λ(t, SoC) in €/kWh (doel-euro's).

    Afgeleid uit de DP-waardefunctie (λ = -dV/dSoC): wat één extra kWh in de
    accu aan de rest van de horizon bijdraagt. Realtime beslissingen vergelijken
    de actuele prijs hiermee — dezelfde afweging als het uurplan, zonder aparte
    budget-heuristieken. Omdat λ per SoC-punt beschikbaar is, is de planreserve
    impliciet: zakt de lading, dan stijgt λ en stopt ontladen vanzelf.
    """

    __slots__ = ("soc_min_kwh", "soc_step_kwh", "rows")

    def __init__(self, soc_min_kwh, soc_step_kwh, rows):
        self.soc_min_kwh = soc_min_kwh
        self.soc_step_kwh = soc_step_kwh
        self.rows = rows  # per uur: λ per SoC-gridvak (lengte n_soc - 1)

    def value(self, t, soc_kwh):
        """λ op uur t bij deze SoC (€/kWh); klemt op horizon- en gridranden."""
        if not self.rows:
            return 0.0
        row = self.rows[max(0, min(t, len(self.rows) - 1))]
        if not row:
            return 0.0
        i = int((soc_kwh - self.soc_min_kwh) / self.soc_step_kwh)
        return row[max(0, min(i, len(row) - 1))]


def discharge_price_floor(lam, params):
    """Minimale opbrengst (€/kWh, AC-zijde) waarbij nú ontladen beter is dan
    de energie bewaren: het verlies aan toekomstwaarde plus slijtage, gedeeld
    door het conversierendement."""
    eta = eta_oneway(params.p_discharge_max_w, params)
    if eta <= 0.0:
        return float("inf")
    return (lam + params.deg_cost) / eta


def charge_price_ceiling(lam, params):
    """Maximale kostprijs (€/kWh, AC-zijde) waarbij nú laden loont: de
    toekomstwaarde minus slijtage, maal het conversierendement."""
    eta = eta_oneway(params.p_charge_max_w, params)
    return (lam - params.deg_cost) * eta


def plan(steps, soc0_kwh, params, terminal_value=0.0):
    """Backward-induction DP; zie plan_with_values. Retourneert
    (setpoints_w, expected_cost) — setpoints per uur, +laden/-ontladen."""
    setpoints, total, _ = plan_with_values(steps, soc0_kwh, params, terminal_value)
    return setpoints, total


def plan_with_values(steps, soc0_kwh, params, terminal_value=0.0):
    """Backward-induction DP.

    terminal_value: €/kWh voor lading die aan het eind van de horizon over is
    (de 'waarde van morgen'); voorkomt dat de planner leegdumpt op een matige piek.

    Retourneert (setpoints_w, expected_cost, LambdaTable) — het plan plus de
    marginale-waardetabel waarop realtime beslissingen dezelfde afweging maken.
    """
    n_soc = int(round((params.soc_max_kwh - params.soc_min_kwh) / params.soc_step_kwh)) + 1
    grid_soc = [params.soc_min_kwh + i * params.soc_step_kwh for i in range(n_soc)]
    actions = [-p for p in params.discharge_levels if p > 0.0] + list(params.charge_levels)

    def snap(soc):
        i = int(round((soc - params.soc_min_kwh) / params.soc_step_kwh))
        return max(0, min(n_soc - 1, i))

    # onzekerheids-fade per stap: de waarde van alles vanaf uur t+1 telt,
    # gezien vanaf uur t, licht af met de getrainde prognose-onzekerheid.
    # Compounding door de recursie benadert de cumulatieve foutcurve.
    rk = params.risk_k
    rs = params.risk_steps
    def fade(lead):
        if rk <= 0.0 or not rs:
            return 1.0
        s = rs[min(lead, len(rs) - 1)]
        return max(1.0 - rk * s, 0.5)

    # V[i] = minimale kosten vanaf dit punt bij SoC grid_soc[i]
    V = [-(s - params.soc_min_kwh) * terminal_value for s in grid_soc]
    best = []      # per stap: beste actie per SoC-index
    lam_rows = []  # per stap: λ per SoC-gridvak (−ΔV/Δsoc)
    for t_abs in range(len(steps) - 1, -1, -1):
        step = steps[t_abs]
        f = fade(t_abs + 1)
        Vn = [0.0] * n_soc
        bn = [0.0] * n_soc
        for i, soc in enumerate(grid_soc):
            bc, ba = None, 0.0
            for a in actions:
                c, soc2, act, _ = hour_result(step, a, soc, params)
                tot = c + V[snap(soc2)] * f
                if bc is None or tot < bc - 1e-9:
                    bc, ba = tot, a
            Vn[i], bn[i] = bc, ba
        lam_rows.append([(Vn[i] - Vn[i + 1]) / params.soc_step_kwh
                         for i in range(n_soc - 1)])
        V = Vn
        best.append(bn)
    best.reverse()
    lam_rows.reverse()

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
    return setpoints, total, LambdaTable(params.soc_min_kwh, params.soc_step_kwh, lam_rows)


def terminal_value_from_prices(future_prices, params):
    """Waarde (€/kWh) van restlading: wat kost het om diezelfde kWh morgen
    goedkoop te laden, gecorrigeerd voor round-trip — met plafond op wat
    ontladen straks oplevert. Conservatief en simpel."""
    if not future_prices:
        return 0.0
    cheap = sorted(future_prices)[: max(1, len(future_prices) // 6)]
    p_cheap = sum(cheap) / len(cheap)
    eta_rt = (eta_oneway(params.p_charge_max_w, params)
              * eta_oneway(params.p_discharge_max_w, params))
    return p_cheap * eta_rt
