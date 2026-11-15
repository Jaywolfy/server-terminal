import { api, clear, confirmDialog, fmt, h, modal, repeat, startClock } from './common.js';
import { COLORS, drawChart } from './charts.js';

startClock();

// --- Kennzahlen

const kpis = document.getElementById('kpis');
let overview = null, updateCount = null;

function kpi(label, value, note, percent = null) {
  return h('div', { class: 'kpi' }, h('div', { class: 'label' }, label), h('div', { class: 'value' }, value),
    percent !== null ? h('div', { class: 'bar' }, h('i', { style: `width:${Math.min(percent, 100)}%` })) : null,
    h('div', { class: 'note' }, note));
}

function renderKpis() {
  if (!overview) return;
  const now = overview.jetzt, disk = overview.festplatten[0];
  const clock = new Date((overview.zeit + (Date.now() - overview.geladen) / 1000) * 1000);
  clear(kpis,
    kpi('Uhrzeit', fmt.time(clock), fmt.date(clock)),
    kpi('Laufzeit', fmt.duration(overview.laufzeit_s + (Date.now() - overview.geladen) / 1000), `seit ${fmt.dateTime(overview.start_zeit * 1000)}`),
    kpi('CPU', now ? fmt.percent(now.cpu) : '–', `Last ${overview.last.join(' · ')} (${overview.cpu_kerne} Kerne)`, now ? now.cpu : null),
    kpi('Speicher', now ? fmt.percent(now.mem) : '–', now ? `Swap ${fmt.percent(now.swap)}` : '', now ? now.mem : null),
    kpi('Festplatte', disk ? fmt.percent(disk.belegt_prozent) : '–', disk ? `${fmt.bytes(disk.gesamt - disk.frei)} von ${fmt.bytes(disk.gesamt)}` : '', disk ? disk.belegt_prozent : null),
    kpi('Updates', updateCount === null ? '…' : String(updateCount), overview.neustart_noetig ? 'Neustart nötig' : updateCount === 0 ? 'System aktuell' : 'verfügbar'));
}

repeat(async () => { overview = await api('/api/overview'); overview.geladen = Date.now(); renderKpis(); }, 5000);
setInterval(renderKpis, 1000);

// --- Auslastung

const RANGES = { '1min': '1 Min', '5min': '5 Min', '1h': '1 Std', '12h': '12 Std', '1d': '1 Tag', '7d': '7 Tage' };
const REFRESH = { '1min': 2000, '5min': 2000, '1h': 15000, '12h': 60000, '1d': 60000, '7d': 120000 };
const pct = (v, axis) => (axis ? `${Math.round(v)}%` : `${v.toFixed(1)} %`);
const CHARTS = [
  { title: 'CPU', max: 100, format: pct, series: [{ key: 'cpu', label: 'CPU', color: COLORS.accent, fill: true }, { key: 'iowait', label: 'I/O-Wartezeit', color: COLORS.blue }] },
  { title: 'Arbeitsspeicher', max: 100, format: pct, series: [{ key: 'mem', label: 'Belegt', color: COLORS.teal, fill: true }, { key: 'swap', label: 'Swap', color: COLORS.muted }] },
  { title: 'Last (1 Min)', max: null, format: (v) => v.toFixed(2), series: [{ key: 'load1', label: 'Last', color: COLORS.blue, fill: true }] },
  { title: 'Netzwerk', max: null, format: (v, axis) => (axis ? fmt.bytes(v) : fmt.rate(v)), series: [{ key: 'rx', label: 'Empfangen', color: COLORS.green, fill: true }, { key: 'tx', label: 'Gesendet', color: COLORS.accent }] },
  { title: 'Festplatten-I/O', max: null, format: (v, axis) => (axis ? fmt.bytes(v) : fmt.rate(v)), series: [{ key: 'read', label: 'Lesen', color: COLORS.teal, fill: true }, { key: 'write', label: 'Schreiben', color: COLORS.accent }] },
  { title: 'Festplatte belegt', max: 100, format: pct, series: [{ key: 'disk', label: 'Belegt', color: COLORS.blue, fill: true }] },
];
let range = '5min', timer = null, lastSeries = null;
const chartsRoot = document.getElementById('charts');
const cells = CHARTS.map((chart) => {
  const canvas = h('canvas'), tip = h('div', { class: 'tip' }), value = h('b');
  const legend = h('div', { class: 'legend' }, chart.series.map((s) => h('span', {}, h('i', { style: `background:${s.color}` }), s.label)));
  chartsRoot.append(h('div', { class: 'chart' }, h('h3', {}, chart.title, value), canvas, tip, legend));
  return { chart, canvas, tip, value };
});

