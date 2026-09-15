/**
 * wattson-v3-card — één Wattson-kaart (vervangt brain- + tree-card).
 *
 * v3.1 (2026-08-14): uurplan-timeline (balken + prijslijn + SoC-curve),
 * 'echt'-teller in de kop (sensor.wattson_gerealiseerd).
 *
 * De beslisboom is het hart: zes niveaus als rustige segmented rows, de
 * actieve optie licht op en één dunne energiedraad volgt het pad van boven
 * naar beneden. Daaronder de prijszone-meter (λ-regel in één oogopslag),
 * het uurplan en de accu/net-strip. Tik op de kaart → de afweging,
 * plan-meta en saldering. Bij een fout klapt hij vanzelf open.
 *
 * v3.5 (2026-09-06, skills-review): Hûs-tokens met fallbacks i.p.v. kale
 * kleuren, getGridOptions voor sections-views, visuele editor (ha-form),
 * entity-change-detectie in de hass-setter (geen herrender per state-tick
 * van het hele huis), SoC in het uurplan als eigen band i.p.v. tweede as
 * over de balken, tooltip per planuur, proportionele cijfers in de held,
 * ontlaadkleur #3fd4b0 (CVD-gevalideerd tegen amber: ΔE 8,5 protan;
 * het oude #4cc88a haalde 4,3).
 *
 * v3.6 (2026-09-15): uurplan ruimer: bandlabels, elk uur gelabeld, kW en
 * prijs per uur als tekst, kolomtint bij actie, SoC begin/eind in %, en een
 * samenvattingsregel (laden/leveren kWh, uren, gemiddelde prijs). Held rechts toont 'vandaag' (gerealiseerd) en
 * 'verwacht' (planvoordeel over de horizon) als twee gelijkwaardige
 * getallen met eigen label en subregel, i.p.v. het plan als klein bijschrift.
 *
 * v3.7 (2026-09-15): merk-onafhankelijk. SoC, accu- en netvermogen komen
 * standaard uit `berekend_met` van de advies-sensor (Wattson kent ze al);
 * losse entiteiten zijn alleen nog een optionele override. Het apparaat-
 * niveau leest de gestuurde actie (laatst_gestuurd) i.p.v. de Zendure-
 * operation-select, zodat de kaart ook op Marstek/generic klopt. Eerder
 * stonden hier de entity-id's van de ontwikkelaar als default: op een
 * andere installatie bleven SoC, vermogen en net leeg.
 *
 * type: custom:wattson-v3-card           # alleen entity is nodig
 * Stijl: overzicht "glass" (--ov-*-look), geen emojis, build-once/patch.
 */
const WT_ENTITY_KEYS = ["entity", "besparing", "soc", "chg_w", "dis_w", "p1", "mode",
  "agressiviteit", "gerealiseerd", "sw_sturing", "sw_assist", "sw_sell"];
const WT_PAL = {
  laden: "#ffb86b", ontladen: "#3fd4b0", verkopen: "#7c9cf5",
  err: "#ff6b81", sage: "#a7ada2", soc: "rgba(255,255,255,.85)",
};

