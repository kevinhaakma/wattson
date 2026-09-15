/**
 * wattson-plan-card — het Wattson-uurplan als hus-energy-graph.
 *
 * Eén grafiek, het Tibber/EVCC-idioom: prijsbalken per uur, gekleurd naar
 * wat Wattson dat uur doet.
 *
 *   verhaal    één zin boven de grafiek: wat doet hij nu, en als hij wacht —
 *              waarom wachten loont (€) en wanneer de accu vol is
 *   balken     hoogte = prijs, kleur = actie (amber laden, groen ontladen,
 *              blauw verkopen, gedimd = rust), intensiteit = vermogen
 *   SoC        gestreepte witte lijn op accucapaciteit-schaal; "vol"-lijn
 *   nu         de nu-lijn schuift mee binnen het uur; verstreken deel gedimd
 *   dag-grens  streepje + weekdag zodra de horizon middernacht kruist
 *   tik        op een uur → detailregel (actie, prijs, zon, SoC-na, reden)
 *
 * type: custom:wattson-plan-card          # alle entities hebben defaults
 *   entity: sensor.wattson_advies
 *   weather: weather.singel_15_slim       # opt-in: weerstrip per uur
 *   hours: 16
 *   show_pv: true                         # opt-in: zon-curve over de balken
 *   show_load: true                       # opt-in: huislast-stippellijn
 *   max_charge_w: 2000                    # schaal voor kleurintensiteit
 *   max_discharge_w: 1400
 *
 * Stijl: overzicht "glass", geen emojis, build-once/patch.
 */
class WattsonPlanCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: "sensor.wattson_advies",
      weather: "",                       // bv. weather.singel_15_slim → weerstrip aan
      hours: 16,
      title: "Wattson plan",
      icon: "mdi:chart-timeline-variant",
      show_pv: false,                    // true → zon-curve over de balken
      show_load: false,                  // true → huislast-stippellijn in de grafiek
      max_charge_w: 2000,
      max_discharge_w: 1400,
      ...(config || {}),
    };
    this._built = false;
    this._forecast = null;
    this._sel = null;
  }

  getCardSize() { return 4; }
  static getStubConfig() { return {}; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    if (!this._fcUnsub && this._config.weather && hass && hass.connection) {
      this._subscribeForecast(hass);
    }
    this._update();
  }

  disconnectedCallback() {
    if (this._fcUnsub) { this._fcUnsub(); this._fcUnsub = null; }
  }

  /* ── weersvoorspelling ─────────────────────────────────── */

  _subscribeForecast(hass) {
    this._fcUnsub = () => {};   // eenmalig, ook als de promise faalt
    hass.connection.subscribeMessage(
      (msg) => { this._forecast = msg.forecast || []; this._sig = null; this._update(); },
      { type: "weather/subscribe_forecast", entity_id: this._config.weather,
        forecast_type: "hourly" },
    ).then((unsub) => { this._fcUnsub = unsub; })
      .catch(() => {
        // oudere HA of niet-ondersteunde integratie: val terug op het attribuut
        const w = this._st(this._config.weather);
        this._forecast = (w && w.attributes.forecast) || [];
        this._sig = null;
        this._update();
      });
  }

  /**
   * Absolute startdatum per planregel. Anker is het huidige uur, gecorrigeerd
   * met ±1 uur als het plan net vóór of ná de uurwissel is gemaakt.
   */
  _rowDates(rows) {
    const label = (k) => String(new Date(k * 3600000).getHours()).padStart(2, "0") + ":00";
    const nowH = Math.floor(Date.now() / 3600000);
    let start = nowH;
    if (rows.length && rows[0].tijd && label(start) !== rows[0].tijd) {
      const alt = [nowH - 1, nowH + 1].find((k) => label(k) === rows[0].tijd);
      if (alt !== undefined) start = alt;
    }
    return rows.map((_, i) => new Date((start + i) * 3600000));
  }

  /** Eén forecast-slot per planregel, gematcht op absoluut uur. */
  _wxForRows(rowDates) {
    const byHour = new Map();
    for (const f of this._forecast || []) {
      const d = new Date(f.datetime);
      if (isNaN(d)) continue;
      const k = Math.floor(d.getTime() / 3600000);
      if (!byHour.has(k)) byHour.set(k, f);
    }
    return rowDates.map((d) => byHour.get(Math.floor(d.getTime() / 3600000)) || null);
  }

  _st(id) { return this._hass && this._hass.states[id]; }
  static _nl(v, dec = 0) {
    return v === null || v === undefined || !Number.isFinite(v) ? "--"
      : v.toLocaleString("nl-NL", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  static _curve(pts) {
    if (pts.length < 2) return "";
    let d = "M" + pts[0][0] + "," + pts[0][1];
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      d += " C" + (p1[0] + (p2[0] - p0[0]) / 6).toFixed(1) + "," + (p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)
         + " " + (p2[0] - (p3[0] - p1[0]) / 6).toFixed(1) + "," + (p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)
         + " " + p2[0] + "," + p2[1];
    }
    return d;
  }

  static _ct(v) {
    return v === null || v === undefined || !Number.isFinite(v) ? "--"
      : WattsonPlanCard._nl(v * 100, 1);
  }

  /* ── weer-glyphs (16x16, geen mdi-afhankelijkheid) ─────── */

  static _sun(cx, cy, r, col) {
    let s = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${col}"/>`;
    for (let k = 0; k < 8; k++) {
      const a = (k * Math.PI) / 4;
      const c = Math.cos(a), n = Math.sin(a);
      s += `<line x1="${(cx + c * (r + 1.5)).toFixed(1)}" y1="${(cy + n * (r + 1.5)).toFixed(1)}"`
         + ` x2="${(cx + c * (r + 3)).toFixed(1)}" y2="${(cy + n * (r + 3)).toFixed(1)}"`
         + ` stroke="${col}" stroke-width="1.1" stroke-linecap="round"/>`;
    }
    return s;
  }

  static _cloud(col, dy = 0) {
    return `<g fill="${col}" transform="translate(0,${dy})">`
         + `<circle cx="5.9" cy="9.6" r="2.9"/><circle cx="10" cy="8.8" r="3.6"/>`
         + `<rect x="4" y="9.5" width="9.2" height="3.1" rx="1.55"/></g>`;
  }

  static _drops(n, col) {
    let s = "";
    for (let i = 0; i < n; i++) {
      const x = 5.4 + i * 2.6;
      s += `<line x1="${x}" y1="13.4" x2="${(x - 0.7).toFixed(1)}" y2="${n > 2 ? 15.6 : 15.2}"`
         + ` stroke="${col}" stroke-width="1.2" stroke-linecap="round"/>`;
    }
    return s;
  }

  /** Nacht-uur? Op basis van sun.sun, met de op-/ondergangstijd van vandaag
   *  geprojecteerd op de dag van `dt` (schuift hooguit een paar minuten). */
  _isNight(dt) {
    const s = this._st("sun.sun");
    if (!s || !dt || isNaN(dt)) return false;
    const onDay = (iso) => {
      const t = new Date(iso);
      if (isNaN(t)) return null;
      const d = new Date(dt);
      d.setHours(t.getHours(), t.getMinutes(), 0, 0);
      return d;
    };
    const rise = onDay(s.attributes.next_rising);
    const set = onDay(s.attributes.next_setting);
    if (!rise || !set) return false;
    return dt < rise || dt >= set;
  }

  static _wxIcon(cond, night = false) {
    if (night) {
      if (cond === "sunny") cond = "clear-night";
      else if (cond === "partlycloudy") cond = "partlycloudy-night";
    } else if (cond === "clear-night") cond = "sunny";
    const SUN = "#f3b23c", CL = "#98a1ac", DARK = "#7d848e",
      RAIN = "#6cb6e8", SNOW = "#d3e7f5", BOLT = "#f6d34a", MOON = "#cbd3e0";
    const C = WattsonPlanCard;
    let g;
    switch (cond) {
      case "sunny": g = C._sun(8, 8, 3.4, SUN); break;
      case "clear-night":
        g = `<path d="M12.2 10.6A5 5 0 0 1 6.4 4.3a5.2 5.2 0 1 0 5.8 6.3z" fill="${MOON}"/>`; break;
      case "partlycloudy":
        g = C._sun(10.6, 5.4, 2.5, SUN) + C._cloud(CL, 0.6); break;
      case "partlycloudy-night":
        g = `<path d="M12.9 6.6A3.4 3.4 0 0 1 9 2.7a3.6 3.6 0 1 0 3.9 3.9z" fill="${MOON}"/>`
          + C._cloud(CL, 0.6); break;
      case "cloudy": g = C._cloud(CL, 0.4); break;
      case "rainy": g = C._cloud(CL, -0.6) + C._drops(2, RAIN); break;
      case "pouring": g = C._cloud(DARK, -0.8) + C._drops(3, RAIN); break;
      case "hail": g = C._cloud(DARK, -0.8)
        + `<circle cx="6" cy="14.4" r="1" fill="${SNOW}"/><circle cx="9.4" cy="14.4" r="1" fill="${SNOW}"/>`; break;
      case "snowy": g = C._cloud(CL, -0.8)
        + `<circle cx="5.8" cy="14.3" r="0.95" fill="${SNOW}"/><circle cx="9" cy="14.3" r="0.95" fill="${SNOW}"/>`
        + `<circle cx="12.2" cy="14.3" r="0.95" fill="${SNOW}"/>`; break;
      case "snowy-rainy": g = C._cloud(CL, -0.8) + C._drops(1, RAIN)
        + `<circle cx="10" cy="14.3" r="0.95" fill="${SNOW}"/>`; break;
      case "lightning": case "lightning-rainy":
        g = C._cloud(DARK, -1)
          + `<path d="M8.9 11.6 L6.3 15.4 H8.1 L7.4 18 L10.2 13.9 H8.4 z" fill="${BOLT}"/>`; break;
      case "fog":
        g = C._cloud(CL, -1.6)
          + `<line x1="3.8" y1="13.2" x2="12.2" y2="13.2" stroke="${CL}" stroke-width="1.2" stroke-linecap="round"/>`
          + `<line x1="5" y1="15.4" x2="11" y2="15.4" stroke="${CL}" stroke-width="1.2" stroke-linecap="round"/>`; break;
      case "windy": case "windy-variant":
        g = `<g fill="none" stroke="${CL}" stroke-width="1.3" stroke-linecap="round">`
          + `<path d="M2.6 6.4h6.6a1.9 1.9 0 1 0-1.9-1.9"/>`
          + `<path d="M2.6 10h8.6a2 2 0 1 1-2 2"/></g>`; break;
      case "exceptional":
        g = `<path d="M8 3.2 14 14H2z" fill="none" stroke="#e0864a" stroke-width="1.3" stroke-linejoin="round"/>`
          + `<line x1="8" y1="7.4" x2="8" y2="10.6" stroke="#e0864a" stroke-width="1.3" stroke-linecap="round"/>`
          + `<circle cx="8" cy="12.3" r="0.8" fill="#e0864a"/>`; break;
      default: g = C._cloud(CL, 0.4);
    }
    return `<svg viewBox="0 0 16 16" aria-hidden="true">${g}</svg>`;
  }

  /* ── opbouw ────────────────────────────────────────────── */

  _build() {
    this._built = true;
    const root = this.attachShadow({ mode: "open" });
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
        .panel-head { display:flex; align-items:center; gap:8px; }
        .panel-head ha-icon { --mdc-icon-size:17px; color:#a7ada2; }
        .panel-title { font-size:12px; font-weight:600; letter-spacing:.12em;
                       text-transform:uppercase; color:rgba(255,255,255,.56); flex:1; }
        .head-vals { display:flex; gap:14px; align-items:baseline; flex-wrap:wrap;
                     justify-content:flex-end; }
        .val { display:flex; flex-direction:column; align-items:flex-end; gap:1px; }
        .val-num { font-size:15px; font-weight:500; color:rgba(255,255,255,.94); line-height:1;
                   font-variant-numeric:tabular-nums; white-space:nowrap; }
        .val-num small { font-size:10px; font-weight:600; color:rgba(255,255,255,.56);
                         margin-left:2px; }
        .val-name { font-size:9px; font-weight:600; text-transform:uppercase;
                    letter-spacing:.07em; line-height:1; }
        .story { margin-top:9px; font-size:11px; line-height:1.5;
                 color:rgba(255,255,255,.82); }
        .story b { font-weight:600; }
        .story .sub { color:rgba(255,255,255,.45); }
        /* weerstrip: kolommen lopen exact gelijk met de uurvakken in de svg */
        .wx { display:grid; grid-auto-flow:column; grid-auto-columns:1fr;
              margin-top:10px; padding:0 1.623%; }
        .wx-cell { display:flex; flex-direction:column; align-items:center; gap:1px;
                   min-width:0; }
        .wx-cell svg { width:13px; height:13px; display:block; }
        .wx-t { font-size:8.5px; line-height:1; color:rgba(255,255,255,.5);
                font-variant-numeric:tabular-nums; }
        .wx-t.ph { visibility:hidden; }
        .wx-r { width:100%; height:2px; border-radius:1px; background:transparent; }
        .gwrap { position:relative; margin-top:6px; cursor:pointer;
                 -webkit-tap-highlight-color:transparent; }
        .gbox svg { width:100%; aspect-ratio:308/100; display:block; }
        .hl { position:absolute; top:-2px; bottom:-2px; display:none;
              background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.18);
              border-radius:3px; pointer-events:none; }
        .detail { display:none; margin-top:7px; padding:7px 10px; border-radius:6px;
                  background:rgba(255,255,255,.05); font-size:10.5px; line-height:1.55;
                  color:rgba(255,255,255,.72); }
        .detail b { color:rgba(255,255,255,.95); font-weight:600; }
        .detail .why { color:rgba(255,255,255,.5); }
        .ticks { position:relative; height:11px; margin-top:2px; font-size:8.5px;
                 color:rgba(255,255,255,.48); }
        .ticks span { position:absolute; top:0; line-height:11px; }
        .range { display:flex; justify-content:space-between; font-size:8.5px;
                 color:rgba(255,255,255,.48); margin-top:3px; font-variant-numeric:tabular-nums; }
        .legend { display:flex; gap:11px; flex-wrap:wrap; margin-top:8px; font-size:9.5px;
                  color:rgba(255,255,255,.42); }
        .legend i { display:inline-block; width:8px; height:8px; border-radius:2px;
                    margin-right:4px; vertical-align:-1px; }
      </style>
      <ha-card>
        <div class="panel-head">
          <ha-icon id="icon"></ha-icon>
          <span class="panel-title" id="title"></span>
          <div class="head-vals" id="vals"></div>
        </div>
        <div class="story" id="story"></div>
        <div class="wx" id="wx"></div>
        <div class="gwrap" id="gwrap">
          <div class="gbox" id="gbox"></div>
          <div class="hl" id="hl"></div>
        </div>
        <div class="detail" id="detail"></div>
        <div class="ticks" id="ticks"></div>
        <div class="range" id="range"></div>
        <div class="legend" id="legend"></div>
      </ha-card>`;
    this._el = (id) => root.getElementById(id);
    this._el("icon").setAttribute("icon", this._config.icon);
    this._el("title").textContent = this._config.title;
    this._el("gwrap").addEventListener("click", (e) => this._onTap(e));
  }

  /* ── tik-detail ────────────────────────────────────────── */

  _onTap(e) {
    const g = this._geom;
    if (!g) return;
    const rect = this._el("gwrap").getBoundingClientRect();
    const vx = ((e.clientX - rect.left) / rect.width) * g.W;
    const i = Math.max(0, Math.min(g.n - 1, Math.floor((vx - g.P) / g.slot)));
    this._sel = this._sel === i ? null : i;
    this._renderSel();
  }

  _renderSel() {
    const C = WattsonPlanCard;
    const hl = this._el("hl"), detail = this._el("detail");
    const g = this._geom, rows = this._rows || [];
    if (this._sel === null || this._sel === undefined || !g || this._sel >= rows.length) {
      hl.style.display = "none"; detail.style.display = "none";
      return;
    }
    const i = this._sel, r = rows[i];
    hl.style.display = "block";
    hl.style.left = `${((g.P + i * g.slot) / g.W) * 100}%`;
    hl.style.width = `${(g.slot / g.W) * 100}%`;

    const act = this._actOf(r);
    const actTxt = act ? `${this._ACT[act].label} ${C._nl(Math.abs(r.setpoint_w))} W` : "rust";
    const socPct = this._cap > 0 ? ` (${Math.round((r.soc_na_kwh / this._cap) * 100)}%)` : "";
    const f = (this._wxArr || [])[i];
    const lines = [
      `<b>${r.tijd}</b> · <b style="color:${act ? this._ACT[act].col : "rgba(255,255,255,.6)"}">${actTxt}</b>`,
      `prijs ${C._ct(r.prijs)} ct · zon ${C._nl(r.verwachte_pv_w)} W · huis ${C._nl(r.verwachte_last_w)} W · SoC na ${C._nl(r.soc_na_kwh, 2)} kWh${socPct}`,
    ];
    if (f) {
      lines.push(`${f.condition}${Number.isFinite(f.temperature) ? ` · ${Math.round(f.temperature)}°C` : ""}`
        + `${Number(f.precipitation_probability) ? ` · ${Math.round(f.precipitation_probability)}% regen` : ""}`);
    }
    if (i === 0 && this._reden) lines.push(`<span class="why">${this._reden}</span>`);
    detail.innerHTML = lines.join("<br>");
    detail.style.display = "block";
  }

  /* ── update ────────────────────────────────────────────── */

  _update() {
    if (!this._hass) return;
    const C = WattsonPlanCard;
    const adv = this._st(this._config.entity);
    if (!adv) return;
    const a = adv.attributes || {};
    const b = a.berekend_met || {};
    const rows = (a.plan || []).slice(0, this._config.hours);

    // kop-waarden: prijs nu + SoC (%)
    const socPctHead = Number.isFinite(b.soc_pct) ? C._nl(b.soc_pct) : "--";
    const vals = [
      ["prijs nu", C._ct(b.prijs_nu), "ct", "#a7ada2"],
      ["SoC", socPctHead, "%", "#4cc88a"],
    ];
    const html = vals.map(([n, v, u, col]) =>
      `<div class="val"><div class="val-num">${v}<small>${u}</small></div>` +
      `<div class="val-name" style="color:${col}">${n}</div></div>`).join("");
    if (this._valsHtml !== html) { this._valsHtml = html; this._el("vals").innerHTML = html; }

    // accucapaciteit uit soc_kwh/soc_pct (bv. 0,92/16% → 5,75 kWh)
    this._cap = (Number.isFinite(b.soc_kwh) && Number.isFinite(b.soc_pct) && b.soc_pct > 1)
      ? b.soc_kwh / (b.soc_pct / 100) : 0;
    this._reden = a.reden || null;

    // actie per uur; "verkopen" zoals de planner het ziet: ontladen dat boven de
    // netto huisvraag uitkomt gaat het net op (decision_from_plan-heuristiek)
    const actOf = (r) => {
      const v = r.setpoint_w || 0;
      if (v > 50) return "chg";
      if (v < -50) {
        const netHome = Math.max((r.verwachte_last_w || 0) - (r.verwachte_pv_w || 0), 0);
        return -v > netHome + 100 ? "sell" : "dis";
      }
      return null;
    };
    const ACT = {
      chg:  { col: "#ffb86b", label: "laden" },
      dis:  { col: "#4cc88a", label: "ontladen" },
      sell: { col: "#5aa9e6", label: "verkopen" },
    };
    this._actOf = actOf;
    this._ACT = ACT;

    // verhaalregel: de conclusie van het plan in één zin — wat doet hij nú,
    // en als hij wacht: waaróm wachten loont en wanneer de accu vol is
    let story = "";
    if (rows.length >= 2) {
      const sp0 = rows[0].setpoint_w || 0;
      const chg = rows.filter((r) => (r.setpoint_w || 0) > 50);
      const totK = chg.reduce((t, r) => t + r.setpoint_w / 1000, 0);
      const avgP = totK > 0
        ? chg.reduce((t, r) => t + (r.setpoint_w / 1000) * r.prijs, 0) / totK : 0;
      let volLbl = null;
      if (this._cap > 0) {
        const vi = rows.findIndex((r) => r.soc_na_kwh >= this._cap - 0.06);
        if (vi >= 0 && rows[vi + 1]) volLbl = rows[vi + 1].tijd;
      }
      const act0 = actOf(rows[0]);
      if (act0 === "chg") {
        story = `<b style="color:${ACT.chg.col}">laadt +${C._nl(sp0)} W</b> · ${C._ct(rows[0].prijs)} ct`
          + (volLbl ? `<span class="sub"> · vol ±${volLbl}</span>` : "");
      } else if (act0 === "sell") {
        story = `<b style="color:${ACT.sell.col}">verkoopt ${C._nl(-sp0)} W</b>`
          + ` · export ${C._ct(b.prijs_nu)} ct &gt; bewaren ${C._ct(a.marginale_waarde_eur_kwh)} ct`;
      } else if (act0 === "dis") {
        story = `<b style="color:${ACT.dis.col}">ontlaadt ${C._nl(-sp0)} W</b>`
          + ` voor het huis · ${C._ct(b.prijs_nu)} ct-uur`;
      } else {
        const up = rows.slice(1).find((r) => Math.abs(r.setpoint_w || 0) > 50);
        if (up && up.setpoint_w > 0) {
          const win = Number.isFinite(b.prijs_nu) && totK > 0 && b.prijs_nu > avgP + 0.005
            ? ` · zo ≈ €${C._nl((b.prijs_nu - avgP) * totK, 2)} goedkoper dan nu laden` : "";
          story = `<b>wacht</b> — laden om ${up.tijd} voor ${C._ct(up.prijs)} ct (nu ${C._ct(b.prijs_nu)})`
            + `<span class="sub">${win}${volLbl ? ` · vol ±${volLbl}` : ""}</span>`;
        } else if (up) {
          story = `<b>wacht</b> — ontladen om ${up.tijd} (${C._ct(up.prijs)} ct)`
            + `<span class="sub"> · accu bewaart ${C._nl(b.soc_kwh, 1)} kWh voor de piek</span>`;
        } else {
          story = a.reden || "";
        }
      }
    }
    if (this._storyHtml !== story) {
      this._storyHtml = story;
      const el = this._el("story");
      el.innerHTML = story;
      el.style.display = story ? "" : "none";
    }

    // grafiek + weerstrip; sig bevat ook de minuut (nu-lijn/dim schuift mee)
    const rowDates = this._rowDates(rows);
    const wx = this._wxForRows(rowDates);
    const sig = JSON.stringify(rows) + "|" + wx.map((f) =>
      f ? `${f.condition},${f.temperature},${f.precipitation_probability}` : "").join(";")
      + `|${b.laadplafond_eur_kwh},${b.ontlaadvloer_eur_kwh},${b.soc_kwh},${Math.floor(Date.now() / 60000)}`;
    if (sig === this._sig) return;
    this._sig = sig;
    this._rows = rows;
    this._wxArr = wx;
    const gbox = this._el("gbox"), ticks = this._el("ticks"),
      range = this._el("range"), wxEl = this._el("wx");
    if (rows.length < 2) {
      gbox.innerHTML = '<div style="color:rgba(255,255,255,.34);font-size:11px;padding:18px 0;text-align:center">Geen plan beschikbaar</div>';
      wxEl.innerHTML = ""; ticks.innerHTML = ""; range.innerHTML = "";
      this._el("legend").innerHTML = "";
      this._geom = null; this._sel = null; this._renderSel();
      return;
    }
    const W = 308, H = 100, P = 5;
    const n = rows.length;
    const slot = (W - 2 * P) / n;
    const X = (i) => P + i * slot;
    this._geom = { W, H, P, n, slot };

    // weerstrip: glyph, temperatuur (elk 2e uur bij een lange horizon) en regenkans
    const tempStep = n > 12 ? 2 : 1;
    let anyWx = false;
    wxEl.innerHTML = rows.map((r, i) => {
      const f = wx[i];
      if (!f) return `<div class="wx-cell"></div>`;
      anyWx = true;
      const t = Number.isFinite(f.temperature) ? `${Math.round(f.temperature)}°` : "";
      const show = i % tempStep === 0;
      const prob = Number(f.precipitation_probability) || 0;
      const rain = prob >= 15
        ? `<div class="wx-r" style="background:linear-gradient(90deg,#6cb6e8 ${Math.min(prob, 100)}%,transparent 0)"
                title="${Math.round(prob)}% kans op neerslag"></div>`
        : `<div class="wx-r"></div>`;
      return `<div class="wx-cell" title="${r.tijd} · ${f.condition} · ${t} · ${Math.round(prob)}% regen">`
        + C._wxIcon(f.condition, this._isNight(rowDates[i]))
        + `<span class="wx-t${show ? "" : " ph"}">${t}</span>${rain}</div>`;
    }).join("");
    wxEl.style.display = anyWx ? "" : "none";

    // prijs-as: balken vanaf 0 (of het negatieve minimum), à la Tibber/Zonneplan
    let pMin = Infinity, pMax = -Infinity;
    for (const r of rows) { if (r.prijs < pMin) pMin = r.prijs; if (r.prijs > pMax) pMax = r.prijs; }
    const pLo = Math.min(0, pMin);
    const pHi = pMax + (pMax - pLo) * 0.08 || 0.01;
    const Yp = (v) => P + (1 - (v - pLo) / (pHi - pLo)) * (H - 2 * P);

    // SoC-as (rechts): 0 .. accucapaciteit, zodat "bovenin" echt "vol" is
    let sMax = 0.01;
    for (const r of rows) if (r.soc_na_kwh > sMax) sMax = r.soc_na_kwh;
    sMax = Math.max(sMax, this._cap) * 1.04;
    const Ys = (v) => P + (1 - v / sMax) * (H - 2 * P);

    // vermogens-as voor zon (en optioneel huislast)
    const showLoad = this._config.show_load === true;
    let pvMax = 0, loadMax = 0;
    for (const r of rows) {
      if ((r.verwachte_pv_w || 0) > pvMax) pvMax = r.verwachte_pv_w;
      if ((r.verwachte_last_w || 0) > loadMax) loadMax = r.verwachte_last_w;
    }
    const wMax = Math.max(this._config.show_pv === true ? pvMax : 0,
      showLoad ? loadMax : 0) * 1.15 || 1;
    const Yw = (v) => P + (1 - v / wMax) * (H - 2 * P);

    // positie van "nu" binnen het eerste uur (alleen als rij 0 het lopende uur is)
    const now = new Date();
    const liveFirst = rowDates[0] && Math.floor(rowDates[0].getTime() / 3600000)
      === Math.floor(now.getTime() / 3600000);
    const nowFrac = liveFirst ? now.getMinutes() / 60 : 0;
    const nowX = X(0) + slot * nowFrac;

    // vaste vermogensgrenzen: kleurintensiteit van een actiebalk schaalt mee
    const chgLim = Math.max(Number(this._config.max_charge_w) || 0,
      ...rows.map((r) => r.setpoint_w || 0), 100);
    const disLim = Math.max(Number(this._config.max_discharge_w) || 0,
      ...rows.map((r) => -(r.setpoint_w || 0)), 100);

    let defs = "", body = "";
    // dag-grens: streepje + weekdag zodra de horizon middernacht kruist
    const WD = ["zo", "ma", "di", "wo", "do", "vr", "za"];
    rowDates.forEach((d, i) => {
      if (i === 0 || d.getHours() !== 0) return;
      body += `<line x1="${X(i).toFixed(1)}" y1="${P - 2}" x2="${X(i).toFixed(1)}" y2="${H - P + 2}" stroke="rgba(255,255,255,.22)" stroke-width="1"/>` +
              `<text x="${(X(i) + 2).toFixed(1)}" y="${P + 6}" font-size="6.5" fill="rgba(255,255,255,.45)">${WD[d.getDay()]}</text>`;
    });
    // prijsbalken, gekleurd naar de actie van dat uur (Tibber/EVCC-idioom):
    // hoogte = prijs, kleur = wat Wattson doet, intensiteit = vermogen
    rows.forEach((r, i) => {
      const act = actOf(r);
      const v = r.setpoint_w || 0;
      const bx = X(i) + 0.8, bw = Math.max(slot - 1.6, 0.8);
      const by = Yp(Math.max(r.prijs, pLo));
      const bh = Math.max(H - P - by, 1);
      if (!act) {
        body += `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${bw.toFixed(1)}"` +
                ` height="${bh.toFixed(1)}" rx="1.5" fill="rgba(255,255,255,.13)"/>`;
      } else {
        const k = Math.min(act === "chg" ? v / chgLim : -v / disLim, 1);
        const op = (0.4 + 0.5 * k).toFixed(2);
        body += `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${bw.toFixed(1)}"` +
                ` height="${bh.toFixed(1)}" rx="1.5" fill="${ACT[act].col}" opacity="${op}"/>`;
      }
    });
    // zon (opt-in via show_pv): zachte gloed achter de balken
    const wpts = (key) => {
      const pts = rows.map((r, i) => [+(X(i) + slot / 2).toFixed(1), +Yw(r[key] || 0).toFixed(1)]);
      pts.unshift([+X(0).toFixed(1), pts[0][1]]);
      pts.push([+(X(n - 1) + slot).toFixed(1), pts[pts.length - 1][1]]);
      return pts;
    };
    if (this._config.show_pv === true && pvMax > 10) {
      body += `<path d="${C._curve(wpts("verwachte_pv_w"))}" fill="none" stroke="#f59e0b"` +
              ` stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round" opacity=".45"/>`;
    }
    if (showLoad && loadMax > 10) {
      body += `<path d="${C._curve(wpts("verwachte_last_w"))}" fill="none" stroke="#8fa6bd"` +
              ` stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"` +
              ` stroke-dasharray="1.5 2.5" opacity=".8"/>`;
    }
    // "vol"-lijn op de SoC-as
    if (this._cap > 0) {
      const vy = Ys(this._cap).toFixed(1);
      body += `<line x1="${P}" y1="${vy}" x2="${W - P}" y2="${vy}" stroke="rgba(255,255,255,.16)" stroke-width="1" stroke-dasharray="1.5 3"/>` +
              `<text x="${W - P - 1}" y="${(+vy - 2).toFixed(1)}" text-anchor="end" font-size="6" fill="rgba(255,255,255,.4)">vol</text>`;
    }
    // SoC-traject: gestreepte witte lijn over de balken, per uureinde
    const sp = rows.map((r, i) => `${(X(i) + slot).toFixed(1)},${Ys(r.soc_na_kwh).toFixed(1)}`);
    const s0 = Number.isFinite(b.soc_kwh) ? b.soc_kwh : rows[0].soc_na_kwh;
    body += `<polyline points="${X(0).toFixed(1)},${Ys(s0).toFixed(1)} ${sp.join(" ")}" fill="none" stroke="rgba(255,255,255,.85)" stroke-width="1.4" stroke-linejoin="round" stroke-dasharray="5 3"/>`;
    // verstreken deel van het lopende uur dimmen + nu-lijn op de echte tijd
    if (nowFrac > 0.02) {
      body += `<rect x="${X(0).toFixed(1)}" y="${P}" width="${(slot * nowFrac).toFixed(1)}" height="${H - 2 * P}" fill="rgba(0,0,0,.38)"/>`;
    }
    body += `<line x1="${nowX.toFixed(1)}" y1="${P - 2}" x2="${nowX.toFixed(1)}" y2="${H - P + 2}" stroke="rgba(255,255,255,.34)" stroke-width="1" stroke-dasharray="2 3"/>`;
    // onzichtbare hitzone per uur (desktop-hover; tik-detail dekt touch)
    rows.forEach((r, i) => {
      const f = wx[i];
      const act = actOf(r);
      const actTxt = act ? `${ACT[act].label} ${Math.abs(r.setpoint_w)} W` : "rust";
      const tip = [
        `${r.tijd} · ${actTxt}`,
        `prijs ${C._ct(r.prijs)} ct`,
        `zon ${Math.round(r.verwachte_pv_w || 0)} W · huis ${Math.round(r.verwachte_last_w || 0)} W`,
        `SoC na ${C._nl(r.soc_na_kwh, 2)} kWh`,
        f ? `${f.condition}${Number.isFinite(f.temperature) ? ` ${Math.round(f.temperature)}°C` : ""}`
          + `${Number(f.precipitation_probability) ? ` · ${Math.round(f.precipitation_probability)}% regen` : ""}` : null,
      ].filter(Boolean).join("\n");
      body += `<rect x="${X(i).toFixed(1)}" y="${P}" width="${slot.toFixed(1)}" height="${H - 2 * P}" fill="transparent">` +
              `<title>${tip}</title></rect>`;
    });

    gbox.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Wattson uurplan, komende ${n} uur"><defs>${defs}</defs>${body}</svg>`;

    // tijd-ticks: nu links, dan kwartpunten
    let th = "";
    for (const f of [0, 0.25, 0.5, 0.75, 1]) {
      const idx = Math.min(n - 1, Math.round(f * n));
      const lbl = f === 0 ? "nu" : rows[Math.min(idx, n - 1)].tijd;
      const tr = f === 0 ? 0 : f === 1 ? 100 : 50;
      th += `<span style="left:${f * 100}%;transform:translateX(-${tr}%)">${lbl}</span>`;
    }
    ticks.innerHTML = th;
    range.innerHTML = `<span>min ${C._ct(pMin)} ct</span>`
      + `<span>balkhoogte = prijs · kleur = actie</span>`
      + `<span>max ${C._ct(pMax)} ct</span>`;

    const hasSell = rows.some((r) => actOf(r) === "sell");
    const items = [
      [`background:#ffb86b`, "laden"],
      [`background:#4cc88a`, "ontladen"],
      hasSell ? [`background:#5aa9e6`, "verkopen"] : null,
      [`background:rgba(255,255,255,.16)`, "rust"],
      this._config.show_pv === true && pvMax > 10 ? [`background:#f59e0b;opacity:.6`, "zon"] : null,
      showLoad && loadMax > 10 ? [`background:transparent;border-bottom:2px dotted #8fa6bd;height:4px;border-radius:0`, "huis"] : null,
      [`background:transparent;border-bottom:2px dashed rgba(255,255,255,.8);height:4px;border-radius:0`, "SoC"],
    ].filter(Boolean);
    this._el("legend").innerHTML = items
      .map(([st, label]) => `<span><i style="${st}"></i>${label}</span>`).join("");

    this._renderSel();
  }
}

customElements.define("wattson-plan-card", WattsonPlanCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "wattson-plan-card",
  name: "Wattson plan",
  description: "Het Wattson-uurplan: acties en SoC-traject met prijs- en zoncontext, tik voor uurdetail.",
});