function timeLabel(ms, full = false) {
  if (full) return fmt.full(ms);
  return range === '1min' || range === '5min' ? fmt.time(ms) : range === '7d' ? fmt.dayTime(ms) : fmt.short(ms);
}

function drawAll() {
  if (!lastSeries) return;
  for (const { chart, canvas, tip, value } of cells) {
    const series = chart.series.map((s) => ({ ...s, points: lastSeries.punkte.map((p) => [p.t, p[s.key]]).filter((p) => p[1] !== null && p[1] !== undefined) }));
    const latest = series[0].points[series[0].points.length - 1];
    value.textContent = latest ? chart.format(latest[1]) : '';
    drawChart(canvas, tip, { series, from: lastSeries.von, to: lastSeries.bis, format: chart.format, max: chart.max, timeLabel });
  }
}

async function loadSeries() {
  lastSeries = await api(`/api/metrics?ansicht=${range}`);
  drawAll();
}

function startPolling() {
  if (timer) timer();
  timer = repeat(loadSeries, REFRESH[range]);
}

const rangeBar = document.getElementById('ranges');
function renderRanges() {
  clear(rangeBar, Object.entries(RANGES).map(([key, label]) => h('button', {
    class: key === range ? 'active' : '', role: 'tab', 'aria-selected': key === range ? 'true' : 'false',
    onclick: () => { range = key; renderRanges(); startPolling(); },
  }, label)));
}
renderRanges();
startPolling();
new ResizeObserver(() => drawAll()).observe(chartsRoot);

// --- Updates

const updatesBody = document.getElementById('updates-body');
const updatesNote = document.getElementById('updates-note');
let updateData = null, jobRunning = false;

function renderUpdates() {
  if (!updateData) return;
  const { verfuegbar, verlauf, neustart_noetig } = updateData;
  const security = verfuegbar.filter((p) => p.sicherheit).length;
  updateCount = verfuegbar.length;
  clear(updatesNote, verfuegbar.length ? h('span', { class: `pill ${security ? 'fail' : 'warn'}` }, `${verfuegbar.length} verfügbar${security ? `, ${security} Sicherheit` : ''}`) : h('span', { class: 'pill ok' }, 'aktuell'));
  const last = verlauf[0];
  clear(updatesBody,
    verfuegbar.length
      ? h('ul', { class: 'rows' }, verfuegbar.slice(0, 5).map((p) => h('li', {}, h('div', { class: 'main' }, h('span', { class: 'title' }, p.name), h('span', { class: 'sub' }, `${p.alt} → ${p.neu}`)), p.sicherheit ? h('span', { class: 'pill fail' }, 'Sicherheit') : null)),
        verfuegbar.length > 5 ? h('li', {}, h('span', { class: 'muted' }, `+ ${verfuegbar.length - 5} weitere`)) : null)
      : h('p', { class: 'empty' }, 'Alle Pakete sind auf dem neuesten Stand.'),
    h('p', { class: 'muted', style: 'margin:10px 0 4px;font-size:0.82rem' }, last ? `Zuletzt aktualisiert: ${last.start} (${last.anzahl} Pakete)` : 'Noch keine Updates protokolliert.'),
    neustart_noetig ? h('p', {}, h('span', { class: 'pill warn' }, 'Neustart nötig'), ' ', h('button', { class: 'small danger', onclick: rebootDialog }, 'Neu starten…')) : null,
    h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin-top:8px' },
      h('button', { class: 'ghost small', onclick: checkUpdates }, 'Auf Updates prüfen'),
      h('button', { class: 'small', onclick: runUpdates, disabled: jobRunning }, jobRunning ? 'Läuft …' : 'Jetzt Updates machen')));
  renderKpis();
}

