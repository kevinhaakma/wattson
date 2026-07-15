/**
 * wattson-tree-card — de complete state tree van Wattson v3, live.
 *
 * Toont de hele beslisketen als boom met het actieve pad opgelicht:
 * Veiligheid -> Doelfunctie -> Plan (DP + λ) -> Realtime -> Apparaat.
 * Elke node draagt zijn actuele waarde; de λ-regel (bewaarwaarde vs
 * ontlaadvloer/laadplafond) staat expliciet naast de actuele prijs.
 *
 * type: custom:wattson-tree-card         # alle entities hebben defaults
 * Stijl: overzicht "glass" (zelfde tokens als wattson-brain-card), geen
 * emojis, build-once/patch.
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

  getCardSize() { return 8; }
  static getStubConfig() { return {}; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

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

  _build() {
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
    const node = (id, label) => `
      <div class="node" id="${id}">
        <span class="nl">${label}</span>
        <span class="nv" data-el="v">–</span>
        <span class="nx" data-el="x"></span>
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
        .head { display:flex; align-items:center; gap:8px; margin-bottom:12px; }
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

        .branch { margin-bottom:2px; }
        .btitle {
          display:flex; align-items:center; gap:7px;
          font-size:10px; font-weight:600; letter-spacing:.14em; text-transform:uppercase;
          color:rgba(255,255,255,.34); padding:7px 0 3px;
        }
        .btitle ha-icon { --mdc-icon-size:13px; color:rgba(167,173,162,.6); }
        .branch.lit .btitle { color:rgba(255,255,255,.62); }
        .branch.lit .btitle ha-icon { color:#a7ada2; }
        .branch.alarm .btitle { color:#ff6b81; }
        .branch.alarm .btitle ha-icon { color:#ff6b81; }
        .nodes { border-left:1px solid rgba(226,224,212,.10); margin-left:6px; padding-left:0; }
        .node {
          display:grid; grid-template-columns:128px auto 1fr; gap:10px; align-items:baseline;
          padding:2.5px 8px 2.5px 14px; border-radius:0 6px 6px 0; position:relative;
        }
        .node::before {
          content:""; position:absolute; left:0; top:50%; width:9px; height:1px;
          background:rgba(226,224,212,.10);
        }
        .node .nl { color:rgba(255,255,255,.42); font-size:11.5px; }
        .node .nv { color:rgba(255,255,255,.94); font-weight:500; white-space:nowrap; }
        .node .nx { color:rgba(255,255,255,.34); font-size:11px; overflow:hidden;
                    text-overflow:ellipsis; white-space:nowrap; }
        .node.on  { background:rgba(255,255,255,.05); }
        .node.on .nv { color:#4cc88a; }
        .node.warn .nv { color:#ffb86b; }
        .node.bad  { background:rgba(255,107,129,.07); }
        .node.bad .nv { color:#ff6b81; }
        .node.dim .nv { color:rgba(255,255,255,.42); font-weight:400; }
        .socbar { grid-column:3; height:4px; border-radius:2px; align-self:center;
                  background:rgba(255,255,255,.08); overflow:hidden; }
        .socbar i { display:block; height:100%; background:#4cc88a; border-radius:2px; }
      </style>
      <ha-card>
        <div class="head">
          <ha-icon icon="mdi:file-tree-outline"></ha-icon>
          <span class="t">Wattson — state tree</span>
          <span class="state-chip" id="chip">–</span>
        </div>

        <div class="branch" id="b-veilig">
          <div class="btitle"><ha-icon icon="mdi:shield-half-full"></ha-icon>Veiligheid</div>
          <div class="nodes">
            ${node("n-watchdog", "watchdog")}
            ${node("n-stale", "telemetrie")}
            ${node("n-ev", "EV-guard")}
          </div>
        </div>

        <div class="branch" id="b-doel">
          <div class="btitle"><ha-icon icon="mdi:target"></ha-icon>Doelfunctie</div>
          <div class="nodes">
            ${node("n-aggro", "agressiviteit")}
            ${node("n-scenario", "scenario")}
            ${node("n-verkopen", "verkopen")}
          </div>
        </div>

        <div class="branch" id="b-plan">
          <div class="btitle"><ha-icon icon="mdi:chart-timeline-variant"></ha-icon>Plan (DP)</div>
          <div class="nodes">
            ${node("n-advies", "advies")}
            ${node("n-lambda", "bewaarwaarde λ")}
            ${node("n-vloer", "ontlaadvloer")}
            ${node("n-plafond", "laadplafond")}
            ${node("n-volgende", "volgende actie")}
            ${node("n-horizon", "horizon")}
          </div>
        </div>

        <div class="branch" id="b-realtime">
          <div class="btitle"><ha-icon icon="mdi:flash-auto"></ha-icon>Realtime</div>
          <div class="nodes">
            ${node("n-assist", "bijspringen")}
            ${node("n-gestuurd", "laatst gestuurd")}
          </div>
        </div>

        <div class="branch" id="b-app">
          <div class="btitle"><ha-icon icon="mdi:battery-charging-outline"></ha-icon>Apparaat</div>
          <div class="nodes">
            ${node("n-mode", "modus")}
            ${node("n-power", "vermogen")}
            <div class="node" id="n-soc">
              <span class="nl">voorraad</span>
              <span class="nv" data-el="v">–</span>
              <span class="socbar"><i data-el="bar" style="width:0%"></i></span>
            </div>
          </div>
        </div>
      </ha-card>`;
    this._el = (id) => root.getElementById(id);
  }

  _setNode(id, value, extra, cls) {
    const n = this._el(id);
    if (!n) return;
    n.className = "node" + (cls ? " " + cls : "");
    n.querySelector('[data-el="v"]').textContent = value;
    const x = n.querySelector('[data-el="x"]');
    if (x) x.textContent = extra || "";
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

    // kop-chip
    const chip = this._el("chip");
    chip.textContent = fout ? "fout" : advies;
    chip.className = "state-chip " + (fout ? "fout"
      : /laden/.test(advies) && !/ontladen/.test(advies) ? "laden"
      : /ontladen|verkopen/.test(advies) ? "ontladen" : "");

    // veiligheid
    const watchErr = typeof fout === "string" && fout.startsWith("WATCHDOG");
    this._setNode("n-watchdog", watchErr ? "TRIP" : "ok",
      watchErr ? fout : "", watchErr ? "bad" : "on");
    this._setNode("n-stale", a.watchdog_telemetrie || "–",
      fout && !watchErr ? fout : "",
      fout && !watchErr ? "bad" : a.watchdog_telemetrie === "actief" ? "on" : "dim");
    const evGuard = advies === "rust (EV-guard)" || advies === "rust (EV-check)";
    this._setNode("n-ev", b.ev_laadt ? "auto laadt" : "vrij",
      evGuard ? "accu geblokkeerd voor de auto" : "",
      b.ev_laadt ? "warn" : "dim");
    this._el("b-veilig").className = "branch" + (watchErr || fout ? " alarm" : "");

    // doelfunctie
    const aggro = this._st(this._config.agressiviteit);
    const pref = b.voorkeur_zelfvoorziening_eur_kwh;
    this._setNode("n-aggro", aggro ? aggro.state : (a.agressiviteit || "–"),
      pref !== undefined ? `zelfvoorziening weegt ${C._eur(pref)}/kWh mee` : "", "");
    this._setNode("n-scenario", b.scenario || "–",
      a.scenario_waarschuwing || (b.scenario === "saldering" ? "export = volle uurprijs" : ""),
      b.scenario_waarschuwing ? "warn" : "dim");
    const sell = this._st(this._config.sw_sell);
    this._setNode("n-verkopen", sell && sell.state === "on" ? "gewapend" : "uit",
      sell && sell.state === "on" ? "DP beslist per uur of exporteren loont" : "",
      sell && sell.state === "on" ? "on" : "dim");

    // plan
    const sp = a.setpoint_w;
    this._setNode("n-advies",
      advies + (sp ? ` ${sp > 0 ? "+" : ""}${C._nl(sp)} W` : ""),
      a.reden || "", /laden|ontladen|verkopen/.test(advies) ? "on" : "");
    const lam = b.marginale_waarde_eur_kwh;
    const prijs = b.prijs_nu;
    this._setNode("n-lambda", C._eur(lam),
      b.soc_pct !== undefined ? `per kWh in de accu bij ${C._nl(b.soc_pct)}% SoC` : "");
    const vloer = b.ontlaadvloer_eur_kwh;
    const boven = prijs !== undefined && vloer !== undefined && prijs > vloer;
    this._setNode("n-vloer", C._eur(vloer),
      prijs !== undefined ? `prijs nu ${C._eur(prijs)} → ${boven ? "ontladen loont" : "bewaren"}` : "",
      boven ? "on" : "dim");
    const plafond = b.laadplafond_eur_kwh;
    const onder = prijs !== undefined && plafond !== undefined && prijs < plafond;
    this._setNode("n-plafond", C._eur(plafond),
      prijs !== undefined ? `prijs nu ${C._eur(prijs)} → ${onder ? "laden loont" : "niet netladen"}` : "",
      onder ? "on" : "dim");
    this._setNode("n-volgende", a.volgende_actie || "–", "", a.volgende_actie ? "" : "dim");
    this._setNode("n-horizon",
      b.zelfvoorziening_horizon_pct !== undefined ? C._nl(b.zelfvoorziening_horizon_pct) + "% eigen" : "–",
      `vraag ${C._nl(b.verwachte_vraag_horizon_kwh, 1)} kWh · restant ${C._nl(b.verwacht_restant_einde_kwh, 2)} kWh · ${b.horizon_uren || "–"} u`);

    // realtime
    const assistActive = /^bijspringen/.test(advies);
    this._setNode("n-assist", assistActive ? advies.replace("bijspringen: ", "actief: ") : (a.bijspringen || "–"),
      assistActive ? a.reden : "", assistActive ? "on" : "dim");
    const sturing = a.sturing_actief;
    this._setNode("n-gestuurd", a.laatst_gestuurd || "–",
      sturing ? "" : "schaduwmodus — Wattson stuurt niet",
      sturing ? "" : "warn");
    this._el("b-realtime").className = "branch" + (assistActive ? " lit" : "");
    this._el("b-plan").className = "branch" + (assistActive ? "" : " lit");

    // apparaat
    const mode = this._st(this._config.mode);
    const ac = this._st(this._config.acmode);
    this._setNode("n-mode", mode ? mode.state : "–", ac ? `ac ${ac.state}` : "",
      mode && mode.state !== "off" ? "on" : "dim");
    const chg = this._num(this._config.chg_w);
    const dis = this._num(this._config.dis_w);
    const p1 = this._num(this._config.p1);
    const actief = (chg || 0) > 50 ? `laadt ${C._nl(chg)} W`
      : (dis || 0) > 50 ? `ontlaadt ${C._nl(dis)} W` : "stil";
    this._setNode("n-power", actief,
      p1 !== null ? `net ${p1 >= 0 ? "import" : "export"} ${C._nl(Math.abs(p1))} W` : "",
      (chg || 0) > 50 ? "warn" : (dis || 0) > 50 ? "on" : "dim");
    const socP = this._num(this._config.soc);
    const socN = this._el("n-soc");
    socN.querySelector('[data-el="v"]').textContent =
      socP === null ? "–" : `${C._nl(socP)}% · ${C._nl(b.soc_kwh, 2)} kWh`;
    socN.querySelector('[data-el="bar"]').style.width = `${socP || 0}%`;
    this._el("b-app").className = "branch" + (mode && mode.state !== "off" ? " lit" : "");
  }
}

customElements.define("wattson-tree-card", WattsonTreeCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "wattson-tree-card",
  name: "Wattson state tree",
  description: "De complete live beslisboom van Wattson: veiligheid, doelfunctie, plan (λ), realtime en apparaat.",
});
