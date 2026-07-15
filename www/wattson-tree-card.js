/**
 * wattson-tree-card — de complete beslisboom van Wattson v3 als échte tree.
 *
 * Elk niveau toont ÁLLE opties (ook de takken die nu niet gekozen zijn); een
 * getekende draad volgt het actieve pad van boven naar beneden:
 *
 *   sturing -> veiligheid (4 gates) -> doelfunctie -> plan-tak (5 opties)
 *           -> realtime-override -> apparaatmodus
 *
 * Onderin staat de λ-regel die de plan-tak bepaalt (bewaarwaarde, vloer,
 * plafond, actuele prijs) plus de reden-tekst en de apparaat-strip.
 *
 * type: custom:wattson-tree-card         # alle entities hebben defaults
 * Stijl: overzicht "glass", geen emojis, build-once/patch.
 */
class WattsonTreeCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: "sensor.wattson_advies",
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

  getCardSize() { return 9; }
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
  static _eur(v) { return v === null || v === undefined ? "–" : "€ " + WattsonTreeCard._nl(v, 3); }

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
      { key: "plan", label: "plan (DP · λ-regel)", opts: [
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
        }
        .head { display:flex; align-items:center; gap:8px; margin-bottom:14px; }
        .head ha-icon { --mdc-icon-size:17px; color:#a7ada2; }
        .head .t { font-size:12px; font-weight:600; letter-spacing:.12em;
                   text-transform:uppercase; color:rgba(255,255,255,.56); flex:1; }
        .state-chip {
          font-size:11px; font-weight:600; letter-spacing:.06em; text-transform:uppercase;
          padding:3px 10px; border-radius:11px;
          background:rgba(255,255,255,.06); border:1px solid rgba(226,224,212,.13);
          color:rgba(255,255,255,.56); white-space:nowrap;
        }
        .state-chip.laden   { color:#ffb86b; border-color:rgba(255,184,107,.4); background:rgba(255,184,107,.10); }
        .state-chip.ontladen{ color:#4cc88a; border-color:rgba(76,200,138,.4);  background:rgba(76,200,138,.10); }
        .state-chip.fout    { color:#ff6b81; border-color:rgba(255,107,129,.4); background:rgba(255,107,129,.10); }

        .tree { position:relative; }
        svg.wires { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
        .lvl { position:relative; padding:11px 0 11px; }
        .lvl + .lvl { margin-top:8px; }
        .lt { display:block; font-size:9.5px; font-weight:600; letter-spacing:.14em;
              text-transform:uppercase; color:rgba(255,255,255,.30); margin:0 0 6px; }
        .opts { display:flex; gap:6px; flex-wrap:wrap; position:relative; }
        .opt {
          font-size:11px; color:rgba(255,255,255,.40);
          background:rgba(255,255,255,.035); border:1px solid rgba(226,224,212,.07);
          border-radius:9px; padding:3px 10px; white-space:nowrap; position:relative; z-index:1;
          transition: all .25s ease;
        }
        .opt.active {
          color:rgba(255,255,255,.96); font-weight:600;
          background:rgba(255,255,255,.09); border-color:rgba(226,224,212,.30);
          box-shadow:0 0 0 1px rgba(255,255,255,.04), 0 2px 10px rgba(0,0,0,.25);
        }
        .opt.active.tint-amber { color:#ffb86b; border-color:rgba(255,184,107,.5); background:rgba(255,184,107,.10); }
        .opt.active.tint-green { color:#4cc88a; border-color:rgba(76,200,138,.5);  background:rgba(76,200,138,.10); }
        .opt.active.tint-red   { color:#ff6b81; border-color:rgba(255,107,129,.5); background:rgba(255,107,129,.10); }
        .opts.gates .opt.ok { color:rgba(76,200,138,.75); border-color:rgba(76,200,138,.22); background:rgba(76,200,138,.05); }
        .opts.gates .opt.blocked { color:#ff6b81; font-weight:600; border-color:rgba(255,107,129,.5); background:rgba(255,107,129,.12); }
        .sub { font-size:10.5px; color:rgba(255,255,255,.38); margin-top:5px;
               overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

        .rule {
          display:flex; gap:6px; flex-wrap:wrap; align-items:center;
          background:rgba(255,255,255,.05); border:1px solid rgba(226,224,212,.13);
          border-radius:7px; padding:8px 11px; margin:14px 0 10px; line-height:1.6;
        }
        .rule .rl { font-size:10px; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
                    color:rgba(255,255,255,.34); width:100%; }
        .kv { font-size:11px; color:rgba(255,255,255,.56); background:rgba(255,255,255,.046);
              border:1px solid rgba(226,224,212,.075); border-radius:9px; padding:2px 8px; white-space:nowrap; }
        .kv b { color:rgba(255,255,255,.94); font-weight:500; }
        .kv.hit b { color:#4cc88a; }
        .reden { color:rgba(255,255,255,.62); font-size:12px; margin:0 0 12px; min-height:1.4em;
                 overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

        .strip { display:flex; align-items:center; gap:12px; padding-top:10px;
                 border-top:1px solid rgba(226,224,212,.075); }
        .strip .kv { flex:none; }
        .socwrap { flex:1; display:flex; align-items:center; gap:8px; }
        .socbar { flex:1; height:4px; border-radius:2px; background:rgba(255,255,255,.08); overflow:hidden; }
        .socbar i { display:block; height:100%; background:#4cc88a; border-radius:2px; transition:width .4s ease; }
        .soctxt { font-size:11px; color:rgba(255,255,255,.56); white-space:nowrap; }
      </style>
      <ha-card>
        <div class="head">
          <ha-icon icon="mdi:file-tree-outline"></ha-icon>
          <span class="t">Wattson — beslisboom</span>
          <span class="state-chip" id="chip">–</span>
        </div>
        <div class="tree" id="tree">
          <svg class="wires" id="wires"></svg>
          ${WattsonTreeCard.LEVELS.map(lvl).join("")}
        </div>
        <div class="rule">
          <span class="rl">De λ-regel — wat een kWh in de accu nu waard is</span>
          <span class="kv">bewaarwaarde λ <b id="r-lam">–</b></span>
          <span class="kv" id="r-vloer-w">ontlaadvloer <b id="r-vloer">–</b></span>
          <span class="kv" id="r-plaf-w">laadplafond <b id="r-plaf">–</b></span>
          <span class="kv">prijs nu <b id="r-prijs">–</b></span>
          <span class="kv">zelfvoorziening <b id="r-zelf">–</b></span>
        </div>
        <p class="reden" id="reden">–</p>
        <div class="strip">
          <span class="kv">accu <b id="s-power">–</b></span>
          <span class="kv">net <b id="s-net">–</b></span>
          <div class="socwrap">
            <div class="socbar"><i id="s-bar" style="width:0%"></i></div>
            <span class="soctxt" id="s-soc">–</span>
          </div>
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

    // kop-chip
    const chip = this._el("chip");
    chip.textContent = fout ? "fout" : advies;
    chip.className = "state-chip " + (fout ? "fout"
      : /laden/.test(advies) && !/ontladen/.test(advies) ? "laden"
      : /ontladen|verkopen/.test(advies) ? "ontladen" : "");

    // niveau 1: sturing
    const actief = !!a.sturing_actief;
    this._setLevel("sturing", actief ? "actief" : "schaduw",
      actief ? "green" : "amber", actief ? null : "Wattson adviseert alleen — stuurt niet");

    // niveau 2: veiligheid-gates (allemaal zichtbaar, rood = pad stopt hier)
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

    // niveau 3: doelfunctie (agressiviteit = de knop op alpha/beta/slijtage)
    const aggro = this._st(this._config.agressiviteit);
    const aggroId = (aggro && aggro.state) || a.agressiviteit || "gebalanceerd";
    const sell = this._st(this._config.sw_sell);
    const sellOn = sell && sell.state === "on";
    const pref = b.voorkeur_zelfvoorziening_eur_kwh;
    this._setLevel("aggro", aggroId, null,
      `zelfvoorziening weegt ${C._eur(pref)}/kWh mee · scenario ${b.scenario || "–"}`
      + ` · verkopen ${sellOn ? "gewapend" : "uit"}`);

    // niveau 4: plan-tak (5 opties; welke koos de DP dit uur)
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
      (sp ? `setpoint ${sp > 0 ? "+" : ""}${C._nl(sp)} W · ` : "") + (a.volgende_actie || ""));

    // niveau 5: realtime-override
    const rtId = advies === "bijspringen: laden" ? "assist_laden"
      : advies === "bijspringen: ontladen" ? "assist_ontladen" : "volgt";
    this._setLevel("rt", rtId, rtId === "volgt" ? null : planTint,
      rtId === "volgt" ? `bijspringen ${a.bijspringen || "uit"}` : a.reden);

    // niveau 6: apparaat
    const mode = this._st(this._config.mode);
    const modeId = mode && ["off", "manual", "smart_charging", "smart_discharging"].includes(mode.state)
      ? mode.state : "off";
    const ac = this._st(this._config.acmode);
    const chg = this._num(this._config.chg_w);
    const dis = this._num(this._config.dis_w);
    const devTint = (chg || 0) > 50 ? "amber" : (dis || 0) > 50 ? "green" : null;
    this._setLevel("device", modeId, devTint,
      `ac ${ac ? ac.state : "–"} · gestuurd: ${gestuurd || "–"}`);

    // λ-regel
    const prijs = b.prijs_nu, lam = b.marginale_waarde_eur_kwh;
    const vloer = b.ontlaadvloer_eur_kwh, plaf = b.laadplafond_eur_kwh;
    this._el("r-lam").textContent = C._eur(lam);
    this._el("r-vloer").textContent = C._eur(vloer);
    this._el("r-plaf").textContent = C._eur(plaf);
    this._el("r-prijs").textContent = C._eur(prijs);
    this._el("r-zelf").textContent = b.zelfvoorziening_horizon_pct !== undefined
      ? C._nl(b.zelfvoorziening_horizon_pct) + "%" : "–";
    this._el("r-vloer-w").className = "kv" + (prijs > vloer ? " hit" : "");
    this._el("r-plaf-w").className = "kv" + (prijs < plaf ? " hit" : "");
    this._el("reden").textContent = a.reden || "–";

    // apparaat-strip
    this._el("s-power").textContent = (chg || 0) > 50 ? `laadt ${C._nl(chg)} W`
      : (dis || 0) > 50 ? `ontlaadt ${C._nl(dis)} W` : "stil";
    const p1 = this._num(this._config.p1);
    this._el("s-net").textContent = p1 === null ? "–"
      : `${p1 >= 0 ? "import" : "export"} ${C._nl(Math.abs(p1))} W`;
    const socP = this._num(this._config.soc);
    this._el("s-bar").style.width = `${socP || 0}%`;
    this._el("s-soc").textContent = socP === null ? "–"
      : `${C._nl(socP)}%${b.soc_kwh !== undefined ? " · " + C._nl(b.soc_kwh, 2) + " kWh" : ""}`;

    // draad tekenen langs het actieve pad
    this._path = { blockedGate, planId, rtId, modeId, actiefId: actief ? "actief" : "schaduw",
                   aggroId, tint: fout ? "red" : planTint || "neutral" };
    // draad na layout tekenen; de late timer vangt het allereerste render
    // (element nog niet gemeten) — daarna houdt de ResizeObserver hem bij
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
    const colors = { amber: "#ffb86b", green: "#4cc88a", red: "#ff6b81", neutral: "rgba(226,224,212,.45)" };
    const color = colors[P.tint] || colors.neutral;
    const pt = (el, edge) => {
      const r = el.getBoundingClientRect();
      return [r.left - tb.left + r.width / 2, (edge === "top" ? r.top : r.bottom) - tb.top];
    };
    // route: sturing -> alle gates (dwars door de rij) -> aggro -> plan -> rt -> device
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
      if (Math.abs(py - y) < 2) d += ` L ${x} ${y}`;                    // horizontaal (gates)
      else d += ` C ${px} ${py + (y - py) * 0.55}, ${x} ${y - (y - py) * 0.55}, ${x} ${y}`;
    }
    const end = stops[stops.length - 1] || [0, 0];
    svg.innerHTML = `
      <path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"
            stroke-linecap="round" opacity=".85"/>
      <circle cx="${end[0]}" cy="${end[1]}" r="3" fill="${color}" opacity=".9"/>`;
  }
}

customElements.define("wattson-tree-card", WattsonTreeCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "wattson-tree-card",
  name: "Wattson beslisboom",
  description: "De complete beslisboom van Wattson met alle opties per niveau en het actieve pad als draad.",
});