async function loadUpdates() {
  try { updateData = await api('/api/updates'); renderUpdates(); } catch (e) { clear(updatesBody, h('p', { class: 'error' }, e.message)); }
}
repeat(loadUpdates, 5 * 60 * 1000);

async function checkUpdates(event) {
  const button = event.target; button.disabled = true; button.textContent = 'Prüfe …';
  try { await api('/api/updates/check', { method: 'POST' }); await loadUpdates(); } catch (e) { alert(e.message); } finally { button.disabled = false; button.textContent = 'Auf Updates prüfen'; }
}

async function runUpdates() {
  const count = updateData ? updateData.verfuegbar.length : 0;
  const ok = await confirmDialog('Updates installieren?',
    `${count} Paket(e) werden installiert (apt-get dist-upgrade). Wird Docker aktualisiert, starten alle Container kurz neu und Webseiten sind für etwa eine Minute nicht erreichbar.`,
    { danger: true, label: 'Jetzt installieren' });
  if (!ok) return;
  try { await api('/api/updates/run', { method: 'POST' }); } catch (e) { alert(e.message); return; }
  jobRunning = true; renderUpdates(); showJob();
}

function showJob() {
  const output = h('pre', { class: 'console' }, 'Starte …');
  const status = h('span', { class: 'pill warn' }, 'läuft');
  let next = 0, closed = false;
  const close = modal('Updates', [h('p', {}, status), output], [h('button', { class: 'ghost', onclick: () => { closed = true; close(); loadUpdates(); } }, 'Schließen')], { wide: true });
  const poll = async () => {
    if (closed) return;
    try {
      const data = await api(`/api/job?seit=${next}`);
      if (next === 0) output.textContent = '';
      if (data.zeilen.length) { output.append(document.createTextNode(data.zeilen.join('\n') + '\n')); output.scrollTop = output.scrollHeight; }
      next = data.naechste;
      if (!data.laeuft) {
        jobRunning = false;
        clear(status, data.erfolg ? 'fertig' : 'fehlgeschlagen'); status.className = `pill ${data.erfolg ? 'ok' : 'fail'}`;
        loadUpdates(); return;
      }
    } catch { /* Verbindung kurz weg (z. B. Docker-Neustart): weiter versuchen */ }
    setTimeout(poll, 1200);
  };
  poll();
}

function rebootDialog() {
  const input = h('input', { placeholder: 'NEUSTART', autocomplete: 'off' });
  const error = h('p', { class: 'error' });
  const close = modal('Server neu starten?', [h('p', {}, 'Alle Dienste und Webseiten sind für etwa eine Minute nicht erreichbar. Zur Bestätigung NEUSTART eingeben.'), input, error], [
    h('button', { class: 'ghost', onclick: () => close() }, 'Abbrechen'),
    h('button', { class: 'danger', onclick: async () => {
      try { await api('/api/reboot', { method: 'POST', body: { bestaetigung: input.value } }); close(); modal('Neustart', [h('p', {}, 'Der Server startet in wenigen Sekunden neu. Diese Seite lädt danach nicht automatisch.')], []); }
      catch (e) { error.textContent = e.message; }
    } }, 'Neu starten'),
  ]);
  input.focus();
}

// Läuft beim Öffnen der Seite schon ein Update?
repeat(async () => { const data = await api('/api/job'); if (data.laeuft && !jobRunning) { jobRunning = true; renderUpdates(); showJob(); } }, 30000);

