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
# uur-forecast van de PV-bron (optioneel, bv. Forecast.Solar
# energy_current_hour / energy_next_hour): scherpt de eerste twee planuren
# aan waar de generieke daglicht-bel het slechtst timet
CONF_ENT_PV_HOUR_NOW = "ent_pv_uur_nu"
CONF_ENT_PV_HOUR_NEXT = "ent_pv_uur_volgend"
# geplande accu-kalibratie (optioneel, bv. Zendure next_calibration): rond een
# kalibratiecyclus drijft de SoC-meting — Wattson waarschuwt in de attributen
CONF_ENT_CALIBRATION = "ent_kalibratie"
CONF_ENT_ZD_OPERATION = "ent_zd_operation"
CONF_ENT_ZD_MANUAL = "ent_zd_manual"
CONF_ENT_ZD_HEMS = "ent_zd_hems"
CONF_ENT_ZD_SOCSET = "ent_zd_socset"   # SoC-plafond-number van het apparaat (socSet)
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
ADAPTER_MARSTEK = "marstek"              # RS485/Modbus: force-mode + forcible powers
ADAPTER_MARSTEK_LOCAL = "marstek_local"  # Marstek Local API (UDP): passive-mode-service
ADAPTERS = [ADAPTER_ZENDURE, ADAPTER_MARSTEK, ADAPTER_MARSTEK_LOCAL, ADAPTER_GENERIC]

# generieke adapter: number-entiteiten waarmee elk accumerk aanstuurbaar is
CONF_ENT_GEN_POWER = "ent_gen_power"          # één signed number: +W laden / -W ontladen
CONF_ENT_GEN_CHARGE = "ent_gen_charge"        # of twee losse numbers
CONF_ENT_GEN_DISCHARGE = "ent_gen_discharge"

# marstek venus (esp32/modbus): force-mode + forcible charge/discharge power.
# mode-entity mag een select (opties stop/charge/discharge) of number (0/1/2) zijn.
CONF_ENT_MS_MODE = "ent_ms_mode"
CONF_ENT_MS_CHARGE = "ent_ms_charge"
CONF_ENT_MS_DISCHARGE = "ent_ms_discharge"
# optioneel: de RS485-control-mode-switch (register 42000). Zonder actieve
# RS485-control negeert de Venus alle force-registers; de adapter zet de
# switch aan vóór het eerste commando als hij uit staat.
CONF_ENT_MS_RS485 = "ent_ms_rs485"
# optioneel: charge/discharge-to-SoC (register 42011). De Venus stopt een
# force-laadopdracht op deze SoC; staat hij laag (bv. 80) dan haalt Wattson
# zijn plafond nooit. De adapter zet hem bij laden op het planplafond en bij
# ontladen op de ondergrens.
CONF_ENT_MS_SOC_TARGET = "ent_ms_soc_target"

# marstek local api (UDP, HACS-integraties jaapp/ha-marstek-local-api,
# Flodesirat-fork, taurgis/has-marstek-local-api): sturing via de service
# `<domein>.set_passive_mode` met een device_id, een signed vermogen
# (+ = ontladen, - = laden) en een looptijd (cd_time). Na afloop van de
# looptijd valt de accu terug op zijn eigen modus — dat is de dodemansknop:
# Wattson ververst het commando periodiek, en valt Wattson weg dan neemt de
# accu binnen MS_PASSIVE_TTL_S zelf het roer weer over.
CONF_MS_DEVICE_ID = "ms_device_id"
CONF_MS_SERVICE = "ms_service"          # "auto" of het integratiedomein
MS_SERVICE_AUTO = "auto"
MS_SERVICE_DOMAINS = ["marstek_local_api", "marstek"]
MS_PASSIVE_TTL_S = 900       # looptijd van elk passive-commando
MS_PASSIVE_REFRESH_S = 300   # ouder dan dit -> opnieuw sturen (bewakingslus 60 s)

