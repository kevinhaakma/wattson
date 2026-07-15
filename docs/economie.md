# Economische parameters — herkomst en validatie

Gevalideerd 2026-07-15. Elke parameter hieronder is gelabeld **gemeten** (uit eigen
data), **onderzocht** (externe bronnen) of **aanname** (nog niet hard te maken).

## 1. Omzetverlies en standby — gemeten

Massabalans over 179 uur eigen Zendure-tellers (laad-, ontlaad- en SoC-verloop):
gemeten verlies 4,36 kWh, model voorspelt 4,34 kWh met:

| parameter | waarde | betekenis |
|---|---|---|
| `eta_nom` | 0,955 | rendement per richting → round-trip ≈ 0,91 |
| `p_fix_w` | 0 | geen vaste vermogensstraf meetbaar |
| `standby_w` | 6 | continu eigenverbruik, ook in rust |

Geen datasheetwaarden; gefit op dit specifieke apparaat.

## 2. Terugleververgoeding onder saldering — gemeten + onderzocht

**Meting** (8 dagen, 3,26 kWh export, Zonneplan-sensoren `electricity_returned_today`
vs `electricity_production_costs_today` per uur naast het uurtarief):

- gemiddelde vergoeding **€0,240/kWh** tegen kWh-gewogen tarief **€0,231** op dezelfde uren
- werkelijke wedge: **≈ €0,00** (licht negatief, −0,009)

**Verklaring** (Zonneplan-beleid, 2026): teruglevering krijgt de kale kwartier-/uurprijs
**plus €0,02/kWh inkoopvergoeding plus 10% Zonnebonus** (overdag, ≤ 7.500 kWh/jr);
de belastingcomponent wordt op jaarbasis gesaldeerd zolang jaarexport ≤ jaarimport.
Zonneplan rekent géén terugleverkosten.

**Consequentie:** onder saldering is zonoverschot nooit "gratis" — terugleveren
levert het volle tarief op. De accu verdient tot 2027 uitsluitend aan
**prijsspreads** (goedkoop laden, duur ontladen); eigenverbruik-optimalisatie op
zich heeft waarde ≈ 0. Was in de trainer aangenomen als €0,02 → moet **€0,00**.

**Kanttekening:** de jaarsaldering geldt tot het eigen jaarverbruik; structureel
netto-exporteurs vallen voor het meerdere terug op kale prijs + bonus.

## 3. Wedge na 1-1-2027 — onderzocht

Saldering stopt volledig per 2027-01-01 (geen afbouwpad). Export wordt dan kale
prijs (+ Zonneplan-bonus); import houdt energiebelasting + btw (≈ €0,111/kWh in 2026).

- verwachte wedge bij Zonneplan: **€0,08–0,12/kWh** (bonus dempt) → default
  `wedge_post_saldering = 0,10` is gevalideerd
- wettelijk minimum voor alle leveranciers (tot 2030): ≥ 50% van het kale leveringstarief
- de Zonneplan-prijssensor levert per uur ook `electricity_price_excl_tax`; de
  post-2027 exportprijs kan te zijner tijd dus **exact per uur** berekend worden
  (excl_tax + 0,02, overdag ×1,10) in plaats van met een vaste wedge

## 4. Slijtage (`deg_cost`) — onderzocht, blijft deels aanname

Actuele feiten (2025/2026): 3× AB2000S ≈ €1.980 (≈ €344/kWh capaciteit); spec
3.000 cycli tot 80% of 6.000 tot 70%; garantie 10 jaar. Gemeten gebruik hier:
**≈ 0,85 cyclus/dag** ≈ 3.100 cycli in 10 jaar — vrijwel exact op de 3.000-cyclusgrens.

| perspectief | €/kWh doorzet |
|---|---|
| naïef: capex / rated cycli | 0,06–0,12 |
| kalender-gelimiteerd gemiddelde (capex / werkelijke doorzet in 10 jr) | 0,09–0,16 |
| **marginaal** (extra cyclus binnen kalenderlevensduur) | **0,00–0,03** |

Omdat het gebruik al óp het omslagpunt zit waar extra cycli echt levensduur
kosten, is **€0,03** de best verdedigbare marginale waarde; de gebruikte €0,04
is licht conservatief. `DEG_TRUE` in de trainer: 0,04 → **0,03** aanbevolen.

## 5. Architectuurconsequentie: zelfverbruik vs. arbitrage

De planner is gebouwd als **eigenverbruik-optimalisator**: ontladen is hard
begrensd op de huisvraag (`planner.hour_result`, sell alleen boven
`verkoop_drempel` €0,45). Met wedge ≈ 0 is dat economisch te smal:

- exporteren om 21:00 tegen €0,36 is exact evenveel waard als de huisvraag dekken
- de correcte strategie onder saldering is **volledige prijsarbitrage**: laden op
  vol vermogen in de goedkoopste uren, ontladen op vol vermogen in de duurste
  uren, surplus het net op — de huisvraag is alleen nog een bijzaak
- de verkoop-drempel van €0,45 blokkeert deze strategie vrijwel permanent
- ná 2027 (wedge ≈ 0,10) wordt de huidige zelfverbruik-architectuur wél weer
  grotendeels correct

Ruwe schatting gemiste waarde onder saldering: volle-capaciteit-arbitrage
(≈ 5,2 kWh × netto spread ≈ €0,09/kWh op een dag als vandaag) t.o.v. huidig
avondtekort-dekken (≈ 2,2 kWh) ≈ **€0,2–0,3/dag** op dagen met ≥ €0,15 spread —
zelfde orde van grootte als de totale huidige backtest-waarde (€49/jr).
Kanttekeningen: meer doorzet → hogere marginale slijtage (regime kantelt boven
~1 cyclus/dag), en apparaat-uitvoer is nu op 1400 W begrensd (Zendure-app-instelling;
hardware kan 2400 W).

## Openstaande verificaties

- verrekent Zonneplan werkelijk per kwartier (EPEX 15-min MTU) terwijl de
  HA-sensor uurprijzen levert? Zo ja, dan mist de planner intra-uur spread
- exacte energiebelasting 2027 + Zonneplan-marges zijn nog niet vastgesteld
