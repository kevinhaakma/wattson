<div align="center">

<img src="logo.svg" width="220" alt="Wattson logo">

# Wattson

**Slimme thuisaccu-sturing voor Home Assistant**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-2FD3FF.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![version](https://img.shields.io/badge/version-1.6.0-00E5A8.svg?style=for-the-badge)](#)
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
  accu levert nooit terug aan het net — tenzij je de aparte verkoop-switch
  aanzet (zie [Verkopen, v1.5](#verkopen-v15)).
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
| Prijs-sensor | dynamisch tarief, met een `forecast`-attribuut (uur-vooruitblik). Ondersteund per forecast-item: Zonneplan (`datetime` + `electricity_price` × 1e7) of generiek (`datetime`/`start`/`from` + `price`/`value` in €/kWh) |
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
- `sensor.wattson_verwachte_besparing` ("verwacht planvoordeel") — kosten van
  niets-doen minus plan-kosten over de horizon, symmetrisch verrekend (zelfde
  start-SoC, zelfde eindwaarde voor restlading). Het voordeel van het plán,
  geen kasstroom-garantie.
- `switch.wattson_sturing` — master-switch. Uit = alleen advies (schaduwmodus).
  Aan = Wattson stuurt de accu daadwerkelijk aan.
- `switch.wattson_bijspringen` — realtime piek/overschot-assist (v1.4).
- `switch.wattson_verkopen` — exporteren boven de drempelprijs (v1.5).

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
Standaard niet: ontladen wordt hard begrensd op de gemeten huisvraag. Alleen
met `switch.wattson_verkopen` aan én een prijs boven de verkoop-drempel mag
de planner bewust exporteren (zie v1.5).

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


## Inzicht in beslissingen (v1.3)

- `sensor.wattson_advies` heeft nu de attributen **reden** (waarom deze actie),
  **volgende_actie** (wat er gepland staat en wanneer) en **historie**
  (laatste 50 beslissingen met tijd, setpoint en reden).
- Elke wijziging verschijnt ook in het **HA-logboek** ("Wattson: ontladen (-420 W) — duur uur (€0.404)...").
- Na een herstart probeert Wattson elke 45 s opnieuw tot de bronnen (prijs/SoC)
  beschikbaar zijn, in plaats van 5 minuten te wachten.
- Alle bron-entiteiten kies je nu uit een **dropdown** in plaats van een tekstveld.


## Dynamisch bijspringen (v1.4)

Met `switch.wattson_bijspringen` aan reageert Wattson realtime (elke ~30 s) bovenop het uurplan:

- **Piek-assist**: schiet het huisverbruik omhoog terwijl het plan "rust" zegt, dan ontlaadt
  de accu mee — maar alleen als de huidige prijs hoger is dan de goedkoopste herlaadprijs
  (round-trip meegerekend) én de **plan-reserve** onaangetast blijft: energie die het plan
  voor de avondpiek heeft gereserveerd wordt nooit opgesnoept.
- **Overschot-assist**: is er PV-overschot, dan wordt dat direct opgeslagen zolang een
  duurder uur in het verschiet ligt (bij Zendure via de native surplus-modus die het
  overschot op de P1 volgt).
- EV-guard blijft altijd voorrang houden; hysterese voorkomt aan/uit-gependel.


## Verkopen (v1.5)

Met `switch.wattson_verkopen` aan (standaard **uit**) mag de planner in uren
waarin de kale verkoopprijs (importprijs minus belasting/opslag) op of boven de
**verkoop-drempel** ligt (optie op de integratie, standaard €0,45/kWh) ontladen
vóórbij de huisvraag — het surplus gaat dan tegen de exportprijs het net op.
Dit is de enige uitzondering op de "nooit exporteren"-regel:

- Het advies toont dan `verkopen`; bij Zendure wordt een vast ontlaadvermogen
  gezet (manual) in plaats van P1-matching.
- De EV-guard houdt voorrang: zodra de auto laadt stopt ook verkopen direct.
- `sensor.wattson_advies` krijgt het attribuut **verkopen**:
  `actief` / `gewapend (drempel €…)` / `uit`.

### Robuustere beveiliging (v1.5)

- De **watchdog** oordeelt alleen nog op *verse* meetwaarden (jonger dan 3 min);
  een bevroren of unavailable sensor triggert geen noodstop, maar heft er ook
  geen op.
- Een noodstop zet gericht de limiet van de foute richting dicht (laden →
  input-limiet, ontladen → output-limiet) — op apparaatniveau, dus ook als de
  operation-select al "off" toont. Na een noodstop opent de eerstvolgende
  echte actie alleen de limiet die hij zelf nodig heeft.
- **Stale-guard**: is álle telemetrie langer dan 10 min stil terwijl sturing
  aan staat, dan gaat de accu eenmalig naar de veilige stand met beide
  limieten op 0.
- **Rust-handhaving**: meet de accu aantoonbaar activiteit terwijl rust
  gecommandeerd is, dan gaan beide apparaat-limieten direct naar 0.
- Limiet-waarden worden geclampt op het min/max van de number-entiteit, zodat
  een te hoge waarde de planning-tick niet meer laat falen.


## Multi-brand hard gemaakt (v1.6)

Alle veiligheidsranden die eerst alleen op Zendure klopten, gelden nu voor
elk accumerk:

- **Noodstops via de adapter-router.** Watchdog en stale-guard commanderen
  `rust` in de taal van de geconfigureerde adapter; bij Zendure gaan daarna
  gericht de apparaat-limieten dicht. De ingreep wordt geregistreerd en
  gelogd vóórdat het stopcommando loopt.
- **Telemetrie per adapter.** Voor marstek/generic zijn optionele laad-/
  ontlaadvermogen-sensoren toe te wijzen; daarmee werken de watchdog en de
  huislast-correctie ook daar. Zonder die sensoren velt de watchdog bewust
  géén oordeel.
- **Zendure-limiet-entiteiten instelbaar.** De input/output-limiet-numbers
  staan nu in de opties in plaats van hardcoded defaults; de output-limiet
  wordt bij ontladen bovendien begrensd op het geplande setpoint.
- **Altijd actieve discharge-guard (marstek/generic).** Het vaste
  ontlaadvermogen wordt realtime verlaagd zodra de P1 export toont
  (throttled, verlaagt alleen; verhogen doet de volgende plan-tick). De
  "nooit exporteren"-belofte hangt daar dus niet meer aan één momentopname.
- **Lifecycle.** Bij unload/reload van de integratie gaat de accu naar rust
  en wordt de herstart-retry netjes geannuleerd.
- **Validatie in de options-flow.** Verplichte stuur-entiteiten blijven
  verplicht, optionele velden zijn wisbaar, numerieke waarden en de
  generic-configuratie worden gecontroleerd met duidelijke foutmeldingen.
- **Tijd en prijsbron.** Alle lokale-tijd-logica gebruikt de HA-tijdzone
  (niet de host-OS-tijdzone); de prijs-parser accepteert naast Zonneplan ook
  generieke forecast-formaten, en een forecast die pas bij het volgende uur
  begint wordt aangevuld met de actuele prijs voor het huidige uur.
- **Eerlijker besparingscijfer.** Het besparing-sensor heet nu "verwacht
  planvoordeel" en rekent symmetrisch (zelfde start-SoC en eindwaarde voor
  beide paden); de entity-id blijft ongewijzigd.
