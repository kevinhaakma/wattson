"""Volgregelaar voor vaste-setpoint-adapters — pure python, HA-onafhankelijk.

sign=+1: ontladen (schatting = huislast = P1 + setpoint)
sign=-1: laden    (schatting = bronoverschot = -P1 + setpoint)
Dezelfde lus, dezelfde bewijsregels; bij laden geldt bovendien een ondergrens
(het geplande netlaadvermogen): overschot erboven wordt meegenomen, daaronder
blijft het plan staan. Gemeten 06-09 11:00: de eigen overschotmatcher van de
Zendure (smart_charging) slingert net zo als bij ontladen — 27 statuswissels
en 9 relaiskliks in 40 min, 65 Wh geladen bij 95 Wh export.

Waarom één trage lus (ontwerpkeuze 2026-09-05):
De P1-meter levert elke ~10 s een sample en een Zendure-commando is pas na
~20-30 s op die meter zichtbaar. Elke regelaar die sneller reageert dan die
latentie combineert metingen van twee verschillende setpoints en gaat
slingeren. Dat is gemeten bij Wattsons vorige volglus (zaagtand ~100 s: fast()
verhoogde per sample op vraag = P1 + accu-echo, de echo bevestigde na 10 s
terwijl de P1 het effect nog niet zag -> 273/713/1110/1284 W bij 270 W
huislast) én bij de eigen matcher van het apparaat (smart_discharging: aan/uit
elke ~50 s, 11 wissels en 7 relaiskliks in 11 min, 2 kW bij 1,7 kW export).

Regels:
1. Na een eigen commando tellen samples pas mee vanaf commando + LATENCY_S.
   Daarna geldt: fysiek accuvermogen == commando, dus huislast = P1 + commando.
2. Beslissen op de mediaan van de huislast over het venster (>= WINDOW_S,
   >= MIN_SAMPLES) — nooit op één sample.
3. Doel = huislast, begrensd op [FLOOR_W, cap]. NOOIT naar 0: uitschakelen is
   voorbehouden aan planner en veiligheid (elke aan/uit = relaisklik + ~40 s
   niets leveren). Bij minder vraag dan de vloer exporteert de accu een paar
   tientallen watt; dat is goedkoper dan een schakeling.
4. Alleen schrijven als het verschil > DEADBAND_W (elke write herstart de
   regelaar van het apparaat kort).
De lus is daarmee per constructie stabiel: één beslissing per (latentie +
venster), op bewijs dat niet meer door de eigen vorige stap vervuild is.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FollowLoop:
    sign: float = 1.0                  # +1 ontladen, -1 laden
    latency_s: float = 25.0
    window_s: float = 20.0
    min_samples: int = 2
    deadband_w: float = 40.0
    floor_w: float = 30.0
    max_age_s: float = 90.0
    setpoint_w: float = 0.0            # laatst gecommandeerd (fysiek na latency)
    cmd_t: float | None = None         # tijdstip laatste commando
    _hist: list[tuple[float, float]] = field(default_factory=list)  # (t, huislast)
    writes: int = 0
    # P1-ack: na een commando van >= ACK_MIN_W moet de meter na de latentie een
    # verschuiving van minstens ACK_RATIO x het verschil laten zien; anders volgt
    # het apparaat niet (offline integratie, commando niet aangekomen). Twee keer
    # achter elkaar = niet-volgend. Gemeten 06-09 11:29: telemetrie stil, lus
    # liep op 500->782->926 W zonder dat het apparaat aantoonbaar volgde.
    ACK_MIN_W: float = 100.0
    ACK_RATIO: float = 0.3
    _pre_p1: float | None = None
    _cmd_delta: float = 0.0
    _ack_pending: bool = False
    _last_p1: float | None = None
    unconfirmed: int = 0

    # ---------- invoer ----------
    def note_command(self, t: float, setpoint_w: float) -> None:
        """Registreer een (eigen of extern) ontlaadcommando; oud bewijs vervalt."""
        prev = self.setpoint_w
        self.setpoint_w = max(float(setpoint_w), 0.0)
        self.cmd_t = t
        self._hist = []
        self._cmd_delta = self.setpoint_w - prev
        self._pre_p1 = self._last_p1
        self._ack_pending = (self._pre_p1 is not None and abs(self._cmd_delta) >= self.ACK_MIN_W)

    def observe(self, t: float, p1_w: float) -> None:
        """Neem een P1-sample op als het niet meer door de vorige stap is vervuild."""
        self._last_p1 = p1_w
        if self.cmd_t is not None and t < self.cmd_t + self.latency_s:
            return
        if self._ack_pending:
            # eerste sample na de latentie: verschuiving t.o.v. vóór het commando
            self._ack_pending = False
            shift = self.sign * (self._pre_p1 - p1_w)
            if shift < self.ACK_RATIO * self._cmd_delta if self._cmd_delta > 0 else shift > self.ACK_RATIO * self._cmd_delta:
                self.unconfirmed += 1
            else:
                self.unconfirmed = 0
        self._hist.append((t, self.sign * p1_w + self.setpoint_w))
        cutoff = t - self.max_age_s
        if self._hist and self._hist[0][0] < cutoff:
            self._hist = [s for s in self._hist if s[0] >= cutoff]

    # ---------- uitvoer ----------
    @property
    def following(self) -> bool:
        """False zodra twee opeenvolgende commando's niet op de meter terugkwamen."""
        return self.unconfirmed < 2

    def house_estimate(self) -> float | None:
        if len(self._hist) < self.min_samples:
            return None
        if self._hist[-1][0] - self._hist[0][0] < self.window_s:
            return None
        vals = sorted(v for _, v in self._hist)
        mid = len(vals) // 2
        return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0

    def decide(self, cap_w: float, min_w: float = 0.0) -> float | None:
        """Nieuw setpoint (W) als er bewijs is voor een wijziging buiten de deadband, anders None.

        min_w: ondergrens (laden: het geplande netlaadvermogen; 0 bij bijspringen).

        Richting-consistent: verhogen alleen als ÉLK sample in het venster
        import boven de deadband toont, verlagen alleen als élk sample export
        toont. Eén meter-uitschieter (gemeten 06-09 01:22: één sample 2270 W
        tussen ~0 W-samples) kan de mediaan van drie al meetrekken; met deze
        eis kan geen enkel los sample de lus bewegen. Kost ~10 s reactie.
        """
        huis = self.house_estimate()
        if huis is None:
            return None
        doel = min(max(huis, self.floor_w, min_w), max(cap_w, self.floor_w))
        if abs(doel - self.setpoint_w) <= self.deadband_w:
            return None
        vals = [v for _, v in self._hist]
        if doel > self.setpoint_w and min(vals) <= self.setpoint_w + self.deadband_w:
            return None   # niet alle samples tonen tekort: geen bewijs
        if doel < self.setpoint_w and max(vals) >= self.setpoint_w - self.deadband_w:
            return None   # niet alle samples tonen overschot: geen bewijs
        return doel

    def step(self, t: float, p1_w: float, cap_w: float, min_w: float = 0.0) -> float | None:
        """Observe + decide in één; bij een besluit wordt het commando meteen genoteerd."""
        self.observe(t, p1_w)
        doel = self.decide(cap_w, min_w)
        if doel is not None:
            self.note_command(t, doel)
            self.writes += 1
        return doel


def DischargeLoop(**kw):
    """Compat-alias: ontlaadlus."""
    return FollowLoop(sign=1.0, **kw)


def ChargeLoop(**kw):
    """Laadlus: schatting = bronoverschot."""
    return FollowLoop(sign=-1.0, **kw)
