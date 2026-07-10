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
            <svg class="wc-brand-icon" viewBox="0 0 256 256" aria-hidden="true">
              <defs><linearGradient id="wc-energy" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2F7FE2"/><stop offset="1" stop-color="#13B887"/></linearGradient></defs>
              <rect x="104" y="20" width="48" height="28" rx="9" fill="url(#wc-energy)"/>
              <rect x="48" y="40" width="160" height="192" rx="36" fill="url(#wc-energy)"/>
              <path d="M151 66 92 151h34l-20 57 62-91h-35z" fill="#fff"/>
            </svg>
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
              <div class="wc-stat">
                <span class="wc-stat-label">SoC</span>
                <span class="wc-stat-value wc-soc"></span>
              </div>
              <div class="wc-stat">
                <span class="wc-stat-label">verwachte besparing</span>
                <span class="wc-stat-value wc-besparing"></span>
              </div>
            </div>
          </div>
          <div class="wc-chart"></div>
          <div class="wc-note"></div>
        </div>
      </ha-card>
    `;
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
  }

  static _label(advies) {
    const labels = {
      laden: "Laden",
      ontladen: "Ontladen",
      verkopen: "Verkopen",
      rust: "Rust",
      init: "Bezig met plannen…",
      "geen data": "Geen data",
    };
    return labels[advies] || advies || "—";
  }

  static _statusClass(advies) {
    if (advies === "laden") return "charge";
    if (advies === "ontladen") return "discharge";
    if (advies === "verkopen") return "sell";
    if (advies === "rust") return "idle";
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
      .wc-status-charge { color: var(--energy-battery-out-color, #2FD3FF); }
      .wc-status-discharge { color: var(--energy-battery-in-color, #00E5A8); }
      .wc-status-sell { color: var(--energy-grid-return-color, #FFB86B); }
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
      .wc-bar-charge { fill: var(--energy-battery-out-color, #2FD3FF); }
      .wc-bar-discharge { fill: var(--energy-battery-in-color, #00E5A8); }
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
