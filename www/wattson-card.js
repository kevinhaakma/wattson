/**
 * wattson-card — kleine, vanilla Lovelace-kaart voor Wattson.
 *
 * Toont het advies (groot, kleur per status), het huidige setpoint, de SoC en
 * een mini-balkgrafiekje op basis van het `plan`-attribuut van de advies-sensor:
 *   plan: [{ tijd, prijs, setpoint_w, soc_na_kwh }, ...]
 *
 * Configuratie (YAML of UI):
 *   type: custom:wattson-card
 *   entity: sensor.wattson_advies      # optioneel, dit is de default
 *   title: Wattson                     # optioneel
 *   hours: 12                          # optioneel, aantal uren in de mini-grafiek
 *
 * Geen dependencies: puur vanilla JS + Shadow DOM, stijl via HA CSS-variabelen
 * zodat de kaart automatisch meekleurt met elk (ook het Wattson-)thema.
 */
class WattsonCard extends HTMLElement {
  setConfig(config) {
    if (!config) {
      throw new Error("Ongeldige configuratie voor wattson-card");
    }
    this._config = {
      entity: "sensor.wattson_advies",
      title: "Wattson",
      hours: 12,
      icon: "/hacsfiles/wattson/wattson-icon.png",
      ...config,
    };
    this._built = false;
  }

  getCardSize() {
    return 4;
  }

