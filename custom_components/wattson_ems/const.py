"""Constanten voor Wattson — slimme thuisaccu."""

DOMAIN = "wattson_ems"
PLATFORMS = ["sensor", "switch", "select"]

# ---------- config-entry optie-sleutels (bron-entiteiten) ----------
CONF_ENT_PRICE = "ent_price"
CONF_ENT_SOC = "ent_soc"
CONF_ENT_P1 = "ent_p1"
CONF_ENT_WALLBOX_1 = "ent_wallbox_1"
CONF_ENT_WALLBOX_2 = "ent_wallbox_2"
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

# accu-eigenschappen (instelbaar per installatie)
CONF_CAPACITY = "capacity_kwh"
CONF_MIN_SOC_PCT = "min_soc_pct"
CONF_P_CHARGE = "p_charge_max_w"
CONF_P_DISCHARGE = "p_discharge_max_w"

# verkopen: boven deze kale verkoopprijs (€/kWh, import minus wedge) mag de
# planner ontladen vóórbij de huisvraag (= exporteren). Alleen actief met de
# aparte "Wattson verkopen"-switch aan.
CONF_SELL_THRESHOLD = "verkoop_drempel_eur"

# ---------- defaults: de huidige entity-ids op deze installatie ----------
DEFAULT_ENT_PRICE = "sensor.zonneplan_current_electricity_tariff"
DEFAULT_ENT_SOC = "sensor.solarflow_2400_ac_electric_level"
DEFAULT_ENT_P1 = "sensor.p1_meter_power"
DEFAULT_ENT_WALLBOX_1 = "sensor.keba_p20_charging_power"
DEFAULT_ENT_WALLBOX_2 = "sensor.jimmy_charger_power"
DEFAULT_ENT_PV_NOW = "sensor.power_production_now"
DEFAULT_ENT_PV_REMAIN = "sensor.energy_production_today_remaining"
DEFAULT_ENT_PV_TOMORROW = "sensor.energy_production_tomorrow"
DEFAULT_ENT_ZD_OPERATION = "select.zendure_manager_operation"
DEFAULT_ENT_ZD_MANUAL = "number.zendure_manager_manual_power"
DEFAULT_ENT_ZD_HEMS = "binary_sensor.solarflow_2400_ac_hems_state"
DEFAULT_ENT_ZD_CHG = "sensor.solarflow_2400_ac_grid_input_power"
DEFAULT_ENT_ZD_DIS = "sensor.solarflow_2400_ac_output_home_power"
DEFAULT_ENT_ZD_INLIM = "number.solarflow_2400_ac_input_limit"
DEFAULT_ENT_ZD_OUTLIM = "number.solarflow_2400_ac_output_limit"

DEFAULT_OPTIONS = {
    CONF_ENT_PRICE: DEFAULT_ENT_PRICE,
    CONF_ENT_SOC: DEFAULT_ENT_SOC,
    CONF_ENT_P1: DEFAULT_ENT_P1,
    CONF_ENT_WALLBOX_1: DEFAULT_ENT_WALLBOX_1,
    CONF_ENT_WALLBOX_2: DEFAULT_ENT_WALLBOX_2,
    CONF_ENT_PV_NOW: DEFAULT_ENT_PV_NOW,
    CONF_ENT_PV_REMAIN: DEFAULT_ENT_PV_REMAIN,
    CONF_ENT_PV_TOMORROW: DEFAULT_ENT_PV_TOMORROW,
    CONF_ENT_ZD_OPERATION: DEFAULT_ENT_ZD_OPERATION,
    CONF_ENT_ZD_MANUAL: DEFAULT_ENT_ZD_MANUAL,
    CONF_ENT_ZD_HEMS: DEFAULT_ENT_ZD_HEMS,
    CONF_ENT_ZD_CHG: DEFAULT_ENT_ZD_CHG,
    CONF_ENT_ZD_DIS: DEFAULT_ENT_ZD_DIS,
    CONF_ENT_ZD_INLIM: DEFAULT_ENT_ZD_INLIM,
    CONF_ENT_ZD_OUTLIM: DEFAULT_ENT_ZD_OUTLIM,
    CONF_ADAPTER: ADAPTER_ZENDURE,
    CONF_ENT_GEN_POWER: "",
    CONF_ENT_GEN_CHARGE: "",
    CONF_ENT_GEN_DISCHARGE: "",
    CONF_ENT_MS_MODE: "",
    CONF_ENT_MS_CHARGE: "",
    CONF_ENT_MS_DISCHARGE: "",
    CONF_CAPACITY: 5.76,
    CONF_MIN_SOC_PCT: 10,
    CONF_P_CHARGE: 1600,
    CONF_P_DISCHARGE: 800,
    CONF_SELL_THRESHOLD: 0.45,
}

EV_THRESHOLD_KW = 0.5      # daarboven telt als "auto laadt"

# dynamisch bijspringen (realtime laag bovenop het uurplan)
ASSIST_IMPORT_W = 400      # huis-import waarboven piek-assist mag starten
ASSIST_EXPORT_W = 300      # export waarboven overschot-assist mag starten
ASSIST_STOP_W = 150        # hysterese: daaronder stopt de assist
ASSIST_THROTTLE_S = 30     # minimale tijd tussen assist-beslissingen
ASSIST_SOC_MARGE_KWH = 0.15
UPDATE_MINUTES = 5         # her-plan interval
DAGLICHT = (7, 21)         # uren waarbinnen de PV-bel wordt verdeeld

# watchdog / robuustheid
WATCH_FRESH_S = 180        # meetwaarde ouder dan dit telt niet als bewijs
WATCH_RUNAWAY_W = 300      # accuvermogen boven dit zonder opdracht = runaway
GEENDATA_STOP_S = 600      # telemetrie zo lang stil met sturing aan -> veilig stoppen

# agressiviteit = plannings-slijtagegewicht (€/kWh doorzet): lager gewicht =
# cyclen op kleinere prijsspreads. "gebalanceerd" is de getrainde waarde.
AGGRO_LEVELS = {"rustig": 0.05, "gebalanceerd": 0.02, "agressief": 0.005}
AGGRO_DEFAULT = "gebalanceerd"
