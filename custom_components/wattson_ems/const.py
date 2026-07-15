"""Constanten voor Wattson — slimme thuisaccu."""

DOMAIN = "wattson_ems"
PLATFORMS = ["sensor", "switch", "select"]

# ---------- config-entry optie-sleutels (bron-entiteiten) ----------
CONF_ENT_PRICE = "ent_price"
CONF_ENT_SOC = "ent_soc"
CONF_ENT_P1 = "ent_p1"
CONF_ENT_WALLBOX_1 = "ent_wallbox_1"
CONF_ENT_WALLBOX_2 = "ent_wallbox_2"
# thuis-gate per EV-meting (optioneel): voertuig-telemetrie zoals
# sensor.<auto>_charger_power meet óók laden elders (openbare lader), en de
# EV-guard zou de accu dan onnodig blokkeren terwijl de auto niet eens thuis
# is (incident 2026-07-14). Met een device_tracker/person/binary_sensor als
# gate telt de bijbehorende wallbox-meting alleen mee als die entiteit
# 'home'/'on' meldt; unknown/unavailable telt als thuis (fail-safe: liever
# onnodig conservatief dan de auto uit de accu voeden).
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
# ontladen; de Zendure-manager zet dit niet betrouwbaar zelf (incident
# 2026-07-09 en 2026-07-10: accu laadde niet omdat ac_mode op output bleef)
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

# exportprijs-korting ná het saldering-einde (1-1-2027, zie scenario.py):
# wat een teruggeleverde kWh dan minder waard is dan een geïmporteerde.
# Onder saldering komt de wedge uit params.json (getraind, ~0,02).
CONF_WEDGE_POST = "wedge_post_saldering"

# ---------- defaults ----------
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
    CONF_CAPACITY: 5.76,
    CONF_MIN_SOC_PCT: 10,
    CONF_P_CHARGE: 1600,
    CONF_P_DISCHARGE: 800,
    CONF_WEDGE_POST: 0.10,
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
ASSIST_STOP_GRACE_S = 150  # "piek/overschot voorbij" moet zo lang aanhouden
                           # voordat de assist echt stopt: vangt wolk-dips en
                           # stale telemetrie af zonder relais-gecycle; native
                           # matching moduleert in de tussentijd zelf al mee
ASSIST_MIN_RUN_S = 180     # opwarmtijd na assist-start: de accutelemetrie
                           # (60s-poll, write-on-change) loopt achter op het
                           # apparaat, dus "piek/overschot voorbij" is in dit
                           # venster geen geldig stopbewijs (harde stops zoals
                           # SoC-vol, EV en reserve blijven wél direct gelden)
# demping laden <-> overschotladen (zelfde advies, dus buiten de gewone
# wisseldrempel): demotie naar vast netladen alleen als het overschot het
# geplande vermogen dit hele venster niet heeft kunnen dragen (piek-geheugen —
# een wolkgat op het tick-moment is geen bewijs; 14:35-incident: demotie en
# 23 s later alweer promotie).
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
# na een eigen stopcommando blijft het apparaat nog even actief (cloud-
# latentie); zolang de grace loopt is de zojuist gestopte richting geen
# runaway (incident 2026-07-10 14:53: assist stopte bij wolk, watchdog zag
# de uitlopende 1592 W laden als runaway en tripte onnodig)
WATCH_STOP_GRACE_S = 45

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
TRACK_DEADBAND_W = 40      # restimport onder deze band niet najagen; 40 W ligt
                           # onder Zendure's 50 W startstap, terwijl de 25 W
                           # export-guard een eventuele overshoot direct remt
SETPOINT_ACK_DEADBAND_W = 25  # vast setpoint geldt als fysiek bereikt binnen
                              # deze tolerantie; blokkeert asynchrone P1/accu-
                              # combinaties tijdens Zendure-cloudlatentie
TRACK_MARGE_W = 150        # limiet iets boven de vraag zodat matching kan ademen
TRACK_LOWER_GRACE_S = 180  # terugnemen volgt de PIEK-vraag van de laatste 3 min:
                           # direct na matching leest P1 ~0 en de ontlaadmeting
                           # loopt achter, waardoor de kale momentvraag oscilleert
                           # en elke limiet-write het apparaat kort herstart

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
# slijtagegewicht. Combinaties komen uit de grid-search (backtest 95 dgn,
# saldering / 2027): agressief €172/€168 pj bij 22,7/35,0% zelfvoorziening,
# gebalanceerd €165/€166 bij 27,3/37,7%, rustig €140/€159 bij 33,2/39,4%.
AGGRO_LEVELS = {
    "rustig": {"pref": 0.05, "deg": 0.01},
    "gebalanceerd": {"pref": 0.02, "deg": 0.02},
    "agressief": {"pref": 0.0, "deg": 0.03},
}
AGGRO_DEFAULT = "gebalanceerd"
