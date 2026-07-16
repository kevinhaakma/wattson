"""Constanten voor Wattson — slimme thuisaccu."""
import json
import os

DOMAIN = "wattson_ems"
PLATFORMS = ["sensor", "switch", "select"]

# ---------- config-entry optie-sleutels (bron-entiteiten) ----------
CONF_ENT_PRICE = "ent_price"
CONF_ENT_SOC = "ent_soc"
CONF_ENT_P1 = "ent_p1"
CONF_ENT_WALLBOX_1 = "ent_wallbox_1"
CONF_ENT_WALLBOX_2 = "ent_wallbox_2"
# thuis-gate per EV-meting (optioneel): voertuig-telemetrie meet óók laden
# elders, dus de meting telt alleen mee als de gate 'home'/'on' meldt;
# unknown/unavailable = thuis (fail-safe: liever onnodig blokkeren dan de
# auto uit de accu voeden).
CONF_ENT_WALLBOX_1_HOME = "ent_wallbox_1_thuis"
CONF_ENT_WALLBOX_2_HOME = "ent_wallbox_2_thuis"
CONF_ENT_PV_NOW = "ent_pv_now"
CONF_ENT_PV_REMAIN = "ent_pv_remain"
CONF_ENT_PV_TOMORROW = "ent_pv_tomorrow"
CONF_ENT_ZD_OPERATION = "ent_zd_operation"
CONF_ENT_ZD_MANUAL = "ent_zd_manual"
CONF_ENT_ZD_HEMS = "ent_zd_hems"
CONF_ENT_ZD_CHG = "ent_zd_chg"
CONF_ENT_ZD_DIS = "ent_zd_dis"
CONF_ENT_ZD_INLIM = "ent_zd_inlim"    # number.*_input_limit (max laadvermogen)
CONF_ENT_ZD_OUTLIM = "ent_zd_outlim"  # number.*_output_limit (max ontlaadvermogen)
# select.*_ac_mode: moet 'input' zijn om AC te laden en 'output' om te
# ontladen; de Zendure-manager zet dit niet betrouwbaar zelf, dus de
# adapter stuurt hem mee
CONF_ENT_ZD_ACMODE = "ent_zd_acmode"

# ---------- adapter (accumerk) ----------
CONF_ADAPTER = "adapter"
ADAPTER_ZENDURE = "zendure"
ADAPTER_GENERIC = "generic"
ADAPTER_MARSTEK = "marstek"
ADAPTERS = [ADAPTER_ZENDURE, ADAPTER_MARSTEK, ADAPTER_GENERIC]

# generieke adapter: number-entiteiten waarmee elk accumerk aanstuurbaar is
CONF_ENT_GEN_POWER = "ent_gen_power"          # één signed number: +W laden / -W ontladen
CONF_ENT_GEN_CHARGE = "ent_gen_charge"        # of twee losse numbers
CONF_ENT_GEN_DISCHARGE = "ent_gen_discharge"

# marstek venus (esp32/modbus): force-mode + forcible charge/discharge power.
# mode-entity mag een select (opties stop/charge/discharge) of number (0/1/2) zijn.
CONF_ENT_MS_MODE = "ent_ms_mode"
CONF_ENT_MS_CHARGE = "ent_ms_charge"
CONF_ENT_MS_DISCHARGE = "ent_ms_discharge"

# telemetrie voor marstek/generic (optioneel): gemeten laad-/ontlaadvermogen
# van de accu zelf. Zonder deze sensoren kan de watchdog op die adapters geen
# runaway detecteren en wordt de huislast niet voor accu-vermogen gecorrigeerd.
CONF_ENT_BAT_CHG = "ent_bat_chg"
CONF_ENT_BAT_DIS = "ent_bat_dis"

# accu-eigenschappen (instelbaar per installatie)
CONF_CAPACITY = "capacity_kwh"
CONF_MIN_SOC_PCT = "min_soc_pct"
CONF_P_CHARGE = "p_charge_max_w"
CONF_P_DISCHARGE = "p_discharge_max_w"