# telemetrie voor marstek/generic (optioneel): gemeten laad-/ontlaadvermogen
# van de accu zelf. Zonder deze sensoren kan de watchdog op die adapters geen
# runaway detecteren en wordt de huislast niet voor accu-vermogen gecorrigeerd.
CONF_ENT_BAT_CHG = "ent_bat_chg"
CONF_ENT_BAT_DIS = "ent_bat_dis"

# accu-eigenschappen (instelbaar per installatie)
CONF_CAPACITY = "capacity_kwh"
CONF_MIN_SOC_PCT = "min_soc_pct"
CONF_MAX_SOC_PCT = "max_soc_pct"   # planplafond; LiFePO4 veroudert het snelst
                                   # vol geladen — 90 kost ~0,6 kWh handel op
                                   # donkere dagen, spaart kalenderleven
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
    CONF_ENT_PV_HOUR_NOW: "",
    CONF_ENT_PV_HOUR_NEXT: "",
    CONF_ENT_CALIBRATION: "",
    CONF_ENT_ZD_OPERATION: "",
    CONF_ENT_ZD_MANUAL: "",
    CONF_ENT_ZD_HEMS: "",
    CONF_ENT_ZD_SOCSET: "",
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
    CONF_ENT_MS_RS485: "",
    CONF_ENT_MS_SOC_TARGET: "",
    CONF_MS_DEVICE_ID: "",
    CONF_MS_SERVICE: MS_SERVICE_AUTO,
    CONF_ENT_BAT_CHG: "",
    CONF_ENT_BAT_DIS: "",
    CONF_CAPACITY: _BAT_CAPACITY,
    CONF_MIN_SOC_PCT: _BAT_MIN_SOC_PCT,
    CONF_MAX_SOC_PCT: 100.0,
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
ASSIST_STOP_MARGIN_EUR = 0.02  # STOP-hysterese: een lopende assist stopt pas als de
                               # λ-vergelijking dit ver de andere kant op is (06-09
                               # 17:12: stop op €0,005 marge, 48 min voor het plan
                               # dezelfde actie startte = 2 relaiskliks voor centen)
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

# --- bronsignaal (SourcePower): één gefilterde kijk op de netflow ------------
# De P1-meter levert losse momentopnames en die bevatten uitschieters van één
# sample: gemeten 12-08 17:00-19:00 stond het huis onafgebroken op -1600..-2250 W
# export, met daartussen enkelvoudige samples van +118 tot +2026 W. De assist
# startte op precies die samples een ontlading, waarna de export-recovery hem
# 15-60 s later terecht afbrak: 12 start/afbreek-paren op twee avonden, en twee
# keer een echte ontlading die volledig het net op liep (18:42-18:51 700 W bij
# een P1 van -350 W). Een beslissing mag daarom nooit op één sample rusten: de
# afwijking moet een heel venster aanhouden, in zowel de kale als de
# accu-gecorrigeerde meting (wijken die uiteen, dan is er geen bewijs en doet
# Wattson niets — fail-safe).
SOURCE_WINDOW_S = 180      # bewaarvenster van bron-samples (≥ het langste
                           # confirm-venster hieronder)
SOURCE_MIN_SAMPLES = 2     # minimaal aantal metingen in een confirm-venster;
                           # tijdsdekking alléén is te weinig bewijs bij een
                           # meter die even stil valt
ASSIST_START_CONFIRM_S = 30  # piek/overschot moet dit hele venster aanhouden
                             # voordat de assist mag starten. Bewust kort
                             # (reactiesnelheid boven maximale demping, keuze
                             # 2026-08-13): ~20 s wachten bij de 10s-cadans van
                             # de P1. Sweep op 2,5 dag echte meterdata:
                             #   direct:  348 starts, 161 vals
                             #   30s/2:   134 starts,  33 vals  <- gekozen
                             #   60s/4:    54 starts,   2 vals
                             # Korter dan 30 s kan niet: de meter valt tot 20 s
                             # stil (p99) en dan klappert het venster op zijn
                             # eigen cadans. Terug naar 60/4 als het relais te
                             # vaak cyclet; de min-run/stop-grace dempen na de
                             # start het restant.

