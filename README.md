<div align="center">

<img src="logo.svg" width="220" alt="Wattson logo">

# Wattson

**Slimme thuisaccu-sturing voor Home Assistant**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-2FD3FF.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![version](https://img.shields.io/badge/version-1.2.0-00E5A8.svg?style=for-the-badge)](#)
[![license](https://img.shields.io/badge/license-MIT-2FD3FF.svg?style=for-the-badge)](#)
[![maintained](https://img.shields.io/badge/maintained-yes-00E5A8.svg?style=for-the-badge)](#)

</div>

---

Wattson plant, elke paar minuten, het optimale gebruik van je thuisaccu op basis
van dynamische energieprijzen — en stuurt 'm, als je dat wilt, ook echt aan.
Geen zwarte doos: elk advies is het resultaat van een expliciete, uitlegbare
berekening die je live kunt volgen in Home Assistant.

## Wat het doet

- **DP-planner.** Een rolling-horizon dynamic-programming planner rekent, per
  uur in de prijs-forecast, het goedkoopste pad door: laden op de goedkope
  uren, ontladen op de dure — begrensd door accucapaciteit, laad-/ontlaadvermogen
  en een "waarde van morgen" zodat de accu niet leegdumpt op een matige piek.
- **Nooit exporteren.** Ontladen wordt per uur gemaximeerd op de huisvraag; de
  accu levert nooit terug aan het net.
- **EV-blokkade.** Zodra de wallbox (of auto) laadt, wordt ontladen direct
  gestopt — de auto mag nooit uit de accu geladen worden.
- **Schaduwmodus.** Standaard publiceert Wattson alleen het advies (`rust` /
  `laden` / `ontladen`) zonder ook maar iets aan te sturen. Zet de master-switch
  aan zodra je 'm vertrouwt.
- **Walk-forward gevalideerd.** De planner-parameters (laadprofiel, PV-bias,
  degradatiekosten) zijn getraind en gevalideerd op historische data met een
  walk-forward backtest — niet zomaar losse getallen.
- **Uitlegbaar.** Elke advies-sensor toont het volledige plan (tijd, prijs,
  setpoint, verwachte SoC) en de inputs waarmee gerekend is.

## Installatie via HACS

1. HACS → **Integraties** → menu (⋮) rechtsboven → **Custom repositories**.
2. Voeg deze repository-URL toe, categorie **Integration**.
3. Zoek naar **Wattson** in HACS en installeer.
4. Herstart Home Assistant.
5. **Instellingen → Apparaten & diensten → Integratie toevoegen** → zoek
   **Wattson**.

Er is maar één instantie van Wattson nodig (één accu, één planner).

## Configuratie

Bij het toevoegen wordt de integratie meteen aangemaakt met de standaard
bron-entiteiten. Klik daarna op **Opties** bij de Wattson-integratie om de
entiteiten aan te passen aan jouw installatie:

| Optie | Betekenis |
|---|---|
| Prijs-sensor | dynamisch tarief, met een `forecast`-attribuut (uur-vooruitblik) |
| SoC-sensor | laadniveau van de accu in % |
| P1-meter | huidig vermogen op de hoofdmeter |
| Wallbox 1 / 2 | laadvermogen van je EV-lader(s), optioneel |
| PV nu / rest vandaag / morgen | actuele en voorspelde zonproductie |
| Accu operation-select | de modus-entiteit van je accu (bv. `off` / `manual` / `smart_discharging`) |
| Accu handmatig vermogen | de `number`-entiteit voor handmatig laad-/ontlaadvermogen |
| Accu HEMS/AI-status | `binary_sensor` die aangeeft of de accu's eigen AI-modus actief is (Wattson wijkt dan uit) |
| Accu laad-/ontlaadvermogen | optionele sensoren om het eigen accuvermogen uit de gemeten huisvraag te filteren |

### Entiteiten die Wattson aanmaakt

- `sensor.wattson_advies` — huidig advies (`laden` / `ontladen` / `rust`),
  met het volledige plan en de rekenparameters als attributen.
- `sensor.wattson_verwachte_besparing` — verwachte besparing (€) over de
  planningshorizon t.o.v. geen accu.
- `switch.wattson_sturing` — master-switch. Uit = alleen advies (schaduwmodus).
  Aan = Wattson stuurt de accu daadwerkelijk aan.

## Wattson-kaart

Een kleine, dependency-vrije Lovelace-kaart die het advies, setpoint, SoC en
een mini-balkgrafiekje van het plan toont.

**Resource toevoegen** (Instellingen → Dashboards → resources, of via YAML):

```yaml
resources:
  - url: /hacsfiles/wattson/wattson-card.js
    type: module
```

**Kaart-configuratie:**

```yaml
type: custom:wattson-card
entity: sensor.wattson_advies   # optioneel, dit is de default
title: Wattson                 # optioneel
hours: 12                       # optioneel, aantal uren in de mini-grafiek
```

## Wattson-thema

Een donker Home Assistant-thema met electric-blue/teal accenten, passend bij
het logo. Kopieer [`themes/wattson.yaml`](themes/wattson.yaml) naar je
`themes/`-map en selecteer **Wattson** bij Instellingen → Weergave → Thema.

## FAQ

**Werkt dit met elke accu?**
Wattson is gebouwd rond een generiek "operation-select + manual-power number"
patroon (zoals bij Zendure Solarflow). Andere merken werken als je de
Opties-entiteiten naar vergelijkbare bediening kunt wijzen; anders kun je
Wattson in schaduwmodus laten draaien en het advies zelf verwerken in een
eigen automatisering.

**Wat als de accu's eigen AI/HEMS-modus aanstaat?**
Wattson herkent dat via de HEMS-status-entiteit en stuurt dan niets aan (het
advies blijft wel zichtbaar) om dubbele sturing te voorkomen.

**Hoe vaak wordt er opnieuw gepland?**
Elke 5 minuten, en direct wanneer je de master-switch aanzet.

**Kan de accu naar het net exporteren?**
Nee. Ontladen wordt hard begrensd op de gemeten huisvraag.

## Disclaimer

Wattson is gebouwd voor persoonlijk gebruik en zonder enige garantie. De
planner rekent met een model van jouw huis, jouw accu en de prijzen —
verkeerde brongegevens (SoC, prijs-forecast, vermogens) leiden tot een
verkeerd advies of verkeerde sturing. Gebruik de schaduwmodus om te wennen
voordat je de master-switch aanzet, en controleer regelmatig of de sturing
doet wat je verwacht. Geen enkele garantie t.a.v. besparing, accu-levensduur
of correcte werking van gekoppelde apparaten.

---

<div align="center">
<sub>Gebouwd met een pure-Python rolling-horizon planner — geen cloud, geen dependencies.</sub>
</div>


## Andere accumerken (v1.1+)

Wattson is merk-onafhankelijk. Bij het toevoegen kies je een **adapter**:

- **zendure** — stuurt de [Zendure-HA-integratie](https://github.com/FireSon/Zendure-HA) aan
  (manual/smart_discharging/off; ontladen via de P1-matching van het apparaat zelf).
- **marstek** — voor een Marstek Venus E/A/D via RS485-modbus (bijv. een ESP32 met de
  [LilyGO-ESPHome-config](https://github.com/whyisthisbroken/marstek-lilygo-rs485) of de
  [HA-modbus-config](https://github.com/reschcloud/marstek_venus_e_modbus_home_assistant)).
  Je geeft drie entiteiten op: de force-mode (een `select` met stop/charge/discharge-opties
  óf een `number` op register 42010: 0=stop, 1=laden, 2=ontladen), het forcible-laadvermogen
  en het forcible-ontlaadvermogen. Let op: de **RS485 control mode** van de Venus moet aan
  staan, anders accepteert hij geen commando's. Ontladen wordt door Wattson begrensd op de
  actuele netto-import (P1).
- **generic** — werkt met elk merk dat via een integratie `number`-entiteiten aanbiedt:
  - één *signed* vermogen-number (+W = laden, −W = ontladen), **of**
  - twee losse numbers (laadvermogen en ontlaadvermogen).

  Wattson begrenst ontladen op de actuele netto-import (P1) zodat de accu nooit naar
  het net exporteert; zet daarnaast in de accu-app zelf een export-limiet als die er is.

Accucapaciteit, minimale SoC en maximale laad-/ontlaadvermogens stel je in via
**Opties** op de integratie — de planner schaalt daar automatisch op mee.