class WattsonV3Card extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: "sensor.wattson_advies",
      besparing: "sensor.wattson_verwachte_besparing",
      soc: "",        // optioneel; standaard berekend_met.soc_pct
      chg_w: "",      // optioneel; standaard berekend_met.accu_laden_w
      dis_w: "",      // optioneel; standaard berekend_met.accu_ontladen_w
      p1: "",         // optioneel; standaard berekend_met.p1_nu_w
      mode: "",       // optioneel (Zendure operation-select); standaard laatst_gestuurd
      agressiviteit: "select.wattson_agressiviteit",
      gerealiseerd: "sensor.wattson_gerealiseerd",
      sw_sturing: "switch.wattson_sturing",
      sw_assist: "switch.wattson_bijspringen",
      sw_sell: "switch.wattson_verkopen",
      ...(config || {}),
    };
    this._built = false;
    this._open = false;
    this._autoOpened = false;
  }

  getCardSize() { return 8; }
  // sections-view: inhoudsafhankelijke hoogte, halve sectiebreedte als default
  getGridOptions() { return { columns: 6, min_columns: 4, rows: "auto" }; }
  getLayoutOptions() { return { grid_columns: 6, grid_min_columns: 4, grid_rows: "auto" }; }
  static getStubConfig() { return {}; }
  static getConfigElement() { return document.createElement("wattson-v3-card-editor"); }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    // alleen herrenderen als een van de eigen entiteiten écht wijzigde
    const key = WT_ENTITY_KEYS.map((k) => {
      const st = hass.states[this._config[k]];
      return st ? st.last_updated : "";
    }).join("|");
    if (key === this._hassKey) return;
    this._hassKey = key;
    this._update();
  }

  connectedCallback() {
    if (!this._ro && "ResizeObserver" in window) {
      this._ro = new ResizeObserver(() => this._wire());
      this._ro.observe(this);
    }
  }
  disconnectedCallback() { if (this._ro) { this._ro.disconnect(); this._ro = null; } }

  _st(id) { return id && this._hass && this._hass.states[id]; }
  // entiteit als die geconfigureerd is en bestaat, anders de waarde uit berekend_met
  _numOr(id, fallback) {
    if (id) {
      const v = this._num(id);
      if (v !== null) return v;
    }
    return typeof fallback === "number" && Number.isFinite(fallback) ? fallback : null;
  }
  // apparaatniveau uit wat Wattson werkelijk stuurde: merk-onafhankelijk.
  // Zendure: "manual (-2000 W)" (negatief = laden), "smart_charging", "off";
  // Marstek/generic: "laden (1250 W, marstek)", "ontladen (…)", "rust".
  static _deviceId(gestuurd, chg, dis) {
    const g = String(gestuurd || "").toLowerCase();
    if (/smart_charging|laden_overschot/.test(g)) return "zon";
    if (/smart_discharging|ontladen|verkopen/.test(g)) return "ontladen";
    if (/manual \(-|\bladen/.test(g)) return "laden";
    if (/manual \(\+?\d/.test(g)) return "ontladen";
    if ((chg || 0) > 50) return "laden";
    if ((dis || 0) > 50) return "ontladen";
    return "rust";
  }
  _num(id) {
    const s = this._st(id);
    const v = s && parseFloat(s.state);
    return Number.isFinite(v) ? v : null;
  }
  static _nl(v, dec = 0) {
    return v === null || v === undefined || !Number.isFinite(v) ? "–"
      : v.toLocaleString("nl-NL", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  static _ct(v) {
    return v === null || v === undefined || !Number.isFinite(v) ? "–"
      : WattsonV3Card._nl(v * 100, 1) + " ct";
  }

  static get LEVELS() {
    return [
      { key: "sturing", label: "sturing", opts: [
        { id: "actief", t: "actief" }, { id: "schaduw", t: "schaduw" }] },
      { key: "gates", label: "veiligheid", gates: true, opts: [
        { id: "data", t: "data" }, { id: "watchdog", t: "watchdog" },
        { id: "stale", t: "meting" }, { id: "ev", t: "EV" }] },
      { key: "aggro", label: "doel", opts: [
        { id: "rustig", t: "rustig" }, { id: "gebalanceerd", t: "gebalanceerd" },
        { id: "agressief", t: "agressief" }] },
      { key: "plan", label: "plan", opts: [
        { id: "laden_net", t: "net" }, { id: "laden_zon", t: "zon" },
        { id: "rust", t: "rust" }, { id: "ontladen", t: "leveren" },
        { id: "verkopen", t: "verkoop" }] },
      { key: "rt", label: "realtime", opts: [
        { id: "assist_laden", t: "extra laden" }, { id: "volgt", t: "volgt plan" },
        { id: "assist_ontladen", t: "extra leveren" }] },
      { key: "device", label: "apparaat", opts: [
        { id: "rust", t: "rust" }, { id: "laden", t: "laden" },
        { id: "zon", t: "zon-laden" },
        { id: "ontladen", t: "leveren" }] },
    ];
  }

  _build() {
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    const lvl = (L) => `
      <div class="lvl" data-lvl="${L.key}">
        <span class="lt">${L.label}</span>
        <div class="track${L.gates ? " gates" : ""}">
          ${L.opts.map((o, i) => `<span class="opt" data-opt="${o.id}" data-i="${i}">${o.t}</span>`).join("")}
        </div>
      </div>`;
    root.innerHTML = `
      <style>
        :host { display:block;
          --wt-laden: var(--hus-amber, #ffb86b);
          --wt-ontladen: #3fd4b0;
          --wt-verkopen: #7c9cf5;
          --wt-err: var(--hus-err, #ff6b81);
          --wt-sage: var(--hus-sage, #a7ada2);
          --wt-text: var(--hus-text, rgba(255,255,255,.94));
          --wt-text-muted: var(--hus-text-muted, rgba(255,255,255,.56));
          --wt-text-dim: var(--hus-text-dim, rgba(255,255,255,.34));
          --wt-border: var(--hus-border, rgba(226,224,212,.13));
          --wt-border-soft: var(--hus-border-soft, rgba(226,224,212,.075));
          --wt-surface: var(--hus-surface, rgba(24,28,34,.42));
          --wt-radius: var(--hus-radius-card, 8px);
        }
        ha-card {
          position: relative;
          background:
            radial-gradient(120% 70% at 85% -10%, rgba(255,184,107,.16), transparent 62%),
            radial-gradient(110% 85% at -10% 112%, rgba(63,212,176,.13), transparent 64%),
            var(--wt-surface);
          -webkit-backdrop-filter: blur(18px) saturate(1.25);
          backdrop-filter: blur(18px) saturate(1.25);
          border: 1px solid var(--wt-border);
          border-radius: var(--wt-radius);
          box-shadow: 0 1px 0 rgba(255,255,255,.03) inset, 0 6px 20px rgba(0,0,0,.18);
          color: var(--wt-text);
          padding: 16px;
          font: 400 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
          cursor: pointer;
          -webkit-tap-highlight-color: transparent;
          transition: transform .15s ease;
        }
        /* liquid-glass: een lichtwas over de bovenrand, alsof het glas licht vangt */
        ha-card::before {
          content:""; position:absolute; top:0; left:0; right:0; height:110px;
          background:radial-gradient(60% 100% at 50% 0%, rgba(255,255,255,.11), transparent 72%);
          border-radius:8px 8px 0 0;
          pointer-events:none;
        }
        ha-card:active { transform:scale(.987); }
        .head { display:flex; align-items:center; gap:8px; margin-bottom:14px; }

        /* hero: SoC-halo + advies + euro's vandaag */
        .hero { display:grid; grid-template-columns:88px minmax(0,1fr) auto; gap:16px;
                align-items:center; margin:2px 0 14px; padding-bottom:14px;
                border-bottom:1px solid rgba(226,224,212,.075); }
        .ring { position:relative; width:88px; height:88px; border-radius:50%;
                transition:box-shadow .6s ease; }
        .ring svg { width:88px; height:88px; transform:rotate(-90deg); display:block; }
        .rbg { fill:none; stroke:rgba(255,255,255,.07); stroke-width:5; }
        .rfg { fill:none; stroke:#a7ada2; stroke-width:5; stroke-linecap:round;
               stroke-dasharray:238.76; stroke-dashoffset:238.76;
               transition:stroke-dashoffset .9s cubic-bezier(.4,0,.2,1), stroke .4s, filter .4s;
               filter:drop-shadow(0 0 2px rgba(167,173,162,.7)) drop-shadow(0 0 9px rgba(167,173,162,.3));
               animation:breathe 4.5s ease-in-out infinite; }
        @keyframes breathe { 0%,100% { opacity:1; } 50% { opacity:.72; } }
        .rtxt { position:absolute; inset:0; display:flex; flex-direction:column;
                align-items:center; justify-content:center; }
        .rp { font-size:26px; font-weight:200; line-height:1; color:rgba(255,255,255,.96);
              letter-spacing:-.01em; }
        .rp small { font-size:11px; font-weight:400; color:rgba(255,255,255,.45); margin-left:1px; }
        .rk { font-size:9.5px; color:rgba(255,255,255,.40); margin-top:2px; }
        .hlbl { font-size:9px; font-weight:600; letter-spacing:.14em; text-transform:uppercase;
                color:rgba(255,255,255,.30); margin-bottom:3px; }
        .hstate { font-size:30px; font-weight:200; line-height:1.05; letter-spacing:-.01em;
                  background:linear-gradient(115deg, rgba(255,255,255,.97) 30%, #a7ada2 92%);
                  -webkit-background-clip:text; background-clip:text; color:transparent;
                  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .hstate.laden    { background-image:linear-gradient(115deg, rgba(255,255,255,.97) 25%, #ffb86b 85%); }
        .hstate.ontladen { background-image:linear-gradient(115deg, rgba(255,255,255,.97) 25%, #3fd4b0 85%); }
        .hstate.fout     { background-image:linear-gradient(115deg, rgba(255,255,255,.97) 25%, #ff6b81 85%); }
        .hsub { font-size:10.5px; color:rgba(255,255,255,.42); margin-top:3px;
                overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .hright { display:flex; gap:20px; align-items:flex-start; }
        .kpi { text-align:right; min-width:0; }
        .hnum { font-size:24px; font-weight:200; line-height:1.05; color:var(--wt-text);
                font-variant-numeric:tabular-nums; white-space:nowrap; }
        .hnum::before { content:""; display:inline-block; width:7px; height:7px; border-radius:50%;
                        background:var(--wt-ontladen); margin-right:7px; vertical-align:3px; }
        .hnum.neg::before { background:var(--wt-err); }
        .hnum.plan { color:rgba(255,255,255,.78); }
        .hnum.plan::before { background:transparent; box-shadow:inset 0 0 0 1.5px var(--wt-ontladen); }
        .hnum.plan.neg::before { box-shadow:inset 0 0 0 1.5px var(--wt-err); }
        .hnum.plan.zero::before { box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.35); }
        .head ha-icon { --mdc-icon-size:17px; color:#a7ada2; }
        .head .t { font-size:12px; font-weight:600; letter-spacing:.12em;
                   text-transform:uppercase; color:rgba(255,255,255,.56); flex:1; }

        /* de boom: zes segmented rows + energiedraad */
        .tree { position:relative; }
        svg.wires { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
        .lvl { display:grid; grid-template-columns:64px 1fr; align-items:center;
               gap:10px; padding:5px 0; position:relative; }
        .lt { font-size:9.5px; font-weight:600; letter-spacing:.13em; text-transform:uppercase;
              color:rgba(255,255,255,.30); text-align:right; }
        .track { display:flex; border-radius:8px; overflow:hidden; position:relative; z-index:1;
                 background:rgba(255,255,255,.035); border:1px solid rgba(226,224,212,.07); }
        .opt { flex:1; text-align:center; font-size:10.5px; padding:5px 3px;
               color:rgba(255,255,255,.34); white-space:nowrap; overflow:hidden;
               text-overflow:ellipsis; border-left:1px solid rgba(226,224,212,.06);
               transition:background .35s ease, color .35s ease; }
        .opt:first-child { border-left:none; }
        .opt.active { color:rgba(255,255,255,.97); font-weight:600;
                      background:rgba(255,255,255,.10); }
        .opt.active.tint-amber { color:#ffcf9b; background:rgba(255,184,107,.16); }
        .opt.active.tint-green { color:#8ae6cb; background:rgba(63,212,176,.16); }
        .opt.active.tint-red   { color:#ff8fa0; background:rgba(255,107,129,.16); }
        .track.gates .opt.ok { color:rgba(140,205,170,.52); }
        .track.gates .opt.blocked { color:#ff8fa0; font-weight:600; background:rgba(255,107,129,.16); }
        .sub { grid-column:2; font-size:10.5px; color:rgba(255,255,255,.38); margin-top:2px;
               overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        @keyframes flow { to { stroke-dashoffset:-24; } }
        path.flowline { stroke-dasharray:4 8; animation:flow 2.2s linear infinite; }

        /* korte lichtpuls op een waarde die écht wisselt (SoC, advies, euro's) */
        @keyframes valFlash { 0% { filter:drop-shadow(0 0 0 rgba(255,255,255,0)); }
                               35% { filter:drop-shadow(0 0 7px rgba(255,255,255,.55)); }
                               100% { filter:drop-shadow(0 0 0 rgba(255,255,255,0)); } }
        .flash { animation:valFlash .8s ease; }

        /* prijszone-meter */
        .gbar { position:relative; height:16px; border-radius:5px; margin:22px 0 5px;
                background:rgba(255,255,255,.05); }
        .gz { position:absolute; top:0; bottom:0; }
        .gz-laden { left:0; background:linear-gradient(90deg, rgba(255,184,107,.30), rgba(255,184,107,.08));
                    border-radius:5px 0 0 5px; }
        .gz-ontladen { right:0; background:linear-gradient(90deg, rgba(63,212,176,.08), rgba(63,212,176,.30));
                       border-radius:0 5px 5px 0; }
        .needle { position:absolute; top:-4px; bottom:-4px; width:2px; border-radius:1px;
                  background:#fff; box-shadow:0 0 8px rgba(255,255,255,.7);
                  transition:left .8s cubic-bezier(.4,0,.2,1); }
        .needle::after { content:attr(data-label); position:absolute; top:-14px; left:50%;
                  transform:translateX(-50%); font-size:10px; font-weight:600; color:#fff;
                  white-space:nowrap; }
        .lamtick { position:absolute; top:2px; bottom:2px; width:1px;
                   background:rgba(255,255,255,.40); transition:left .8s ease; }
        .gl { display:flex; justify-content:space-between; font-size:9.5px; font-weight:600;
              letter-spacing:.11em; text-transform:uppercase; color:rgba(255,255,255,.30); }

        /* uurplan-timeline */
        .tl { margin:16px 0 0; }
        .tl svg { width:100%; display:block; }
        .tlcap { display:flex; justify-content:space-between; align-items:baseline;
                 font-size:9.5px; font-weight:600; letter-spacing:.11em;
                 text-transform:uppercase; color:rgba(255,255,255,.30); margin-bottom:3px; }
        .tlcap .leg { display:flex; gap:12px; font-weight:400; letter-spacing:.02em;
                      text-transform:none; font-size:10px; color:rgba(255,255,255,.38); }
        .tlsum { font-size:11px; color:rgba(255,255,255,.55); margin-top:6px; line-height:1.4; }
        .tlsum b { font-weight:600; }
        .tlsum b.c { color:var(--wt-laden); }
        .tlsum b.d { color:var(--wt-ontladen); }
        .leg i { display:inline-block; width:8px; height:8px; border-radius:2px;
                 margin-right:4px; vertical-align:-1px; font-style:normal; }

        /* accu/net-strip */
        .strip { display:flex; align-items:center; gap:14px; padding-top:12px; margin-top:10px;
                 border-top:1px solid rgba(226,224,212,.075); }
        .bt { flex:1; font-size:12px; color:rgba(255,255,255,.86); font-weight:600; }
        .bt small { display:block; font-weight:400; font-size:10.5px; color:rgba(255,255,255,.42); }
        .netkv { text-align:right; font-size:12px; color:rgba(255,255,255,.86); font-weight:600; }
        .netkv small { display:block; font-weight:400; font-size:10.5px; color:rgba(255,255,255,.42); }
        .chev { --mdc-icon-size:16px; color:rgba(255,255,255,.30);
                transition:transform .3s ease; flex:none; }
        :host([data-open]) .chev { transform:rotate(180deg); }

        /* detail: nul-voetafdruk dicht, klapt open (climate-card-patroon) */
        .dwrap { display:grid; grid-template-rows:0fr; transition:grid-template-rows .35s ease; }
        :host([data-open]) .dwrap { grid-template-rows:1fr; }
        .dinner { overflow:hidden; min-height:0; }
        .detail { padding-top:12px; }
        .sec { font-size:10px; font-weight:600; letter-spacing:.14em; text-transform:uppercase;
               color:rgba(255,255,255,.34); margin:10px 0 5px; }
        .sec:first-child { margin-top:0; }
        .afw-row { display:grid; grid-template-columns:118px auto 1fr; align-items:baseline;
                   gap:10px; padding:2px 0; }
        .afw-row .l { color:rgba(255,255,255,.42); font-size:11.5px; }
        .afw-row .v { color:rgba(255,255,255,.94); font-weight:500; white-space:nowrap; }
        .afw-row .x { color:rgba(255,255,255,.34); font-size:11px; overflow:hidden;
                      text-overflow:ellipsis; white-space:nowrap; }
        .afw-row.win .v { color:var(--wt-ontladen); }
        .meta { font-size:11px; color:rgba(255,255,255,.42); margin-top:2px; }
        .meta b { color:rgba(255,255,255,.72); font-weight:500; }

        @media (prefers-reduced-motion: reduce) {
          .needle, .lamtick, .rfg, .dwrap, .chev, .opt, ha-card, .ring { transition:none; }
          path.flowline, .rfg { animation:none; }
        }
      </style>
      <ha-card id="card">
        <div class="head">
          <ha-icon icon="mdi:head-lightbulb-outline"></ha-icon>
          <span class="t">Wattson</span>
        </div>
        <div class="hero">
          <div class="ring">
            <svg viewBox="0 0 88 88">
              <circle class="rbg" cx="44" cy="44" r="38"/>
              <circle class="rfg" id="ringfg" cx="44" cy="44" r="38"/>
            </svg>
            <div class="rtxt">
              <div class="rp"><span id="soc-big">–</span><small>%</small></div>
              <div class="rk" id="soc-kwh"></div>
            </div>
          </div>
          <div class="hmid">
            <div class="hlbl">advies</div>
            <div class="hstate" id="hstate">–</div>
            <div class="hsub" id="hnext"></div>
          </div>
          <div class="hright">
            <div class="kpi">
              <div class="hlbl">vandaag</div>
              <div class="hnum" id="h-echt">–</div>
              <div class="hsub" id="h-echt-sub">gerealiseerd</div>
            </div>
            <div class="kpi">
              <div class="hlbl">verwacht</div>
              <div class="hnum plan" id="h-plan">–</div>
              <div class="hsub" id="h-plan-sub"></div>
            </div>
          </div>
        </div>
        <div class="tree" id="tree">
          <svg class="wires" id="wires"></svg>
          ${WattsonV3Card.LEVELS.map(lvl).join("")}
        </div>
        <div class="gbar" id="gbar">
          <div class="gz gz-laden" id="gz-laden"></div>
          <div class="gz gz-ontladen" id="gz-ontladen"></div>
          <div class="lamtick" id="lamtick"></div>
          <div class="needle" id="needle" data-label="–"></div>
        </div>
        <div class="gl"><span>laden loont</span><span>bewaren</span><span>ontladen loont</span></div>
        <div class="tl" id="tl">
          <div class="tlcap"><span>uurplan</span>
            <span class="leg"><span><i style="background:var(--wt-laden)"></i>laden</span><span><i style="background:var(--wt-ontladen)"></i>leveren</span><span><i style="background:rgba(255,255,255,.55)"></i>prijs</span><span><i style="background:rgba(255,255,255,.28)"></i>SoC</span></span>
          </div>
          <div id="tlsvg"></div>
          <div class="tlsum" id="tlsum"></div>
        </div>
        <div class="strip">
          <div class="bt"><span id="bat-s">–</span><small>accu</small></div>
          <div class="netkv"><span id="net-v">–</span><small id="net-s">net</small></div>
          <ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
        </div>
        <div class="dwrap"><div class="dinner"><div class="detail">
          <div class="sec">De afweging</div>
          <div class="afw-row" id="afw-nu"><span class="l">stroom uit net nu</span><span class="v" data-el="v">–</span><span class="x" data-el="x"></span></div>
          <div class="afw-row" id="afw-accu"><span class="l">waarde in de accu</span><span class="v" data-el="v">–</span><span class="x" data-el="x"></span></div>
          <div class="afw-row" id="afw-soc"><span class="l">voorraad</span><span class="v" data-el="v">–</span><span class="x" data-el="x"></span></div>
          <div class="afw-row" id="afw-zon"><span class="l">zon</span><span class="v" data-el="v">–</span><span class="x" data-el="x"></span></div>
          <div class="sec">Plan</div>
          <div class="meta" id="planmeta">–</div>
          <div class="meta" id="sald"></div>
        </div></div></div>
      </ha-card>`;
    this._el = (id) => root.getElementById(id);
    this._q = (sel) => root.querySelector(sel);
    this._qa = (sel) => [...root.querySelectorAll(sel)];
    this._afw = {};
    root.querySelectorAll(".afw-row").forEach((r) => {
      this._afw[r.id] = { row: r, v: r.querySelector('[data-el="v"]'), x: r.querySelector('[data-el="x"]') };
    });
    this._el("card").addEventListener("click", () => {
      this._open = !this._open;
      this.toggleAttribute("data-open", this._open);
    });
  }

  _opt(lvl, id) { return this._q(`.lvl[data-lvl="${lvl}"] .opt[data-opt="${id}"]`); }

  _setLevel(lvl, activeId, tint, subText) {
    const box = this._q(`.lvl[data-lvl="${lvl}"]`);
    this._qa(`.lvl[data-lvl="${lvl}"] .opt`).forEach((o) => {
      o.className = "opt" + (o.dataset.opt === activeId
        ? " active" + (tint ? " tint-" + tint : "") : "");
    });
    let sub = box.querySelector(".sub");
    if (subText) {
      if (!sub) { sub = document.createElement("div"); sub.className = "sub"; box.appendChild(sub); }
      if (sub.textContent !== subText) sub.textContent = subText;
    } else if (sub) sub.remove();
  }

  /* zet tekst en geeft één korte lichtpuls als de waarde écht wisselt, of
     geforceerd bij de allereerste render — anders zie je 'm nooit als de
     status toevallig stabiel blijft tussen twee bezoeken aan het dashboard */
  _setTextFlash(el, text, force = false) {
    if (!el) return;
    const changed = el.textContent !== text;
    if (!changed && !force) return;
    el.textContent = text;
    if (this._reduceMotion) return;
    el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
  }

  _setAfw(key, value, extra, win = false) {
    const r = this._afw[key];
    if (!r) return;
    if (r.v.textContent !== value) r.v.textContent = value;
    if (r.x.textContent !== extra) r.x.textContent = extra;
    r.row.classList.toggle("win", win);
  }

  _update() {
    if (!this._hass) return;
    const C = WattsonV3Card;
    const adv = this._st(this._config.entity);
    if (!adv) return;
    const firstPaint = !this._everPainted;
    this._everPainted = true;
    if (this._reduceMotion === undefined) {
      this._reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    }
    const a = adv.attributes || {};
    const b = a.berekend_met || {};
    const advies = adv.state;
    const fout = a.fout;
    const gestuurd = a.laatst_gestuurd || "";

    // hero rechts: euro's vandaag (echt groot, plan als subregel)
    const sav = this._num(this._config.besparing);
    const real = this._num(this._config.gerealiseerd);
    const echtEl = this._el("h-echt");
    const echtTxt = real === null ? "–" : `${real < 0 ? "−" : "+"}€ ${C._nl(Math.abs(real), 2)}`;
    this._setTextFlash(echtEl, echtTxt, firstPaint);
    echtEl.classList.toggle("neg", real !== null && real < 0);
    // verwacht planvoordeel: over de hele planhorizon, niet alleen vandaag
    const planEl = this._el("h-plan");
    const planTxt = sav === null ? "–" : `${sav < 0 ? "−" : "+"}€ ${C._nl(Math.abs(sav), 2)}`;
    this._setTextFlash(planEl, planTxt, firstPaint);
    planEl.classList.toggle("neg", sav !== null && sav < -0.005);
    planEl.classList.toggle("zero", sav !== null && Math.abs(sav) <= 0.005);
    const hz = typeof b.horizon_uren === "number" ? Math.round(b.horizon_uren) : null;
    const planSub = sav === null ? "" : hz ? `plan · komende ${hz} u` : "plan";
    const planSubEl = this._el("h-plan-sub");
    if (planSubEl.textContent !== planSub) planSubEl.textContent = planSub;

    // hero midden: advieswoord (kort) + volgende actie
    const hstate = this._el("hstate");
    const kort = fout ? "fout" : (advies || "–").split(":")[0].replace(/\(.*\)/, "").trim();
    this._setTextFlash(hstate, kort, firstPaint);
    hstate.className = "hstate " + (fout ? "fout"
      : /laden/.test(advies) && !/ontladen/.test(advies) ? "laden"
      : /ontladen|verkopen/.test(advies) ? "ontladen" : "");
    const hnextTxt = fout ? String(fout) : (a.volgende_actie || a.reden || "");
    const hnext = this._el("hnext");
    if (hnext.textContent !== hnextTxt) hnext.textContent = hnextTxt;

    // fout → automatisch open (één keer, sluiten mag daarna)
    if (fout && !this._autoOpened) {
      this._autoOpened = true; this._open = true; this.toggleAttribute("data-open", true);
    } else if (!fout) this._autoOpened = false;

    // niveau 1: sturing
    const actief = !!a.sturing_actief;
    this._setLevel("sturing", actief ? "actief" : "schaduw", actief ? "green" : "amber",
      actief ? null : "Wattson adviseert alleen — stuurt niet");

    // niveau 2: veiligheid
    const watchTrip = typeof fout === "string" && fout.startsWith("WATCHDOG");
    const staleErr = typeof fout === "string" && !watchTrip && /telemetrie|stil/i.test(fout);
    const geenData = advies === "geen data";
    const evBlock = advies === "rust (EV-guard)" || advies === "rust (EV-check)";
    const gates = { data: !geenData, watchdog: !watchTrip, stale: !staleErr, ev: !evBlock };
    let blockedGate = null;
    this._qa('.lvl[data-lvl="gates"] .opt').forEach((o) => {
      const ok = gates[o.dataset.opt];
      o.className = "opt " + (ok ? "ok" : "blocked");
      if (!ok && !blockedGate) blockedGate = o.dataset.opt;
    });
    const gateBox = this._q('.lvl[data-lvl="gates"]');
    let gsub = gateBox.querySelector(".sub");
    const gateTxt = watchTrip ? fout
      : evBlock ? "auto laadt — accu geblokkeerd voor de auto"
      : staleErr ? fout : null;
    if (gateTxt) {
      if (!gsub) { gsub = document.createElement("div"); gsub.className = "sub"; gateBox.appendChild(gsub); }
      if (gsub.textContent !== gateTxt) gsub.textContent = gateTxt;
    } else if (gsub) gsub.remove();

    // niveau 3: doel
    const aggro = this._st(this._config.agressiviteit);
    const aggroId = (aggro && aggro.state) || a.agressiviteit || "gebalanceerd";
    this._setLevel("aggro", aggroId, null, null);

    // niveau 4: plan (met reden als enige sub-regel)
    let planId = "rust", planTint = null;
    if (advies === "laden" || advies === "bijspringen: laden") {
      planId = /smart_charging/.test(gestuurd) ? "laden_zon" : "laden_net";
      planTint = "amber";
    } else if (advies === "ontladen" || advies === "bijspringen: ontladen") {
      planId = "ontladen"; planTint = "green";
    } else if (advies === "verkopen") {
      planId = "verkopen"; planTint = "green";
    }
    this._setLevel("plan", planId, planTint, a.reden || null);

    // niveau 5: realtime
    const rtId = advies === "bijspringen: laden" ? "assist_laden"
      : advies === "bijspringen: ontladen" ? "assist_ontladen" : "volgt";
    this._setLevel("rt", rtId, rtId === "volgt" ? null : planTint, null);

    // niveau 6: apparaat — merk-onafhankelijk uit laatst_gestuurd + meting;
    // een Zendure operation-select (config.mode) mag dat verfijnen
    const chg = this._numOr(this._config.chg_w, b.accu_laden_w);
    const dis = this._numOr(this._config.dis_w, b.accu_ontladen_w);
    const mode = this._st(this._config.mode);
    let modeId = C._deviceId(gestuurd, chg, dis);
    if (mode && mode.state === "smart_charging") modeId = "zon";
    else if (mode && mode.state === "off") modeId = "rust";
    const devTint = modeId === "laden" || modeId === "zon" ? "amber" : modeId === "ontladen" ? "green" : null;
    this._setLevel("device", modeId, devTint, null);

    // prijszone-meter
    const prijs = b.prijs_nu, lam = b.marginale_waarde_eur_kwh;
    const plaf = b.laadplafond_eur_kwh, vloer = b.ontlaadvloer_eur_kwh;
    if ([prijs, plaf, vloer].every((v) => typeof v === "number")) {
      const pad = Math.max((vloer - plaf) * 0.35, 0.03);
      const lo = Math.min(plaf, prijs) - pad, hi = Math.max(vloer, prijs) + pad;
      const pct = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
      this._el("gz-laden").style.width = pct(plaf) + "%";
      this._el("gz-ontladen").style.width = (100 - pct(vloer)) + "%";
      const needle = this._el("needle");
      needle.style.left = pct(prijs) + "%";
      needle.dataset.label = C._ct(prijs);
      const lt = this._el("lamtick");
      if (typeof lam === "number") { lt.style.display = ""; lt.style.left = pct(lam) + "%"; }
      else lt.style.display = "none";
    }

    // uurplan-timeline (herbouw alleen als het plan zelf wijzigt)
    this._renderPlan(a.plan || [], b);

    // hero links: SoC-halo (kleur volgt wat de accu nú doet)
    const socP = this._numOr(this._config.soc, b.soc_pct);
    const ring = this._el("ringfg");
    const CIRC = 238.76;
    const targetOffset = (CIRC * (1 - Math.max(0, Math.min(socP || 0, 100)) / 100)).toFixed(1);
    if (firstPaint && !this._reduceMotion) {
      // forceer een lege startstand + reflow, zodat de ring altijd zichtbaar intekent
      // bij het openen van het dashboard — niet afhankelijk van of de data wisselt
      ring.style.transition = "none";
      ring.style.strokeDashoffset = String(CIRC);
      void ring.getBoundingClientRect();
      ring.style.transition = "";
      requestAnimationFrame(() => { ring.style.strokeDashoffset = targetOffset; });
    } else {
      ring.style.strokeDashoffset = targetOffset;
    }
    const rc = fout ? WT_PAL.err : (chg || 0) > 50 ? WT_PAL.laden : (dis || 0) > 50 ? WT_PAL.ontladen : WT_PAL.sage;
    ring.style.stroke = rc;
    ring.style.filter = `drop-shadow(0 0 2px ${rc}b3) drop-shadow(0 0 9px ${rc}4d)`;
    const ringWrap = this._q(".ring");
    if (ringWrap) ringWrap.style.boxShadow = `0 0 22px 4px ${rc}30, 0 0 46px 10px ${rc}17`;
    this._setTextFlash(this._el("soc-big"), socP === null ? "–" : C._nl(socP), firstPaint);
    this._el("soc-kwh").textContent = typeof b.soc_kwh === "number" ? `${C._nl(b.soc_kwh, 1)} kWh` : "";

    // strip
    this._el("bat-s").textContent = (chg || 0) > 50 ? `laadt met ${C._nl(chg)} W`
      : (dis || 0) > 50 ? `levert ${C._nl(dis)} W` : "in rust";
    const p1 = this._numOr(this._config.p1, b.p1_nu_w);
    this._el("net-v").textContent = p1 === null ? "–" : `${C._nl(Math.abs(p1))} W`;
    this._el("net-s").textContent = p1 === null ? "net" : p1 >= 0 ? "import van net" : "export naar net";

    // detail: de afweging
    const plan = a.plan || [];
    const disRows = plan.filter((r) => r.setpoint_w < -50);
    const best = disRows.length ? disRows.reduce((m, r) => (r.prijs > m.prijs ? r : m)) : null;
    const eur = (v, d = 3) => `€ ${C._nl(v, d)}`;
    const discharging = /(ontladen|verkopen)/.test(advies);
    this._setAfw("afw-nu", typeof prijs === "number" ? eur(prijs) : "–", "importprijs dit uur", advies === "laden");
    this._setAfw("afw-accu", best ? eur(best.prijs) : "–",
      best ? `beste planuur · ${best.tijd}` : "geen ontlaaduren gepland", discharging);
    this._setAfw("afw-soc",
      socP !== null ? `${C._nl(socP)}% · ${C._nl(b.soc_kwh, 1)} kWh` : "–",
      typeof lam === "number" ? `bewaren is ${C._ct(lam)}/kWh waard` : "");
    this._setAfw("afw-zon",
      typeof b.pv_rest_vandaag_kwh === "number" ? `${C._nl(b.pv_rest_vandaag_kwh, 1)} kWh vandaag` : "–",
      typeof b.pv_morgen_kwh === "number" ? `morgen ~${C._nl(b.pv_morgen_kwh, 1)} kWh` : "");

    // detail: plan-meta + saldering
    const sell = this._st(this._config.sw_sell);
    const meta = `volgende: <b>${a.volgende_actie || "–"}</b> · bijspringen ${a.bijspringen || "–"} · verkopen ${sell && sell.state === "on" ? "gewapend" : "uit"} · gestuurd: ${gestuurd || "–"}`;
    if (this._metaHtml !== meta) { this._metaHtml = meta; this._el("planmeta").innerHTML = meta; }
    const ruimte = b.salderingsruimte_kwh;
    this._el("sald").textContent = typeof ruimte === "number"
      ? `saldering: ${C._nl(ruimte)} kWh ruimte over` + (b.wedge_effectief ? ` · wedge ${C._ct(b.wedge_effectief)}` : "")
      : "";

    // energiedraad
    this._path = { blockedGate, planId, rtId, modeId,
      actiefId: actief ? "actief" : "schaduw", aggroId,
      tint: fout ? "red" : planTint || "neutral" };
    requestAnimationFrame(() => this._wire());
  }

  _renderPlan(plan, b) {
    const host = this._el("tlsvg");
    if (!host) return;
    if (!plan.length) { host.innerHTML = ""; this._el("tlsum").textContent = ""; this._planKey = ""; return; }
    const cap = (typeof b.soc_kwh === "number" && typeof b.soc_pct === "number" && b.soc_pct > 1)
      ? b.soc_kwh / (b.soc_pct / 100) : 5.76;
    const key = JSON.stringify([plan, b.soc_kwh]);
    if (key === this._planKey) return;   // plan ongewijzigd: niets herbouwen
    this._planKey = key;
    const C = WattsonV3Card;
    // v3.6: drie ruimere banden met eigen label in de linkermarge, elke uurkolom
    // gelabeld, vermogen en prijs per uur als tekst, kolomtint bij laden/leveren,
    // SoC begin/eind in %, en een samenvattingsregel onder de grafiek.
    const n = plan.length, cw = 30, gx = 26, W = gx + n * cw;
    const priceTop = 14, priceH = 22;                 // prijsband
    const midY = priceTop + priceH + 30, barMax = 26; // vermogensband (balken op/neer)
    const socTop = midY + barMax + 22, socH = 18;     // SoC-band
    const H = socTop + socH + 14;
    const pMaxChg = 2000, pMaxDis = 1400;
    const prices = plan.map((r) => r.prijs);
    const pMin = Math.min(...prices), pMax = Math.max(...prices);
    const priceY = (p) => priceTop + (pMax === pMin ? priceH / 2 : (1 - (p - pMin) / (pMax - pMin)) * priceH);
    const socY = (kwh) => socTop + socH - (Math.max(0, Math.min(kwh, cap)) / cap) * socH;
    const X = (i) => gx + i * cw;
    const kw = (w) => C._nl(Math.abs(w) / 1000, 1);
    const parts = [];
    // kolomtint: amber bij laden, teal bij leveren — koppelt prijs en SoC aan de actie
    plan.forEach((r, i) => {
      const sp = r.setpoint_w || 0;
      if (sp > 20) parts.push(`<rect x="${X(i)}" y="2" width="${cw}" height="${H - 14}" fill="${WT_PAL.laden}" opacity=".09"/>`);
      else if (sp < -20) parts.push(`<rect x="${X(i)}" y="2" width="${cw}" height="${H - 14}" fill="${WT_PAL.ontladen}" opacity=".09"/>`);
    });
    parts.push(`<rect x="${gx}" y="2" width="${cw}" height="${H - 14}" fill="rgba(255,255,255,.04)"/>`);
    // bandlabels links
    const lbl = (y, t) => parts.push(`<text x="${gx - 6}" y="${y}" text-anchor="end" font-size="7.5" letter-spacing=".08em" fill="rgba(255,255,255,.34)">${t}</text>`);
    lbl(priceTop + priceH / 2 + 3, "PRIJS");
    lbl(midY + 3, "ACCU");
    lbl(socTop + socH / 2 + 3, "SOC");
    // hairline-raster
    parts.push(`<line x1="${gx}" y1="${midY}" x2="${W}" y2="${midY}" stroke="rgba(226,224,212,.16)"/>`);
    parts.push(`<line x1="${gx}" y1="${socTop + socH}" x2="${W}" y2="${socTop + socH}" stroke="rgba(226,224,212,.10)"/>`);
    // vermogensbalken + kW-tekst + uurlabel per kolom
    plan.forEach((r, i) => {
      const x = X(i) + 4, bw = cw - 8, sp = r.setpoint_w || 0, cx = X(i) + cw / 2;
      if (sp > 20) {
        const h = Math.max(Math.min(sp / pMaxChg, 1) * barMax, 3);
        parts.push(`<path d="M${x},${midY} v${-(h - 3)} a3,3 0 0 1 3,-3 h${bw - 6} a3,3 0 0 1 3,3 v${h - 3} z" fill="${WT_PAL.laden}" opacity=".9"/>`);
        parts.push(`<text x="${cx}" y="${midY - h - 3}" text-anchor="middle" font-size="7.5" fill="${WT_PAL.laden}">+${kw(sp)}</text>`);
      } else if (sp < -20) {
        const h = Math.max(Math.min(-sp / pMaxDis, 1) * barMax, 3);
        parts.push(`<path d="M${x},${midY} v${h - 3} a3,3 0 0 0 3,3 h${bw - 6} a3,3 0 0 0 3,-3 v${-(h - 3)} z" fill="${WT_PAL.ontladen}" opacity=".9"/>`);
        parts.push(`<text x="${cx}" y="${midY + h + 9}" text-anchor="middle" font-size="7.5" fill="${WT_PAL.ontladen}">−${kw(sp)}</text>`);
      } else {
        parts.push(`<rect x="${x}" y="${midY - 1}" width="${bw}" height="2" rx="1" fill="rgba(226,224,212,.16)"/>`);
      }
      const hh = r.tijd.slice(0, 2);
      parts.push(`<text x="${cx}" y="${H - 3}" text-anchor="middle" font-size="8.5" fill="rgba(255,255,255,${i === 0 ? ".85" : ".40"})">${i === 0 ? "nu" : hh}</text>`);
    });
    // SoC-band: was + lijn, begin- en eindpercentage
    const socPts = [[gx, socY(b.soc_kwh || 0)]];
    plan.forEach((r, i) => socPts.push([X(i + 1), socY(r.soc_na_kwh)]));
    const socLine = socPts.map((p) => `${p[0]},${p[1].toFixed(1)}`).join(" ");
    parts.push(`<polygon points="${gx},${socTop + socH} ${socLine} ${W},${socTop + socH}" fill="rgba(255,255,255,.12)"/>`);
    parts.push(`<polyline points="${socLine}" fill="none" stroke="rgba(255,255,255,.8)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`);
    const pct = (kwh) => C._nl((kwh / cap) * 100) + "%";
    const last = plan[plan.length - 1].soc_na_kwh;
    const minRow = plan.reduce((m, r) => (r.soc_na_kwh < m.soc_na_kwh ? r : m));
    parts.push(`<text x="${gx + 7}" y="${socTop - 3}" font-size="7.5" fill="rgba(255,255,255,.6)">${pct(b.soc_kwh || 0)}</text>`);
    parts.push(`<text x="${W - 2}" y="${socTop - 3}" text-anchor="end" font-size="7.5" fill="rgba(255,255,255,.6)">→ ${pct(last)}</text>`);
    if (minRow.soc_na_kwh < (b.soc_kwh || 0) - 0.3 && minRow.soc_na_kwh < last - 0.3) {
      const im = plan.indexOf(minRow);
      parts.push(`<text x="${X(im + 1)}" y="${socY(minRow.soc_na_kwh) + 9}" text-anchor="middle" font-size="7" fill="rgba(255,255,255,.45)">${pct(minRow.soc_na_kwh)}</text>`);
    }
    // prijsband: staplijn + prijs per uur (extremen fel, rest gedempt)
    const pr = plan.map((r, i) => `${X(i)},${priceY(r.prijs).toFixed(1)} ${X(i + 1)},${priceY(r.prijs).toFixed(1)}`).join(" ");
    parts.push(`<polyline points="${pr}" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="2" stroke-linejoin="round"/>`);
    const iMax = prices.indexOf(pMax), iMin = prices.indexOf(pMin);
    plan.forEach((r, i) => {
      const hot = i === iMax || i === iMin;
      const y = priceY(r.prijs) - 4;
      parts.push(`<text x="${X(i) + cw / 2}" y="${y}" text-anchor="middle" font-size="${hot ? 7.5 : 6.5}" fill="rgba(255,255,255,${hot ? ".8" : ".38"})">${C._nl(r.prijs * 100, 0)}</text>`);
    });
    parts.push(`<text x="${W - 2}" y="${priceTop + priceH + 9}" text-anchor="end" font-size="6.5" fill="rgba(255,255,255,.3)">ct/kWh</text>`);
    // nu-marker
    parts.push(`<line x1="${gx + 1}" y1="2" x2="${gx + 1}" y2="${H - 12}" stroke="rgba(255,255,255,.16)" stroke-width="4"/>`);
    parts.push(`<line x1="${gx + 1}" y1="4" x2="${gx + 1}" y2="${H - 12}" stroke="rgba(255,255,255,.55)" stroke-width="1.5"/>`);
    // hover/tik-laag: één tooltip per uur
    plan.forEach((r, i) => {
      const sp = r.setpoint_w || 0;
      const act = sp > 20 ? `laden ${C._nl(sp)} W` : sp < -20 ? `leveren ${C._nl(-sp)} W` : "rust";
      const tip = `${r.tijd} · ${act}\nprijs ${C._ct(r.prijs)}\nSoC na ${C._nl(r.soc_na_kwh, 2)} kWh (${C._nl((r.soc_na_kwh / cap) * 100)}%)`;
      parts.push(`<rect x="${X(i)}" y="0" width="${cw}" height="${H}" fill="transparent"><title>${tip}</title></rect>`);
    });
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="uurplan: prijs, vermogen en SoC per uur">${parts.join("")}</svg>`;
    // samenvatting: wat het plan in gewone taal betekent
    const rng = (rows) => rows.length ? (rows.length === 1 ? `${rows[0].tijd.slice(0, 2)} u` : `${rows[0].tijd.slice(0, 2)}–${rows[rows.length - 1].tijd.slice(0, 2)} u`) : "";
    const chg = plan.filter((r) => (r.setpoint_w || 0) > 20), dis = plan.filter((r) => (r.setpoint_w || 0) < -20);
    const kwhC = chg.reduce((a, r) => a + r.setpoint_w, 0) / 1000, kwhD = -dis.reduce((a, r) => a + r.setpoint_w, 0) / 1000;
    const eurD = -dis.reduce((a, r) => a + r.setpoint_w * r.prijs, 0) / 1000, eurC = chg.reduce((a, r) => a + r.setpoint_w * r.prijs, 0) / 1000;
    const bits = [];
    if (chg.length) bits.push(`<b class="c">laden</b> ${C._nl(kwhC, 1)} kWh om ${rng(chg)} (gem. ${C._ct(eurC / kwhC)})`);
    if (dis.length) bits.push(`<b class="d">leveren</b> ${C._nl(kwhD, 1)} kWh om ${rng(dis)} (gem. ${C._ct(eurD / kwhD)})`);
    if (!bits.length) bits.push("geen accu-actie gepland in de komende uren");
    const sumEl = this._el("tlsum");
    const html = bits.join(" · ");
    if (sumEl && sumEl.innerHTML !== html) sumEl.innerHTML = html;
  }

  _wire() {
    if (!this._built || !this._path) return;
    const svg = this._el("wires");
    const tree = this._el("tree");
    if (!svg || !tree) return;
    const tb = tree.getBoundingClientRect();
    if (tb.width === 0) return;
    const P = this._path;
    const colors = { amber: WT_PAL.laden, green: WT_PAL.ontladen, red: WT_PAL.err,
                     neutral: "rgba(226,224,212,.45)" };
    const color = colors[P.tint] || colors.neutral;
    const pt = (el, edge) => {
      const r = el.getBoundingClientRect();
      return [r.left - tb.left + r.width / 2, (edge === "top" ? r.top : r.bottom) - tb.top];
    };
    const stops = [[...pt(this._opt("sturing", P.actiefId), "bottom")]];
    const blockedEl = P.blockedGate && this._opt("gates", P.blockedGate);
    if (blockedEl) {
      stops.push(pt(blockedEl, "top"));
    } else {
      const gateTrack = this._q('.lvl[data-lvl="gates"] .track');
      const gr = gateTrack.getBoundingClientRect();
      const gx = gr.left - tb.left + gr.width / 2;
      stops.push([gx, gr.top - tb.top]);
      stops.push([gx, gr.bottom - tb.top]);
      stops.push(pt(this._opt("aggro", P.aggroId), "top"));
      stops.push(pt(this._opt("aggro", P.aggroId), "bottom"));
      stops.push(pt(this._opt("plan", P.planId), "top"));
      stops.push(pt(this._opt("plan", P.planId), "bottom"));
      stops.push(pt(this._opt("rt", P.rtId), "top"));
      stops.push(pt(this._opt("rt", P.rtId), "bottom"));
      stops.push(pt(this._opt("device", P.modeId), "top"));
    }
    svg.setAttribute("viewBox", `0 0 ${tb.width} ${tb.height}`);
    let d = "";
    for (let i = 0; i < stops.length; i++) {
      const [x, y] = stops[i];
      if (i === 0) { d += `M ${x} ${y}`; continue; }
      const [px, py] = stops[i - 1];
      if (Math.abs(py - y) < 2) d += ` L ${x} ${y}`;
      else d += ` C ${px} ${py + (y - py) * 0.55}, ${x} ${y - (y - py) * 0.55}, ${x} ${y}`;
    }
    const end = stops[stops.length - 1] || [0, 0];
    svg.innerHTML =
      `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" opacity=".45" stroke-linecap="round"/>` +
      `<path class="flowline" d="${d}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" opacity=".9"/>` +
      `<circle cx="${end[0]}" cy="${end[1]}" r="2.5" fill="${color}"/>`;
  }


}

customElements.define("wattson-v3-card", WattsonV3Card);

/* visuele editor: ha-form met entity-selectors; config-changed alleen bij echte wijziging */
class WattsonV3CardEditor extends HTMLElement {
  setConfig(config) { this._config = { ...(config || {}) }; this._render(); }
  set hass(hass) { this._hass = hass; if (this._form) this._form.hass = hass; }
  static get SCHEMA() {
    const ent = (name, domain, label) => ({ name, selector: { entity: { domain } }, label });
    return [
      ent("entity", "sensor", "Wattson advies-sensor"),
      ent("gerealiseerd", "sensor", "Gerealiseerd (EUR vandaag)"),
      ent("besparing", "sensor", "Verwachte besparing (EUR)"),
      ent("soc", "sensor", "Accu SoC (%) — optioneel, standaard uit Wattson"),
      ent("chg_w", "sensor", "Laadvermogen (W) — optioneel, standaard uit Wattson"),
      ent("dis_w", "sensor", "Ontlaadvermogen (W) — optioneel, standaard uit Wattson"),
      ent("p1", "sensor", "P1 netvermogen (W) — optioneel, standaard uit Wattson"),
      ent("mode", "select", "Zendure operation-select — optioneel"),
      ent("agressiviteit", "select", "Wattson agressiviteit (select)"),
      ent("sw_sell", "switch", "Verkopen-schakelaar"),
    ];
  }
  _render() {
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (sch) => sch.label || sch.name;
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        const next = { ...this._config, ...(ev.detail.value || {}) };
        if (JSON.stringify(next) === JSON.stringify(this._config)) return;
        this._config = next;
        this.dispatchEvent(new CustomEvent("config-changed",
          { detail: { config: next }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.schema = WattsonV3CardEditor.SCHEMA;
    this._form.data = this._config;
    if (this._hass) this._form.hass = this._hass;
  }
}
customElements.define("wattson-v3-card-editor", WattsonV3CardEditor);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "wattson-v3-card",
  name: "Wattson kaart v3.7",
  description: "Wattson-beslisboom als segmented rows met energiedraad, prijszones en uurplan — detail op tik.",
});