# demping laden <-> overschotladen: demotie naar vast netladen alleen als het
# overschot het geplande vermogen dit hele venster niet droeg (piek-geheugen;
# een wolkgat op het tick-moment is geen bewijs)
SURPLUS_DEMOTE_WINDOW_S = 300
# promotie vast netladen -> overschotladen eist AANGEHOUDEN export: de kale
# P1 moet het hele SourcePower-confirmvenster (ASSIST_START_CONFIRM_S) onder
# -SURPLUS_PROMOTE_W liggen. Gemeten 03-09: één P1-sample van -400 W in een
# wolkgat promoveerde 7x naar smart_charging, dat daarna op ~300 W bleef
# hangen tot de volgende plantick — 149 van 219 dalminuten op een derde van
# het geplande vermogen, accu 57% i.p.v. vol.
SURPLUS_PROMOTE_W = 300
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
GEENDATA_RELOAD_S = 300    # eerder al: accu-integratie herladen (homeassistant.reload_config_entry
                           # op de SoC-entity) — 05/06-09: zendure_ha verliest de cloud-MQTT en
                           # herstelt alleen door een entry-reload; max 1x per RELOAD_COOLDOWN_S
GEENDATA_RELOAD_COOLDOWN_S = 900
WATCH_STOP_GRACE_S = 45    # na een eigen stopcommando loopt het apparaat door
                           # cloud-latentie nog even uit; binnen de grace is de
                           # zojuist gestopte richting geen runaway
STARTUP_GRACE_S = 90       # na (her)start van de integratie: zolang er nog
                           # geen eigen commando is gegeven is een draaiende
                           # accu geen runaway maar een geërfde actie van vóór
                           # de reload (reload wist de stop-grace-status;
                           # gereproduceerd 2026-08-14: entry-reload tijdens
                           # gepland laden -> valse WATCHDOG-trip)

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

# Ontlaadregelaar voor vaste-setpoint-adapters (discharge_loop.DischargeLoop,
# ontwerp 2026-09-05). Vervangt fast()/tick()-volgen, DischargeGuard en de
# assist-modulatie voor ontladen op deze adapters. Kern: nooit beslissen op
# samples die nog door het eigen vorige commando vervuild zijn, en nooit naar 0.
DIS_LOOP_LATENCY_S = 35    # commando -> zichtbaar op de P1. Gemeten Zendure
                           # 20-30 s; sim: aanname MOET >= werkelijk zijn
                           # (25 s bij een 40 s-apparaat: 161 writes i.p.v. 35),
                           # te hoog kost ~10 Wh/3 u. Daarom ruim.
DIS_LOOP_WINDOW_S = 20     # bewijsvenster na de latentie (>= 2 P1-samples)
DIS_LOOP_MIN_SAMPLES = 2
DIS_LOOP_DEADBAND_W = 40   # kleinere correcties niet schrijven (write = herstart)
DIS_LOOP_FLOOR_W = 30      # nooit lager: uitschakelen is aan planner/veiligheid
DIS_LOOP_STUCK_S = 90      # apparaat volgt het commando zo lang aantoonbaar niet
                           # -> één schop (rust + opnieuw), max 1x per 5 min.
                           # 05-09: na een zendure_ha-reload toonde de select
                           # 'manual' maar stond de manager idle; Wattson schrijft
                           # alleen bij wijziging en bleef dus eeuwig wachten
DIS_LOOP_KICK_COOLDOWN_S = 300
DIS_LOOP_MAX_AGE_S = 30    # mediaan over de laatste 30 s (3 samples): sim
                           # 05-09: 90 s liet oude waarden na een sprong ~1 min
                           # meewegen (trapsgewijs terugnemen); 30 s reageert
                           # 30 s sneller bij gelijk aantal writes, alle latenties

