/**
 * wattson-tree-card — de complete beslisboom van Wattson v3 als levende tree.
 *
 * - zes niveaus met ÁLLE opties zichtbaar; een geanimeerde energiedraad
 *   (glow + stromende stippen) volgt het actieve pad van boven naar beneden
 * - een "gedachte" in mensentaal bovenaan: wat doet Wattson en waarom
 * - de prijs-zone-meter: waar zit de actuele prijs t.o.v. het laadplafond en
 *   de ontlaadvloer (laden / bewaren / ontladen) — de λ-regel in één oogopslag
 * - accu-glyph met live vulling en richting
 *
 * type: custom:wattson-tree-card         # alle entities hebben defaults
 * Stijl: overzicht "glass", geen emojis, build-once/patch.
 */
class WattsonTreeCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: "sensor.wattson_advies",
      besparing: "sensor.wattson_verwachte_besparing",
      soc: "sensor.solarflow_2400_ac_electric_level",
      chg_w: "sensor.solarflow_2400_ac_grid_input_power",
      dis_w: "sensor.solarflow_2400_ac_output_home_power",
      p1: "sensor.p1_meter_power",
      mode: "select.zendure_manager_operation",
      acmode: "select.solarflow_2400_ac_ac_mode",
      sw_sturing: "switch.wattson_sturing",
      sw_assist: "switch.wattson_bijspringen",
      sw_sell: "switch.wattson_verkopen",
      agressiviteit: "select.wattson_agressiviteit",
      ...(config || {}),
    };
    this._built = false;
  }

  getCardSize() { return 10; }
  static getStubConfig() { return {}; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  connectedCallback() {
    if (!this._ro && "ResizeObserver" in window) {
      this._ro = new ResizeObserver(() => this._wire());
      this._ro.observe(this);
    }
  }
  disconnectedCallback() { if (this._ro) { this._ro.disconnect(); this._ro = null; } }

  _st(id) { return this._hass && this._hass.states[id]; }
  _num(id) {
    const s = this._st(id);
    const v = s && parseFloat(s.state);
    return Number.isFinite(v) ? v : null;
  }
  static _nl(v, dec = 0) {
    return v === null || v === undefined || !Number.isFinite(v) ? "–"
      : v.toLocaleString("nl-NL", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  static _ct(v) {  // €/kWh -> centen, leest makkelijker
    return v === null || v === undefined || !Number.isFinite(v) ? "–"
      : WattsonTreeCard._nl(v * 100, 1) + " ct";
  }

  static get LEVELS() {
    return [
      { key: "sturing", label: "sturing", opts: [
        { id: "actief", t: "actief" }, { id: "schaduw", t: "schaduw" }] },
      { key: "gates", label: "veiligheid", gates: true, opts: [
        { id: "data", t: "data" }, { id: "watchdog", t: "watchdog" },
        { id: "stale", t: "telemetrie" }, { id: "ev", t: "EV" }] },
      { key: "aggro", label: "doelfunctie", opts: [
        { id: "rustig", t: "rustig" }, { id: "gebalanceerd", t: "gebalanceerd" },
        { id: "agressief", t: "agressief" }] },
      { key: "plan", label: "plan — wat doet dit uur", opts: [
        { id: "laden_net", t: "laden net" }, { id: "laden_zon", t: "laden zon" },
        { id: "rust", t: "rust" }, { id: "ontladen", t: "ontladen" },
        { id: "verkopen", t: "verkopen" }] },
      { key: "rt", label: "realtime", opts: [
        { id: "assist_laden", t: "bijspringen laden" }, { id: "volgt", t: "volgt plan" },
        { id: "assist_ontladen", t: "bijspringen ontladen" }] },
      { key: "device", label: "apparaat", opts: [
        { id: "off", t: "off" }, { id: "manual", t: "manual" },
        { id: "smart_charging", t: "smart charge" },
        { id: "smart_discharging", t: "smart discharge" }] },
    ];
  }

  _build() {
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    const lvl = (L) => `
      <div class="lvl" data-lvl="${L.key}">
        <span class="lt">${L.label}</span>
        <div class="opts${L.gates ? " gates" : ""}">
          ${L.opts.map(o => `<span class="opt" data-opt="${o.id}">${o.t}</span>`).join("")}
        </div>
      </div>`;
    root.innerHTML = `
      <style>
        :host { display:block; }
        ha-card {
          background: rgba(24,28,34,.42);
          -webkit-backdrop-filter: blur(18px) saturate(1.25);
          backdrop-filter: blur(18px) saturate(1.25);
          border: 1px solid rgba(226,224,212,.13);
          border-radius: 8px;
          box-shadow: 0 1px 0 rgba(255,255,255,.03) inset, 0 6px 20px rgba(0,0,0,.18);
          color: rgba(255,255,255,.94);
          padding: 16px;
          font: 400 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
          overflow: hidden;
        }
        .head { display:flex; align-items:center; gap:8px; margin-bottom:4px; }
        .head ha-icon { --mdc-icon-size:17px; color:#a7ada2; }
        .head .t { font-size:12px; font-weight:600; letter-spacing:.12em;
                   text-transform:uppercase; color:rgba(255,255,255,.56); flex:1; }
        .save { font-size:11px; color:rgba(255,255,255,.42); white-space:nowrap; }
        .save b { color:#4cc88a; font-weight:600; }
        .state-chip {
          font-size:11px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
          padding:3px 10px; border-radius:11px;
          background:rgba(255,255,255,.06); border:1px solid rgba(226,224,212,.13);
          color:rgba(255,255,255,.56); white-space:nowrap;
        }
        .state-chip.laden   { color:#ffb86b; border-color:rgba(255,184,107,.4); background:rgba(255,184,107,.10); }
        .state-chip.ontladen{ color:#4cc88a; border-color:rgba(76,200,138,.4);  background:rgba(76,200,138,.10); }
        .state-chip.fout    { color:#ff6b81; border-color:rgba(255,107,129,.4); background:rgba(255,107,129,.10); }

        .thought {
          font-size:14.5px; line-height:1.5; color:rgba(255,255,255,.88);
          margin:8px 0 16px; min-height:2.9em;
        }
        .thought b { color:#fff; font-weight:600; }
        .thought .amber { color:#ffb86b; } .thought .green { color:#4cc88a; }
        .thought .red { color:#ff6b81; }

        /* prijs-zone-meter */
        .gauge { margin:0 0 18px; }
        .gl { display:flex; justify-content:space-between; font-size:9.5px; font-weight:600;
              letter-spacing:.12em; text-transform:uppercase; color:rgba(255,255,255,.30);
              margin-bottom:5px; }
        .gbar { position:relative; height:26px; border-radius:6px; overflow:visible;
                background:rgba(255,255,255,.05); }
        .gz { position:absolute; top:0; bottom:0; }
        .gz-laden { left:0; background:linear-gradient(90deg, rgba(255,184,107,.34), rgba(255,184,107,.10));
                    border-radius:6px 0 0 6px; }
        .gz-bewaren { background:rgba(255,255,255,.045); }
        .gz-ontladen { right:0; background:linear-gradient(90deg, rgba(76,200,138,.10), rgba(76,200,138,.34));
                       border-radius:0 6px 6px 0; }
        .gzt { position:absolute; top:50%; transform:translateY(-50%); font-size:9.5px;
               font-weight:600; letter-spacing:.08em; text-transform:uppercase;
               color:rgba(255,255,255,.38); white-space:nowrap; padding:0 7px; }
        .needle { position:absolute; top:-7px; bottom:-7px; width:2px; border-radius:1px;
                  background:#fff; box-shadow:0 0 8px rgba(255,255,255,.8);
                  transition:left .8s cubic-bezier(.4,0,.2,1); }
        .needle::after { content:attr(data-label); position:absolute; top:-15px; left:50%;
                  transform:translateX(-50%); font-size:10px; font-weight:600; color:#fff;
                  white-space:nowrap; }
        .lamtick { position:absolute; top:3px; bottom:3px; width:1px;
                   background:rgba(255,255,255,.45); transition:left .8s ease; }
        .lamtick::after { content:"λ"; position:absolute; bottom:-15px; left:50%;
                  transform:translateX(-50%); font-size:9px; color:rgba(255,255,255,.45); }
        .gcap { display:flex; justify-content:space-between; font-size:10px;
                color:rgba(255,255,255,.34); margin-top:16px; }

        /* de boom */
        .tree { position:relative; }
        svg.wires { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
        .lvl { position:relative; padding:10px 0; }
        .lt { display:block; font-size:9.5px; font-weight:600; letter-spacing:.14em;
              text-transform:uppercase; color:rgba(255,255,255,.30); margin:0 0 6px; }
        .opts { display:flex; gap:6px; flex-wrap:wrap; position:relative; }
        .opt {
          font-size:11px; color:rgba(255,255,255,.38);
          background:rgba(255,255,255,.03); border:1px solid rgba(226,224,212,.06);
          border-radius:9px; padding:3px 10px; white-space:nowrap; position:relative; z-index:1;
          transition: all .35s ease;
        }
        .opt.active {
          color:rgba(255,255,255,.97); font-weight:600; transform:scale(1.04);
          background:rgba(255,255,255,.10); border-color:rgba(226,224,212,.32);
          box-shadow:0 0 0 1px rgba(255,255,255,.05), 0 2px 14px rgba(0,0,0,.3);
        }
        .opt.active.tint-amber { color:#ffb86b; border-color:rgba(255,184,107,.55);
          background:rgba(255,184,107,.12); box-shadow:0 0 14px rgba(255,184,107,.25); }
        .opt.active.tint-green { color:#4cc88a; border-color:rgba(76,200,138,.55);
          background:rgba(76,200,138,.12); box-shadow:0 0 14px rgba(76,200,138,.25); }
        .opt.active.tint-red   { color:#ff6b81; border-color:rgba(255,107,129,.55);
          background:rgba(255,107,129,.12); box-shadow:0 0 14px rgba(255,107,129,.25); }
        .opts.gates .opt.ok { color:rgba(76,200,138,.70); border-color:rgba(76,200,138,.20);
          background:rgba(76,200,138,.045); }
        .opts.gates .opt.blocked { color:#ff6b81; font-weight:600;
          border-color:rgba(255,107,129,.55); background:rgba(255,107,129,.13);
          box-shadow:0 0 14px rgba(255,107,129,.3); }
        .sub { font-size:10.5px; color:rgba(255,255,255,.38); margin-top:5px;
               overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

        @keyframes flow { to { stroke-dashoffset:-26; } }
        path.glowline { filter:blur(3px); opacity:.35; }
        path.flowline { stroke-dasharray:3 10; animation:flow 1.4s linear infinite; }
        @keyframes pulse { 0%,100% { r:3; opacity:.9; } 50% { r:5.5; opacity:.45; } }
        circle.end { animation:pulse 2.2s ease-in-out infinite; }

        /* strip onderin: accu-glyph + net */
        .strip { display:flex; align-items:center; gap:14px; padding-top:12px; margin-top:6px;
                 border-top:1px solid rgba(226,224,212,.075); }
        .bat { display:flex; align-items:center; gap:8px; flex:1; }
        .batbody { position:relative; width:64px; height:24px; border-radius:5px;
                   border:1.5px solid rgba(226,224,212,.35); padding:2.5px; }
        .batbody::after { content:""; position:absolute; right:-5px; top:7px; width:3px;
                   height:10px; border-radius:0 2px 2px 0; background:rgba(226,224,212,.35); }
        .batfill { height:100%; border-radius:2.5px; background:#4cc88a;
                   transition:width .8s ease, background .4s; min-width:2px; }
        .batfill.chg { background:#ffb86b; }
        .battxt { font-size:12px; color:rgba(255,255,255,.86); font-weight:600; }
        .battxt small { display:block; font-weight:400; font-size:10.5px;
                        color:rgba(255,255,255,.42); }
        .netkv { text-align:right; font-size:12px; color:rgba(255,255,255,.86); font-weight:600; }
        .netkv small { display:block; font-weight:400; font-size:10.5px;
                       color:rgba(255,255,255,.42); }
      </style>
      <ha-card>
        <div class="head">
          <ha-icon icon="mdi:brain"></ha-icon>
          <span class="t">Wattson — beslisboom</span>
          <span class="save" id="save"></span>
          <span class="state-chip" id="chip">–</span>
        </div>
        <p class="thought" id="thought">–</p>

        <div class="gauge">
          <div class="gl"><span>laden loont</span><span>bewaren</span><span>ontladen loont</span></div>
          <div class="gbar" id="gbar">
            <div class="gz gz-laden" id="gz-laden"></div>
            <div class="gz gz-bewaren" id="gz-bewaren"></div>
            <div class="gz gz-ontladen" id="gz-ontladen"></div>
            <div class="lamtick" id="lamtick"></div>
            <div class="needle" id="needle" data-label="–"></div>
          </div>
          <div class="gcap"><span id="g-lo">–</span><span>stroomprijs per kWh</span><span id="g-hi">–</span></div>
        </div>

        <div class="tree" id="tree">
          <svg class="wires" id="wires"></svg>
          ${WattsonTreeCard.LEVELS.map(lvl).join("")}
        </div>

        <div class="strip">
          <div class="bat">
            <div class="batbody"><div class="batfill" id="batfill" style="width:0%"></div></div>
            <div class="battxt"><span id="bat-v">–</span><small id="bat-s">–</small></div>
          </div>
          <div class="netkv"><span id="net-v">–</span><small id="net-s">net</small></div>
        </div>
      </ha-card>`;
    this._el = (id) => root.getElementById(id);
    this._q = (sel) => root.querySelector(sel);
    this._qa = (sel) => [...root.querySelectorAll(sel)];
  }

  _opt(lvl, id) { return this._q(`.lvl[data-lvl="${lvl}"] .opt[data-opt="${id}"]`); }

  _setLevel(lvl, activeId, tint, subText) {
    const box = this._q(`.lvl[data-lvl="${lvl}"]`);
    this._qa(`.lvl[data-lvl="${lvl}"] .opt`).forEach(o => {
      o.className = "opt" + (o.dataset.opt === activeId
        ? " active" + (tint ? " tint-" + tint : "") : "");
    });
    let sub = box.querySelector(".sub");
    if (subText) {
      if (!sub) { sub = document.createElement("div"); sub.className = "sub"; box.appendChild(sub); }
      sub.textContent = subText;
    } else if (sub) sub.remove();
  }

  _thought(advies, a, b, fout) {
    const C = WattsonTreeCard;
    const piek = (a.volgende_actie || "").match(/om (\d\d:\d\d)/);
    const t = piek ? piek[1] : "vanavond";
    if (fout) return `<span class="red">Er is ingegrepen:</span> ${fout}`;
    if (advies === "geen data") return "Wattson wacht op verse metingen…";
    if (advies === "rust (EV-guard)" || advies === "rust (EV-check)")
      return `De <b>auto</b> laadt — de accu blijft er bewust vanaf; het huis krijgt zo nodig zijn eigen deel.`;
    if (advies === "verkopen")
      return `De accu <span class="green"><b>verkoopt aan het net</b></span> voor <b>${C._ct(b.prijs_nu)}</b> per kWh — meer dan bewaren nu waard is.`;
    if (advies === "ontladen")
      return `De accu <span class="green"><b>levert het huis</b></span> — netstroom kost nu <b>${C._ct(b.prijs_nu)}</b>, de accu heeft hem goedkoper ingeslagen.`;
    if (advies === "bijspringen: ontladen")
      return `Onverwachte piek: de accu <span class="green"><b>springt bij</b></span> zodat er geen dure netstroom (<b>${C._ct(b.prijs_nu)}</b>) nodig is.`;
    if (advies === "bijspringen: laden")
      return `Zonoverschot: de accu <span class="amber"><b>vangt het op</b></span> in plaats van het weg te geven.`;
    if (advies === "laden") {
      const zon = /smart_charging/.test(a.laatst_gestuurd || "");
      return zon
        ? `De <span class="amber"><b>zon vult de accu</b></span> — die stroom is om ${t} zo'n <b>${C._ct(b.marginale_waarde_eur_kwh)}</b> per kWh waard.`
        : `Wattson <span class="amber"><b>koopt nu goedkoop in</b></span> (${C._ct(b.prijs_nu)}) voor de piek van ${t}.`;
    }
    // rust
    const vol = (b.soc_pct || 0) >= 97;
    return `${vol ? "De accu is <b>vol</b> en" : "Wattson"} <b>wacht</b> op het duurste moment${piek ? `: om <b>${t}</b> gaat hij leveren` : ""} — elke bewaarde kWh is straks <b>${C._ct(b.marginale_waarde_eur_kwh)}</b> waard.`;
  }

  _update() {
    if (!this._hass) return;
    const C = WattsonTreeCard;
    const adv = this._st(this._config.entity);
    if (!adv) return;
    const a = adv.attributes || {};
    const b = a.berekend_met || {};
    const advies = adv.state;
    const fout = a.fout;
    const gestuurd = a.laatst_gestuurd || "";

    // kop
    const chip = this._el("chip");
    chip.textContent = fout ? "fout" : advies;
    chip.className = "state-chip " + (fout ? "fout"
      : /laden/.test(advies) && !/ontladen/.test(advies) ? "laden"
      : /ontladen|verkopen/.test(advies) ? "ontladen" : "");
    const sav = this._num(this._config.besparing);
    this._el("save").innerHTML = sav === null ? "" : `plan <b>+€ ${C._nl(sav, 2)}</b>`;

    // gedachte
    this._el("thought").innerHTML = this._thought(advies, a, b, fout);

    // prijs-zone-meter
    const prijs = b.prijs_nu, lam = b.marginale_waarde_eur_kwh;
    const plaf = b.laadplafond_eur_kwh, vloer = b.ontlaadvloer_eur_kwh;
    if ([prijs, plaf, vloer].every(v => typeof v === "number")) {
      const pad = Math.max((vloer - plaf) * 0.35, 0.03);
      const lo = Math.min(plaf, prijs) - pad, hi = Math.max(vloer, prijs) + pad;
      const pct = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
      const pPlaf = pct(plaf), pVloer = pct(vloer);
      this._el("gz-laden").style.width = pPlaf + "%";
      const bew = this._el("gz-bewaren");
      bew.style.left = pPlaf + "%"; bew.style.width = Math.max(pVloer - pPlaf, 0) + "%";
      this._el("gz-ontladen").style.width = (100 - pVloer) + "%";
      const needle = this._el("needle");
      needle.style.left = pct(prijs) + "%";
      needle.dataset.label = C._ct(prijs);
      const lt = this._el("lamtick");
      if (typeof lam === "number") { lt.style.display = ""; lt.style.left = pct(lam) + "%"; }
      else lt.style.display = "none";
      this._el("g-lo").textContent = C._ct(lo);
      this._el("g-hi").textContent = C._ct(hi);
    }

    // niveau 1: sturing
    const actief = !!a.sturing_actief;
    this._setLevel("sturing", actief ? "actief" : "schaduw",
      actief ? "green" : "amber", actief ? null : "Wattson adviseert alleen — stuurt niet");

    // niveau 2: veiligheid-gates
    const watchTrip = typeof fout === "string" && fout.startsWith("WATCHDOG");
    const staleErr = typeof fout === "string" && !watchTrip && /telemetrie|stil/i.test(fout);
    const geenData = advies === "geen data";
    const evBlock = advies === "rust (EV-guard)" || advies === "rust (EV-check)";
    const gates = { data: !geenData, watchdog: !watchTrip, stale: !staleErr, ev: !evBlock };
    let blockedGate = null;
    this._qa('.lvl[data-lvl="gates"] .opt').forEach(o => {
      const ok = gates[o.dataset.opt];
      o.className = "opt " + (ok ? "ok" : "blocked");
      if (!ok && !blockedGate) blockedGate = o.dataset.opt;
    });
    const gateBox = this._q('.lvl[data-lvl="gates"]');
    let gsub = gateBox.querySelector(".sub");
    const gateTxt = watchTrip ? fout
      : evBlock ? (b.ev_laadt ? "auto laadt — accu geblokkeerd voor de auto" : a.reden)
      : staleErr ? fout : null;
    if (gateTxt) {
      if (!gsub) { gsub = document.createElement("div"); gsub.className = "sub"; gateBox.appendChild(gsub); }
      gsub.textContent = gateTxt;
    } else if (gsub) gsub.remove();

    // niveau 3: doelfunctie
    const aggro = this._st(this._config.agressiviteit);
    const aggroId = (aggro && aggro.state) || a.agressiviteit || "gebalanceerd";
    const sell = this._st(this._config.sw_sell);
    const sellOn = sell && sell.state === "on";
    this._setLevel("aggro", aggroId, null,
      `zelfvoorziening telt voor ${C._ct(b.voorkeur_zelfvoorziening_eur_kwh)}/kWh mee`
      + ` · ${b.scenario || "–"} · verkopen ${sellOn ? "gewapend" : "uit"}`);

    // niveau 4: plan-tak
    let planId = "rust", planTint = null;
    if (advies === "laden" || advies === "bijspringen: laden") {
      planId = /smart_charging/.test(gestuurd) ? "laden_zon" : "laden_net";
      planTint = "amber";
    } else if (advies === "ontladen" || advies === "bijspringen: ontladen") {
      planId = "ontladen"; planTint = "green";
    } else if (advies === "verkopen") {
      planId = "verkopen"; planTint = "green";
    }
    const sp = a.setpoint_w;
    this._setLevel("plan", planId, planTint,
      (sp ? `setpoint ${sp > 0 ? "+" : ""}${C._nl(sp)} W · ` : "") + (a.reden || ""));

    // niveau 5: realtime
    const rtId = advies === "bijspringen: laden" ? "assist_laden"
      : advies === "bijspringen: ontladen" ? "assist_ontladen" : "volgt";
    this._setLevel("rt", rtId, rtId === "volgt" ? null : planTint,
      rtId === "volgt" ? `bijspringen ${a.bijspringen || "uit"} · volgende: ${a.volgende_actie || "–"}` : a.reden);

    // niveau 6: apparaat
    const mode = this._st(this._config.mode);
    const modeId = mode && ["off", "manual", "smart_charging", "smart_discharging"].includes(mode.state)
      ? mode.state : "off";
    const ac = this._st(this._config.acmode);
    const chg = this._num(this._config.chg_w);
    const dis = this._num(this._config.dis_w);
    const devTint = (chg || 0) > 50 ? "amber" : (dis || 0) > 50 ? "green" : null;
    this._setLevel("device", modeId, devTint, `ac ${ac ? ac.state : "–"} · gestuurd: ${gestuurd || "–"}`);

    // strip: accu-glyph + net
    const socP = this._num(this._config.soc);
    const fill = this._el("batfill");
    fill.style.width = `${socP || 0}%`;
    fill.className = "batfill" + ((chg || 0) > 50 ? " chg" : "");
    this._el("bat-v").textContent = socP === null ? "–"
      : `${C._nl(socP)}%${b.soc_kwh !== undefined ? " · " + C._nl(b.soc_kwh, 2) + " kWh" : ""}`;
    this._el("bat-s").textContent = (chg || 0) > 50 ? `laadt met ${C._nl(chg)} W`
      : (dis || 0) > 50 ? `levert ${C._nl(dis)} W` : "in rust";
    const p1 = this._num(this._config.p1);
    this._el("net-v").textContent = p1 === null ? "–" : `${C._nl(Math.abs(p1))} W`;
    this._el("net-s").textContent = p1 === null ? "net" : p1 >= 0 ? "import van net" : "export naar net";

    // draad langs het actieve pad
    this._path = { blockedGate, planId, rtId, modeId, actiefId: actief ? "actief" : "schaduw",
                   aggroId, tint: fout ? "red" : planTint || "neutral" };
    requestAnimationFrame(() => this._wire());
    setTimeout(() => this._wire(), 120);
  }

  _wire() {
    if (!this._built || !this._path) return;
    const svg = this._el("wires");
    const tree = this._el("tree");
    if (!svg || !tree) return;
    const tb = tree.getBoundingClientRect();
    if (tb.width === 0) return;
    const P = this._path;
    const colors = { amber: "#ffb86b", green: "#4cc88a", red: "#ff6b81", neutral: "rgba(226,224,212,.55)" };
    const color = colors[P.tint] || colors.neutral;
    const pt = (el, edge) => {
      const r = el.getBoundingClientRect();
      return [r.left - tb.left + r.width / 2, (edge === "top" ? r.top : r.bottom) - tb.top];
    };
    const stops = [];
    stops.push(pt(this._opt("sturing", P.actiefId), "bottom"));
    const gateEls = this._qa('.lvl[data-lvl="gates"] .opt');
    let stopAt = null;
    for (const g of gateEls) {
      const r = g.getBoundingClientRect();
      stops.push([r.left - tb.left + r.width / 2, r.top - tb.top + r.height / 2]);
      if (P.blockedGate && g.dataset.opt === P.blockedGate) { stopAt = true; break; }
    }
    if (!stopAt) {
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
    svg.innerHTML = `
      <path class="glowline" d="${d}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="1.4" stroke-linecap="round" opacity=".55"/>
      <path class="flowline" d="${d}" fill="none" stroke="${color}" stroke-width="2.6" stroke-linecap="round"/>
      <circle class="end" cx="${end[0]}" cy="${end[1]}" r="3" fill="${color}"/>`;
  }
}

customElements.define("wattson-tree-card", WattsonTreeCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "wattson-tree-card",
  name: "Wattson beslisboom",
  description: "De complete beslisboom van Wattson: alle opties, geanimeerde energiedraad, prijs-zone-meter en mensentaal.",
});
