/* Dump uurlijkse HA long-term statistics naar data/raw_stats.json.
 * Bron voor de EMS-trainer; draait op deze PC, leest via WS van 192.168.1.10.
 */
const WebSocket = require('../../../node_modules/ws');
const fs = require('fs');
const path = require('path');

const TOKEN = process.env.HA_TOKEN;
if (!TOKEN) throw new Error('Set HA_TOKEN before running this script');
const IDS = [
  'sensor.zonneplan_current_electricity_tariff', // €/kWh incl. belasting (mean)
  'sensor.p1_meter_energy_import',               // kWh teller (change)
  'sensor.p1_meter_energy_export',               // kWh teller (change)
  'sensor.growatt_total_energy_production',      // kWh teller (change) — echte PV
  'sensor.keba_p20_charging_power',              // kW mean — Tesla thuis
  'sensor.jimmy_charger_power',                  // kW mean — Tesla overal (fallback pre-Keba)
  'sensor.power_production_now',                 // W mean — Forecast.Solar voorspelling
  'sensor.solarflow_2400_ac_electric_level',     // % SoC (sanity/vergelijk live)
];
const START = '2026-03-01T00:00:00Z';

const ws = new WebSocket('ws://192.168.1.10:8123/api/websocket');
ws.on('open', () => console.log('ws open'));
ws.on('error', (e) => { console.error('ws error', e.message); process.exit(1); });
ws.on('close', (c) => console.log('ws close', c));
let id = 0; const pending = {};
const send = (m) => new Promise((res, rej) => { m.id = ++id; pending[m.id] = { res, rej }; ws.send(JSON.stringify(m)); });
ws.on('message', (raw) => {
  const m = JSON.parse(raw);
  if (m.type === 'auth_required') ws.send(JSON.stringify({ type: 'auth', access_token: TOKEN }));
  else if (m.type === 'auth_ok') main().catch((e) => { console.error('ERR', e); process.exit(1); });
  else if (m.type === 'result') { const q = pending[m.id]; if (q) { m.success ? q.res(m.result) : q.rej(new Error(JSON.stringify(m.error))); delete pending[m.id]; } }
});

async function main() {
  const r = await send({
    type: 'recorder/statistics_during_period',
    start_time: START,
    end_time: new Date().toISOString(),
    statistic_ids: IDS,
    period: 'hour',
    types: ['mean', 'change'],
  });
  const outDir = path.join(__dirname, 'data');
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'raw_stats.json'), JSON.stringify({ fetched: new Date().toISOString(), start: START, stats: r }));
  for (const k of IDS) console.log(k.padEnd(48), r[k] ? r[k].length + ' uren' : 'GEEN');
  process.exit(0);
}