# kalibratievenster: de BMS herijkt zijn SoC-schatting alleen bij een volle
# lading (zendure_ha zet next_calibration = nu + 30 d zodra electricLevel 100%
# meldt). Met een planplafond < 100% gebeurt dat nooit meer en drijft de SoC.
# Zoveel uur vóór next_calibration gaat het plafond (plan én apparaat) naar
# 100% tot de accu vol is geweest; daarna terug naar max_soc_pct.
CAL_LEAD_H = 36

# wissel-demping: een modewissel (rust <-> laden/ontladen) gaat pas door als
# het CUMULATIEVE voordeel over de horizon deze drempel overschrijdt. Stopt
# pendelen rond break-even-prijzen zonder echte marge weg te geven: het
# gemiste voordeel telt per tick op en de wissel volgt zodra die loont.
SWITCH_DEADBAND_EUR = 0.02
# overbrugging: een actieve stand (laden/ontladen) niet loslaten voor rust als
# dezelfde actie binnen BRIDGE_GAP_H uur hervat wordt en het uurprijsverschil
# onder BRIDGE_MAX_DPRICE blijft — elke aan/uit is een relaisklik (06-09 12:14:
# laden gepauzeerd voor 13:00 à €0,002/kWh goedkoper = 2 kliks voor €0,001)
BRIDGE_GAP_H = 2
BRIDGE_MAX_DPRICE = 0.01
PLAN_MIN_DWELL_S = 900     # na een modewissel: kleine voordelen wachten deze
                           # tijd uit, zodat vlakke (nacht)prijzen het relais
                           # niet elke tick laten schakelen
DWELL_OVERRIDE_EUR = 0.05  # een wissel die per tick zoveel oplevert (echte
                           # piek / duur uur) gaat wél direct door de dwell heen

# verdachte lastsprong: springt de huisvraag in één tick zoveel omhoog zonder
# dat een wallbox het bevestigt, dan kan het een EV-start zijn waarvan de
# vermogenssensor achterloopt (~1 min bij Keba/Tesla) -> één tick niet ontladen
EV_SUSPECT_JUMP_W = 3000

# agressiviteit = de knop op de doelfunctie. pref (= alpha, €/kWh) is de
# zelfvoorzienings-voorkeur: hoe duurder import in het planningsdoel.
# beta = pref + beta_extra: export-korting. deg = plannings-slijtagegewicht.
# sell_top_pct (optioneel): >0 beperkt sell_ok tot de duurste N% van de
# zichtbare horizon (dynamische drempel i.p.v. de globale verkoopschakelaar).
# Combinaties uit grid-search (hertraind 2026-07-16, 95 dgn, saldering / 2027):
# agressief €166/€174 pj bij 41,6/59,1% zelfvoorziening,
# gebalanceerd €153/€169 bij 52,3/63,0%, rustig €119/€163 bij 61,6/65,7%.
# zelfvoorzienend (2026-09-13, 10,7 dgn backtest): asymmetrisch alpha>>beta
# + top-5% verkoop → 55% ZV bij slechts €4/jaar minder dan pure arbitrage.
AGGRO_LEVELS = {
    "rustig": {"pref": 0.05, "beta_extra": 0.04, "deg": 0.02, "risk": 0.10},
    "gebalanceerd": {"pref": 0.02, "beta_extra": 0.02, "deg": 0.02, "risk": 0.05},
    "agressief": {"pref": 0.0, "beta_extra": 0.0, "deg": 0.03, "risk": 0.02},
    "zelfvoorzienend": {"pref": 0.12, "beta_extra": -0.10, "deg": 0.02, "risk": 0.05,
                        "sell_top_pct": 5},
}
AGGRO_DEFAULT = "gebalanceerd"