# jaarsaldering-bewaking: totaaltellers (kWh, total_increasing) waaruit de
# netto-importruimte van dit jaar wordt berekend; leeg = bewaking uit
CONF_ENT_IMPORT_TOTALS = "ent_import_totalen"
CONF_ENT_EXPORT_TOTALS = "ent_export_totalen"

# exportprijs-korting ná het saldering-einde (1-1-2027, zie scenario.py):
# wat een teruggeleverde kWh dan minder waard is dan een geïmporteerde.
# Onder saldering komt de wedge uit params.json (getraind, ~0,02).
CONF_WEDGE_POST = "wedge_post_saldering"

# ---------- defaults ----------
# Accu-defaults komen uit het battery-blok van params.json: dat blok wordt
# door de trainer geëxporteerd en is de enige bron van waarheid voor de
# apparaatgrenzen. Voorheen liepen const.py (1600/800) en params.json
# (2000/1400, de getrainde apparaat-realiteit) stil uiteen, waardoor een
# verse wizard-installatie niet-getrainde limieten kreeg.
def _battery_defaults() -> dict:
    path = os.path.join(os.path.dirname(__file__), "params.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["battery"]
    except (OSError, ValueError, KeyError):
        return {}


_BAT = _battery_defaults()
_BAT_CAPACITY = float(_BAT.get("capacity_kwh", 5.76))
_BAT_MIN_SOC_PCT = round(
    float(_BAT.get("soc_min_kwh", 0.58)) / _BAT_CAPACITY * 100.0) if _BAT_CAPACITY else 10
_BAT_P_CHARGE = float(_BAT.get("p_charge_max_w", 2000.0))
_BAT_P_DISCHARGE = float(_BAT.get("p_discharge_max_w", 1400.0))

# Bewust géén entity-id's: elke installatie kiest zijn eigen bronnen in de
# setup-wizard / options-flow. Een lege waarde betekent "niet geconfigureerd";
# de coordinator behandelt lege entiteiten als afwezig.
DEFAULT_OPTIONS = {
    CONF_ENT_PRICE: "",
    CONF_ENT_SOC: "",
    CONF_ENT_P1: "",
    CONF_ENT_WALLBOX_1: "",
    CONF_ENT_WALLBOX_2: "",
    CONF_ENT_WALLBOX_1_HOME: "",
    CONF_ENT_WALLBOX_2_HOME: "",
    CONF_ENT_PV_NOW: "",
    CONF_ENT_PV_REMAIN: "",
    CONF_ENT_PV_TOMORROW: "",
    CONF_ENT_ZD_OPERATION: "",
    CONF_ENT_ZD_MANUAL: "",
    CONF_ENT_ZD_HEMS: "",
    CONF_ENT_ZD_CHG: "",
    CONF_ENT_ZD_DIS: "",
    CONF_ENT_ZD_INLIM: "",
    CONF_ENT_ZD_OUTLIM: "",
    CONF_ENT_ZD_ACMODE: "",
    CONF_ADAPTER: ADAPTER_ZENDURE,
    CONF_ENT_GEN_POWER: "",
    CONF_ENT_GEN_CHARGE: "",
    CONF_ENT_GEN_DISCHARGE: "",
    CONF_ENT_MS_MODE: "",
    CONF_ENT_MS_CHARGE: "",
    CONF_ENT_MS_DISCHARGE: "",
    CONF_ENT_BAT_CHG: "",
    CONF_ENT_BAT_DIS: "",
    CONF_CAPACITY: _BAT_CAPACITY,
    CONF_MIN_SOC_PCT: _BAT_MIN_SOC_PCT,
    CONF_P_CHARGE: _BAT_P_CHARGE,
    CONF_P_DISCHARGE: _BAT_P_DISCHARGE,
    CONF_WEDGE_POST: 0.10,
    CONF_ENT_IMPORT_TOTALS: [],
    CONF_ENT_EXPORT_TOTALS: [],
}

EV_THRESHOLD_KW = 0.5      # daarboven telt als "auto laadt"
EV_HOUSE_MIN_W = 100       # huisdeel-ontladen tijdens EV pas vanaf deze last

# dynamisch bijspringen (realtime laag bovenop het uurplan). De beslissing
# zelf is de λ-regel (values.py); deze drempels zijn apparaat-bescherming:
# geen relais-gecycle voor verwaarloosbare vermogens of flinterdunne marges.
ASSIST_IMPORT_W = 150      # huis-import waarboven piek-assist mag starten
                           # (boven de 50 W minimale Zendure-stap en de 100 W
                           # track-deadband; kleinere piekjes zijn ruis)
ASSIST_EXPORT_W = 300      # export waarboven overschot-assist mag starten
ASSIST_STOP_W = 40         # pas stoppen als bronvraag/overschot vrijwel nul is
ASSIST_MARGIN_EUR = 0.005  # hysterese op de λ-vergelijking: prijs moet dit
                           # boven de vloer / onder het plafond liggen
ASSIST_THROTTLE_S = 10     # minimale tijd tussen assist-beslissingen — gelijk
                           # aan de P1-updatecadans (~10 s); sneller beslissen
                           # dan er nieuwe metingen zijn heeft geen zin
ASSIST_SOC_MARGE_KWH = 0.15
ASSIST_MAX_SOC_MARGIN_KWH = 0.05  # laad-assist stopt voor de absolute bovengrens
ASSIST_POWER_DEADBAND_W = 50      # voorkom setpoint-calls voor meetruis
DISCHARGE_EXPORT_ABORT_W = 150    # bronexport die zelfs bij het volledige nog
                                  # niet gemeten ontlaadcommando overblijft
DISCHARGE_EXPORT_ABORT_HOLD_S = 15  # bevestig over meerdere P1-updates; normale
                                    # smart-charge-regelruis bleef binnen ±124 W
ASSIST_STOP_GRACE_S = 150  # "voorbij" moet zo lang aanhouden vóór echt stoppen:
                           # vangt wolk-dips en stale telemetrie af zonder gecycle
ASSIST_MIN_RUN_S = 180     # opwarmtijd na assist-start: accutelemetrie (60s-poll)
                           # loopt achter, dus "voorbij" is hier geen stopbewijs;
                           # harde stops (SoC-vol, EV) blijven wél direct gelden
# demping laden <-> overschotladen: demotie naar vast netladen alleen als het
# overschot het geplande vermogen dit hele venster niet droeg (piek-geheugen;
# een wolkgat op het tick-moment is geen bewijs)
SURPLUS_DEMOTE_WINDOW_S = 300
SURPLUS_DEMOTE_MARGIN_W = 300
UPDATE_MINUTES = 10        # her-plan interval; realtime werk (bijspringen,
                           # EV-guard, discharge-guard) is event-gedreven en
                           # de veiligheid draait apart op WATCH_INTERVAL_S
WATCH_INTERVAL_S = 60      # eigen lichte bewakingslus (watchdog + stale-guard)
DAGLICHT = (7, 21)         # uren waarbinnen de PV-bel wordt verdeeld

# watchdog / robuustheid
WATCH_FRESH_S = 180        # meetwaarde ouder dan dit telt niet als bewijs
WATCH_RUNAWAY_W = 300      # accuvermogen boven dit zonder opdracht = runaway
GEENDATA_STOP_S = 600      # telemetrie zo lang stil met sturing aan -> veilig stoppen
WATCH_STOP_GRACE_S = 45    # na een eigen stopcommando loopt het apparaat door
                           # cloud-latentie nog even uit; binnen de grace is de
                           # zojuist gestopte richting geen runaway

# Volgen van de gemeten vraag. Asymmetrisch, want de twee richtingen hebben
# verschillende urgentie:
# - RUIMTE GEVEN (limiet/setpoint omhoog naar de vraag) is haastwerk: zolang
#   het te laag staat komt de piek van het net. Gebeurt event-gedreven op de
#   P1-meter, throttled op TRACK_FAST_THROTTLE_S. Commando-latentie is gemeten
#   op ~0,2 s, dus dit landt binnen een seconde na de meterupdate.
# - TERUGNEMEN mag lui: te veel ruimte kost niets (matching exporteert niet,
#   en op vaste adapters remt de discharge-guard direct bij export).
TRACK_INTERVAL_S = 30      # trage lus: terugnemen + surplus-promotie
TRACK_FAST_THROTTLE_S = 2  # snelle lus: minimale tijd tussen twee ophogingen
TRACK_DEADBAND_W = 40      # restimport onder deze band niet najagen (onder de
                           # 50 W apparaat-startstap; export-guard remt overshoot)
SETPOINT_ACK_DEADBAND_W = 25  # vast setpoint geldt als fysiek bereikt binnen deze
                              # tolerantie (P1/accu lopen asynchroon bij cloudlatentie)
TRACK_MARGE_W = 150        # limiet iets boven de vraag zodat matching kan ademen
TRACK_LOWER_GRACE_S = 180  # terugnemen volgt de PIEK-vraag van dit venster:
                           # de kale momentvraag oscilleert (P1 ~0 na matching,
                           # ontlaadmeting loopt achter) en elke limiet-write
                           # herstart het apparaat kort

# discharge-guard (marstek/generic): het ontlaad-setpoint is daar een vast
# vermogen; zakt de huisvraag, dan verlaagt deze altijd-actieve bewaking het
# setpoint (nooit verhogen — dat doet de volgende plan-tick).
DIS_GUARD_THROTTLE_S = 15
DIS_GUARD_DEADBAND_W = 25

# wissel-demping: een modewissel (rust <-> laden/ontladen) gaat pas door als
# het CUMULATIEVE voordeel over de horizon deze drempel overschrijdt. Stopt
# pendelen rond break-even-prijzen zonder echte marge weg te geven: het
# gemiste voordeel telt per tick op en de wissel volgt zodra die loont.
SWITCH_DEADBAND_EUR = 0.02
PLAN_MIN_DWELL_S = 900     # na een modewissel: kleine voordelen wachten deze
                           # tijd uit, zodat vlakke (nacht)prijzen het relais
                           # niet elke tick laten schakelen
DWELL_OVERRIDE_EUR = 0.05  # een wissel die per tick zoveel oplevert (echte
                           # piek / duur uur) gaat wél direct door de dwell heen

# verdachte lastsprong: springt de huisvraag in één tick zoveel omhoog zonder
# dat een wallbox het bevestigt, dan kan het een EV-start zijn waarvan de
# vermogenssensor achterloopt (~1 min bij Keba/Tesla) -> één tick niet ontladen
EV_SUSPECT_JUMP_W = 3000

# agressiviteit = de knop op de doelfunctie. pref (= alpha = beta, €/kWh) is
# de zelfvoorzienings-voorkeur: hoe duurder import/hoe onaantrekkelijker
# export in het planningsdoel. deg is het bijbehorende plannings-
# slijtagegewicht; beta = extra export-korting bovenop pref (asymmetrie):
# beprijst centen-trades (reserve verkopen om hem uren later terug te kopen)
# zonder huisdekking te raken. Combinaties komen uit de grid-search
# (hertraind 2026-07-16 op de EV-geschoonde dataset, 95 dgn, saldering / 2027):
# agressief €166/€174 pj bij 41,6/59,1% zelfvoorziening,
# gebalanceerd €153/€169 bij 52,3/63,0%, rustig €119/€163 bij 61,6/65,7%.
# (De oude, veel lagere zelfvoorzieningscijfers telden onherkende EV-nachten
# als huislast mee.)
AGGRO_LEVELS = {
    "rustig": {"pref": 0.05, "beta_extra": 0.04, "deg": 0.02, "risk": 0.10},
    "gebalanceerd": {"pref": 0.02, "beta_extra": 0.02, "deg": 0.02, "risk": 0.05},
    "agressief": {"pref": 0.0, "beta_extra": 0.0, "deg": 0.03, "risk": 0.02},
}
AGGRO_DEFAULT = "gebalanceerd"