  static getStubConfig() {
    return { entity: "sensor.wattson_advies" };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) {
      this._build();
    }
    this._update();
  }

  _build() {
    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `
      <style>${WattsonCard._css()}</style>
      <ha-card>
        <div class="wc-header">
          <div class="wc-brand">
            <button class="wc-icon-trigger wc-popup-trigger" type="button"
                    aria-label="Open Wattson-details" title="Wat doet Wattson?">
              <img class="wc-brand-icon" src="${this._config.icon}" alt="" aria-hidden="true">
            </button>
            <span class="wc-title"></span>
          </div>
          <span class="wc-trained"></span>
        </div>
        <div class="wc-body">
          <div class="wc-advies-row">
            <div class="wc-advies"></div>
            <div class="wc-stats">
              <div class="wc-stat">
                <span class="wc-stat-label">setpoint</span>
                <span class="wc-stat-value wc-setpoint"></span>
              </div>
              <button class="wc-stat wc-soc-trigger wc-popup-trigger" type="button"
                      aria-label="Open batterij- en Wattson-details" title="Wat doet Wattson?">
                <span class="wc-stat-label">SoC</span>
                <span class="wc-stat-value wc-soc"></span>
                <span class="wc-stat-hint">details</span>
              </button>
              <div class="wc-stat">
                <span class="wc-stat-label">verwachte besparing</span>
                <span class="wc-stat-value wc-besparing"></span>
              </div>
            </div>
          </div>
          <div class="wc-chart"></div>
          <div class="wc-note"></div>
        </div>
        <div class="wc-dialog" role="dialog" aria-modal="true"
             aria-labelledby="wc-dialog-title" hidden>
          <div class="wc-dialog-shell">
            <div class="wc-dialog-header">
              <div class="wc-dialog-heading">
                <img class="wc-dialog-icon" src="${this._config.icon}" alt="" aria-hidden="true">
                <div>
                  <div class="wc-dialog-eyebrow">Live batterijstatus</div>
                  <div class="wc-dialog-title" id="wc-dialog-title">Wat doet Wattson?</div>
                </div>
              </div>
              <button class="wc-dialog-close" type="button" aria-label="Sluiten">×</button>
            </div>
            <div class="wc-dialog-body">
              <div class="wc-popup-hero">
                <div>
                  <div class="wc-popup-label">Actie</div>
                  <div class="wc-popup-action"></div>
                </div>
                <div class="wc-popup-power"></div>
              </div>
              <div class="wc-popup-reason-wrap">
                <div class="wc-popup-label">Waarom?</div>
                <div class="wc-popup-reason"></div>
              </div>
              <div class="wc-popup-grid">
                <div class="wc-popup-item"><span>SoC</span><strong class="wc-popup-soc"></strong></div>
                <div class="wc-popup-item"><span>P1 netflow</span><strong class="wc-popup-p1"></strong></div>
                <div class="wc-popup-item"><span>Huisvraag</span><strong class="wc-popup-house"></strong></div>
                <div class="wc-popup-item"><span>PV nu</span><strong class="wc-popup-pv"></strong></div>
                <div class="wc-popup-item"><span>Accu laadt</span><strong class="wc-popup-charge"></strong></div>
                <div class="wc-popup-item"><span>Accu ontlaadt</span><strong class="wc-popup-discharge"></strong></div>
                <div class="wc-popup-item"><span>Prijs nu</span><strong class="wc-popup-price"></strong></div>
                <div class="wc-popup-item"><span>Reserve</span><strong class="wc-popup-reserve"></strong></div>
              </div>
              <div class="wc-popup-section">
                <div class="wc-popup-line"><span>Gestuurd</span><strong class="wc-popup-command"></strong></div>
                <div class="wc-popup-line"><span>Volgende actie</span><strong class="wc-popup-next"></strong></div>
                <div class="wc-popup-line"><span>Bijspringen</span><strong class="wc-popup-assist"></strong></div>
                <div class="wc-popup-line"><span>Sturing</span><strong class="wc-popup-control"></strong></div>
                <div class="wc-popup-line"><span>Adapter</span><strong class="wc-popup-adapter"></strong></div>
                <div class="wc-popup-line"><span>Bewaking</span><strong class="wc-popup-watch"></strong></div>
              </div>
              <div class="wc-popup-error" hidden></div>
              <div class="wc-popup-history-wrap">
                <div class="wc-popup-label">Laatste beslissingen</div>
                <div class="wc-popup-history"></div>
              </div>
            </div>
          </div>
        </div>
      </ha-card>
    `;
    const dialog = this._root.querySelector(".wc-dialog");
    const closeDialog = () => {
      dialog.hidden = true;
      this._popupOpener?.focus();
    };
    this._root.querySelectorAll(".wc-popup-trigger").forEach((trigger) => {
      trigger.addEventListener("click", () => {
        this._popupOpener = trigger;
        dialog.hidden = false;
        this._root.querySelector(".wc-dialog-close").focus();
      });
    });
    this._root.querySelector(".wc-dialog-close").addEventListener("click", closeDialog);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog();
    });
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDialog();
      }
    });
    this._built = true;
  }

  _update() {
    const hass = this._hass;
    const cfg = this._config;
    const stateObj = hass.states[cfg.entity];
    const root = this._root;

    root.querySelector(".wc-title").textContent = cfg.title;

    if (!stateObj) {
      root.querySelector(".wc-body").innerHTML =
        `<div class="wc-error">Entiteit <code>${cfg.entity}</code> niet gevonden</div>`;
      return;
    }

    const attrs = stateObj.attributes || {};
    const advies = stateObj.state;
    const setpoint = attrs.setpoint_w ?? 0;
    const plan = Array.isArray(attrs.plan) ? attrs.plan : [];
    const inputs = attrs.berekend_met || {};
    const soc = inputs.soc_kwh;
    const trained = attrs.getraind_tot;

    root.querySelector(".wc-trained").textContent = trained ? `getraind: ${trained}` : "";

    const adviesEl = root.querySelector(".wc-advies");
    adviesEl.textContent = WattsonCard._label(advies);
    adviesEl.className = `wc-advies wc-status-${WattsonCard._statusClass(advies)}`;

    root.querySelector(".wc-setpoint").textContent =
      `${setpoint > 0 ? "+" : ""}${Math.round(setpoint)} W`;
    root.querySelector(".wc-soc").textContent =
      soc !== undefined ? `${soc.toFixed(2)} kWh` : "—";

    // besparing-sensor staat los; als de gebruiker de entity_id volgt (advies -> besparing)
    // proberen we 'm te vinden, anders laten we het veld leeg.
    const besparingId = cfg.entity.replace("advies", "verwachte_besparing");
    const besparingObj = hass.states[besparingId];
    root.querySelector(".wc-besparing").textContent = besparingObj
      ? `€ ${parseFloat(besparingObj.state).toFixed(2)}`
      : "—";

    root.querySelector(".wc-note").textContent = attrs.fout
      ? `⚠ ${attrs.fout}`
      : (attrs.laatst_gestuurd ? `laatst gestuurd: ${attrs.laatst_gestuurd}` : "");

    root.querySelector(".wc-chart").innerHTML = WattsonCard._chart(
      plan.slice(0, cfg.hours)
    );
    this._updatePopup(advies, setpoint, attrs, inputs, soc);
  }

  _updatePopup(advies, setpoint, attrs, inputs, soc) {
    const root = this._root;
    const setText = (selector, value, fallback = "—") => {
      root.querySelector(selector).textContent = value ?? fallback;
    };
    const watts = (value, signed = false) => {
      if (value === undefined || value === null || Number.isNaN(Number(value))) return "—";
      const number = Math.round(Number(value));
      return `${signed && number > 0 ? "+" : ""}${number} W`;
    };
    const kwh = (value) => value === undefined || value === null
      ? "—" : `${Number(value).toFixed(2)} kWh`;

    const action = root.querySelector(".wc-popup-action");
    action.textContent = WattsonCard._label(advies);
    action.className = `wc-popup-action wc-status-${WattsonCard._statusClass(advies)}`;
    setText(".wc-popup-power", watts(setpoint, true));
    setText(".wc-popup-reason", attrs.reden);
    setText(".wc-popup-soc", soc === undefined ? null
      : `${kwh(soc)}${inputs.soc_pct === undefined ? "" : ` (${Number(inputs.soc_pct).toFixed(0)}%)`}`);
    const p1Value = inputs.p1_nu_w;
    setText(".wc-popup-p1", p1Value === undefined || p1Value === null
      ? null
      : `${Math.abs(Math.round(Number(p1Value)))} W ${Number(p1Value) > 0 ? "import" : Number(p1Value) < 0 ? "export" : "netto"}`);
    setText(".wc-popup-house", watts(inputs.huislast_nu_w));
    setText(".wc-popup-pv", watts(inputs.pv_nu_w));
    setText(".wc-popup-charge", watts(inputs.accu_laden_w));
    setText(".wc-popup-discharge", watts(inputs.accu_ontladen_w));
    setText(".wc-popup-price", inputs.prijs_nu === undefined
      ? null : `€ ${Number(inputs.prijs_nu).toFixed(3)}/kWh`);
    setText(".wc-popup-reserve", kwh(attrs.reserve_kwh));
    setText(".wc-popup-command", attrs.laatst_gestuurd);
    setText(".wc-popup-next", attrs.volgende_actie, "nog niets gepland");
    setText(".wc-popup-assist", attrs.bijspringen);
    setText(".wc-popup-control", attrs.sturing_actief ? "actief" : "schaduwmodus");
    setText(".wc-popup-adapter", attrs.adapter);
    setText(".wc-popup-watch", [attrs.watchdog_telemetrie, attrs.export_guard].filter(Boolean).join(" · "));

    const error = root.querySelector(".wc-popup-error");
    error.hidden = !attrs.fout;
    error.textContent = attrs.fout ? `⚠ ${attrs.fout}` : "";

    const historyEl = root.querySelector(".wc-popup-history");
    const history = Array.isArray(attrs.historie) ? attrs.historie.slice(0, 4) : [];
    const rows = history.map((item) => {
      const row = document.createElement("div");
      row.className = "wc-history-row";
      const top = document.createElement("div");
      top.className = "wc-history-top";
      const state = document.createElement("strong");
      state.textContent = WattsonCard._label(item.advies);
      const meta = document.createElement("span");
      meta.textContent = `${item.tijd || ""}${item.setpoint_w === undefined ? "" : ` · ${watts(item.setpoint_w, true)}`}`;
      top.append(state, meta);
      const reason = document.createElement("div");
      reason.className = "wc-history-reason";
      reason.textContent = item.reden || item.gestuurd || "—";
      row.append(top, reason);
      return row;
    });
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "wc-history-empty";
      empty.textContent = "Nog geen beslissingen vastgelegd";
      rows.push(empty);
    }
    historyEl.replaceChildren(...rows);
  }

  static _label(advies) {
    const labels = {
      laden: "Laden",
      ontladen: "Ontladen",
      verkopen: "Verkopen",
      rust: "Rust",
      "bijspringen: laden": "Bijspringen: laden",
      "bijspringen: ontladen": "Bijspringen: ontladen",
      "rust (EV-guard)": "Rust · EV-bewaking",
      "rust (EV-check)": "Rust · EV-controle",
      init: "Bezig met plannen…",
      "geen data": "Geen data",
    };
    return labels[advies] || advies || "—";
  }

  static _statusClass(advies) {
    if (advies === "laden" || advies === "bijspringen: laden") return "charge";
    if (advies === "ontladen" || advies === "bijspringen: ontladen") return "discharge";
    if (advies === "verkopen") return "sell";
    if (advies === "rust" || String(advies).startsWith("rust (")) return "idle";
    return "unknown";
  }

  static _chart(plan) {
    if (!plan.length) {
      return `<div class="wc-chart-empty">geen plan beschikbaar</div>`;
    }
    const w = 300;
    const h = 84;
    const barGap = 3;
    const barW = (w - barGap * (plan.length - 1)) / plan.length;
    const maxAbs = Math.max(...plan.map((p) => Math.abs(p.setpoint_w || 0)), 100);
    const zeroY = h / 2;

    const bars = plan
      .map((p, i) => {
        const sp = p.setpoint_w || 0;
        const barH = Math.max((Math.abs(sp) / maxAbs) * (h / 2 - 4), 1.5);
        const x = i * (barW + barGap);
        const y = sp >= 0 ? zeroY - barH : zeroY;
        const cls = sp > 5 ? "charge" : sp < -5 ? "discharge" : "idle";
        const title = `${p.tijd}  ${sp >= 0 ? "+" : ""}${Math.round(sp)} W  €${(p.prijs ?? 0).toFixed(3)}  SoC ${(p.soc_na_kwh ?? 0).toFixed(2)} kWh`;
        return `<rect class="wc-bar wc-bar-${cls}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" rx="1.5"><title>${title}</title></rect>`;
      })
      .join("");

    const labelStep = Math.max(1, Math.round(plan.length / 6));
    const labels = plan
      .map((p, i) => (i % labelStep === 0 ? { i, tijd: p.tijd } : null))
      .filter(Boolean)
      .map(
        ({ i, tijd }) =>
          `<text class="wc-tick" x="${(i * (barW + barGap) + barW / 2).toFixed(1)}" y="${h + 12}" text-anchor="middle">${tijd}</text>`
      )
      .join("");

    return `
      <svg viewBox="0 0 ${w} ${h + 16}" preserveAspectRatio="none" class="wc-svg">
        <line class="wc-zero" x1="0" y1="${zeroY}" x2="${w}" y2="${zeroY}"></line>
        ${bars}
        ${labels}
      </svg>
    `;
  }

  static _css() {
    return `
      ha-card {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .wc-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
      }
      .wc-brand {
        display: flex;
        align-items: center;
        gap: 9px;
        min-width: 0;
      }
      .wc-icon-trigger {
        appearance: none;
        border: 0;
        padding: 3px;
        margin: -3px;
        border-radius: 10px;
        background: transparent;
        color: inherit;
        cursor: pointer;
        line-height: 0;
        transition: background 120ms ease, transform 120ms ease;
      }
      .wc-icon-trigger:hover {
        background: color-mix(in srgb, var(--primary-color, #3987e5) 14%, transparent);
        transform: translateY(-1px);
      }
      .wc-icon-trigger:focus-visible,
      .wc-soc-trigger:focus-visible,
      .wc-dialog-close:focus-visible {
        outline: 2px solid var(--primary-color, #3987e5);
        outline-offset: 2px;
      }
      .wc-brand-icon {
        width: 30px;
        height: 30px;
        flex: 0 0 auto;
      }
      .wc-title {
        font-size: 1rem;
        font-weight: 600;
        color: var(--primary-text-color);
      }
      .wc-trained {
        font-size: 0.7rem;
        color: var(--secondary-text-color);
      }
      .wc-body {
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .wc-error {
        color: var(--error-color, #db4437);
        font-size: 0.9rem;
      }
      .wc-advies-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 8px;
      }
      .wc-advies {
        font-size: 1.7rem;
        font-weight: 700;
        line-height: 1.1;
      }
      .wc-status-charge { color: var(--energy-battery-out-color, #4C9BE8); }
      .wc-status-discharge { color: var(--energy-battery-in-color, #22C55E); }
      .wc-status-sell { color: #FFB86B; }
      .wc-status-idle { color: var(--secondary-text-color); }
      .wc-status-unknown { color: var(--warning-color, #F5C542); }
      .wc-stats {
        display: flex;
        gap: 16px;
      }
      .wc-stat {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
      }
      .wc-soc-trigger {
        appearance: none;
        border: 0;
        border-radius: 8px;
        margin: -4px;
        padding: 4px;
        background: transparent;
        font: inherit;
        cursor: pointer;
        transition: background 120ms ease;
      }
      .wc-soc-trigger:hover {
        background: color-mix(in srgb, var(--primary-color, #3987e5) 12%, transparent);
      }
      .wc-stat-hint {
        margin-top: 1px;
        font-size: 0.58rem;
        line-height: 1;
        color: var(--primary-color, #3987e5);
      }
      .wc-stat-label {
        font-size: 0.65rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--secondary-text-color);
      }
      .wc-stat-value {
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--primary-text-color);
      }
      .wc-chart {
        width: 100%;
      }
      .wc-svg {
        width: 100%;
        height: 100px;
        display: block;
        overflow: visible;
      }
      .wc-zero {
        stroke: var(--divider-color, #444);
        stroke-width: 1;
        vector-effect: non-scaling-stroke;
      }
      .wc-bar-charge { fill: var(--energy-battery-out-color, #4C9BE8); }
      .wc-bar-discharge { fill: var(--energy-battery-in-color, #22C55E); }
      .wc-bar-idle { fill: var(--disabled-text-color, #555); opacity: 0.6; }
      .wc-tick {
        font-size: 6.5px;
        fill: var(--secondary-text-color);
      }
      .wc-chart-empty {
        font-size: 0.8rem;
        color: var(--secondary-text-color);
        text-align: center;
        padding: 8px 0;
      }
      .wc-note {
        font-size: 0.7rem;
        color: var(--secondary-text-color);
        min-height: 1em;
      }
      .wc-dialog {
        box-sizing: border-box;
        position: fixed;
        inset: 0;
        z-index: 9999;
        display: grid;
        place-items: center;
        padding: 14px;
        color: var(--primary-text-color, #f4f6fb);
        background: rgba(5, 10, 22, 0.68);
      }
      .wc-dialog[hidden] { display: none; }
      .wc-dialog-shell {
        box-sizing: border-box;
        width: min(440px, calc(100vw - 28px));
        max-height: min(760px, calc(100vh - 28px));
        overflow: auto;
        border-radius: 20px;
        background: var(--card-background-color, #1c1c22);
        box-shadow: 0 24px 80px rgba(0, 0, 0, 0.48);
      }
      .wc-dialog-header {
        position: sticky;
        top: 0;
        z-index: 1;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 18px 18px 14px;
        background: color-mix(in srgb, var(--card-background-color, #1c1c22) 94%, transparent);
        border-bottom: 1px solid var(--divider-color, #333);
      }
      .wc-dialog-heading {
        display: flex;
        align-items: center;
        gap: 11px;
        min-width: 0;
      }
      .wc-dialog-icon {
        width: 36px;
        height: 36px;
        flex: 0 0 auto;
      }
      .wc-dialog-eyebrow,
      .wc-popup-label {
        font-size: 0.65rem;
        line-height: 1.2;
        letter-spacing: 0.07em;
        text-transform: uppercase;
        color: var(--secondary-text-color, #9aa);
      }
      .wc-dialog-title {
        margin-top: 2px;
        font-size: 1.08rem;
        font-weight: 700;
      }
      .wc-dialog-close {
        appearance: none;
        width: 34px;
        height: 34px;
        border: 0;
        border-radius: 50%;
        background: color-mix(in srgb, var(--secondary-text-color, #9aa) 15%, transparent);
        color: var(--primary-text-color, #fff);
        font: 400 1.45rem/1 system-ui, sans-serif;
        cursor: pointer;
      }
      .wc-dialog-body {
        display: flex;
        flex-direction: column;
        gap: 14px;
        padding: 16px 18px 20px;
      }
      .wc-popup-hero {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 16px;
        padding: 15px;
        border: 1px solid color-mix(in srgb, var(--primary-color, #3987e5) 28%, transparent);
        border-radius: 14px;
        background: color-mix(in srgb, var(--primary-color, #3987e5) 9%, transparent);
      }
      .wc-popup-action {
        margin-top: 3px;
        font-size: 1.55rem;
        font-weight: 750;
      }
      .wc-popup-power {
        font-size: 1.1rem;
        font-weight: 700;
        white-space: nowrap;
      }
      .wc-popup-reason-wrap {
        padding: 0 2px;
      }
      .wc-popup-reason {
        margin-top: 4px;
        font-size: 0.94rem;
        line-height: 1.4;
      }
      .wc-popup-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
      }
      .wc-popup-item {
        display: flex;
        flex-direction: column;
        gap: 3px;
        min-width: 0;
        padding: 10px 11px;
        border-radius: 11px;
        background: color-mix(in srgb, var(--secondary-text-color, #9aa) 9%, transparent);
      }
      .wc-popup-item span,
      .wc-popup-line span {
        font-size: 0.68rem;
        color: var(--secondary-text-color, #9aa);
      }
      .wc-popup-item strong {
        overflow: hidden;
        font-size: 0.9rem;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .wc-popup-section {
        border-top: 1px solid var(--divider-color, #333);
        border-bottom: 1px solid var(--divider-color, #333);
        padding: 7px 0;
      }
      .wc-popup-line {
        display: grid;
        grid-template-columns: 105px minmax(0, 1fr);
        align-items: baseline;
        gap: 12px;
        padding: 5px 2px;
      }
      .wc-popup-line strong {
        font-size: 0.78rem;
        font-weight: 600;
        text-align: right;
        overflow-wrap: anywhere;
      }
      .wc-popup-error {
        padding: 10px 12px;
        border-radius: 10px;
        background: color-mix(in srgb, var(--error-color, #db4437) 16%, transparent);
        color: var(--error-color, #ff6b62);
        font-size: 0.78rem;
      }
      .wc-popup-history-wrap {
        display: flex;
        flex-direction: column;
        gap: 7px;
      }
      .wc-popup-history {
        display: flex;
        flex-direction: column;
      }
      .wc-history-row {
        padding: 9px 2px;
        border-bottom: 1px solid var(--divider-color, #333);
      }
      .wc-history-row:last-child { border-bottom: 0; }
      .wc-history-top {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        font-size: 0.75rem;
      }
      .wc-history-top span {
        color: var(--secondary-text-color, #9aa);
        text-align: right;
      }
      .wc-history-reason,
      .wc-history-empty {
        margin-top: 3px;
        font-size: 0.72rem;
        line-height: 1.35;
        color: var(--secondary-text-color, #9aa);
      }
      @media (max-width: 440px) {
        .wc-dialog-body { padding-inline: 14px; }
        .wc-popup-grid { grid-template-columns: 1fr 1fr; }
        .wc-popup-line { grid-template-columns: 90px minmax(0, 1fr); }
      }
    `;
  }
}

customElements.define("wattson-card", WattsonCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "wattson-card",
  name: "Wattson",
  description: "Advies, setpoint, SoC en plan van de Wattson accu-planner.",
  preview: false,
});