// --- Anmeldeversuche

const LOGIN_RANGES = { 1: '1 Std', 24: '24 Std', 168: '7 Tage' };
let loginHours = 24, stopLogins = null;
const loginsBody = document.getElementById('logins-body');
const loginRanges = document.getElementById('login-ranges');

function renderLoginRanges() {
  clear(loginRanges, Object.entries(LOGIN_RANGES).map(([hours, label]) => h('button', {
    class: Number(hours) === loginHours ? 'active' : '', onclick: () => { loginHours = Number(hours); renderLoginRanges(); startLogins(); },
  }, label)));
}

function renderLogins(data) {
  const max = Math.max(...data.laender.map((c) => c.anzahl), 1);
  clear(loginsBody,
    h('div', { class: 'stats3' }, h('div', {}, h('b', {}, data.fehlgeschlagen.toLocaleString('de-DE')), h('span', {}, 'fehlgeschlagen')),
      h('div', {}, h('b', {}, data.ips.toLocaleString('de-DE')), h('span', {}, 'verschiedene Adressen')),
      h('div', {}, h('b', {}, data.erfolgreich), h('span', {}, 'erfolgreich'))),
    data.herkunft_fehler ? h('p', { class: 'error' }, data.herkunft_fehler) : null,
    data.laender.length ? h('div', { class: 'bars' }, data.laender.slice(0, 5).map((c) => h('div', { class: 'bar-row' }, h('span', {}, c.land), h('span', { class: 'track' }, h('i', { style: `width:${(100 * c.anzahl) / max}%` })), h('span', { class: 'num' }, c.anzahl.toLocaleString('de-DE'))))) : null,
    data.haeufigste.length
      ? h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Adresse'), h('th', { class: 'num' }, 'Versuche'), h('th', {}, 'Herkunft'), h('th', {}, 'Zuletzt'))),
        h('tbody', {}, data.haeufigste.slice(0, 8).map((e) => h('tr', {}, h('td', { class: 'mono' }, e.ip), h('td', { class: 'num' }, e.anzahl.toLocaleString('de-DE')), h('td', {}, [e.flagge, e.stadt || e.land || '–'].filter(Boolean).join(' ')), h('td', {}, fmt.dateTime(e.zuletzt))))))
      : h('p', { class: 'empty' }, 'Keine fehlgeschlagenen Versuche in diesem Zeitraum.'),
    h('p', { style: 'margin-top:8px' }, h('a', { href: '/sicherheit' }, 'Alle Details →')));
}

function startLogins() {
  if (stopLogins) stopLogins();
  clear(loginsBody, h('p', { class: 'empty' }, 'Lade …'));
  stopLogins = repeat(async () => {
    try { renderLogins(await api(`/api/logins?stunden=${loginHours}`)); } catch (e) { clear(loginsBody, h('p', { class: 'error' }, e.message)); }
  }, 60000);
}
renderLoginRanges();
startLogins();

// --- Container

const dockerBody = document.getElementById('docker-body');
repeat(async () => {
  let data;
  try { data = await api('/api/docker'); } catch (e) { clear(dockerBody, h('p', { class: 'error' }, `Docker nicht erreichbar: ${e.message}`)); return; }
  const running = data.container.filter((c) => c.zustand === 'running').length;
  clear(dockerBody,
    h('p', { class: 'muted', style: 'margin:0 0 6px;font-size:0.82rem' }, `${running} von ${data.container.length} laufen`),
    h('ul', { class: 'rows' }, data.container.map((c) => h('li', {}, h('span', { class: `dot ${c.zustand === 'running' ? 'ok' : 'fail'}` }),
      h('div', { class: 'main' }, h('span', { class: 'title' }, c.name), h('span', { class: 'sub' }, c.status)),
      h('span', { class: 'muted mono', style: 'font-size:0.75rem' }, [c.cpu, c.speicher].filter(Boolean).join(' · '))))));
}, 30000);
