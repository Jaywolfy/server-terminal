import { api, clear, confirmDialog, fmt, h, modal, repeat, startClock } from './common.js';

startClock();
const table = document.getElementById('docker-table');
const note = document.getElementById('docker-note');
const processes = document.getElementById('processes');

async function act(name, action, label) {
  const ok = await confirmDialog(`${label}?`, `Container „${name}“ wird ${label.toLowerCase()}.`, { danger: action !== 'start', label });
  if (!ok) return;
  try { await api(`/api/docker/${encodeURIComponent(name)}/${action}`, { method: 'POST' }); await load(); } catch (e) { modal('Fehler', [h('p', { class: 'error' }, e.message)], []); }
}

async function showLogs(name) {
  const pre = h('pre', { class: 'console' }, 'Lade …');
  modal(`Protokoll: ${name}`, [pre], [], { wide: true });
  try { pre.textContent = (await api(`/api/docker/${encodeURIComponent(name)}/logs`)).logs || '(leer)'; pre.scrollTop = pre.scrollHeight; } catch (e) { pre.textContent = e.message; }
}

async function load() {
  let data;
  try { data = await api('/api/docker'); } catch (e) { clear(table, h('p', { class: 'error' }, `Docker nicht erreichbar: ${e.message}`)); return; }
  const running = data.container.filter((c) => c.zustand === 'running').length;
  clear(note, `${running} von ${data.container.length} laufen`);
  clear(table, h('table', {},
    h('thead', {}, h('tr', {}, ['Name', 'Image', 'Zustand', 'CPU', 'Speicher', 'Ports', ''].map((t) => h('th', {}, t)))),
    h('tbody', {}, data.container.map((c) => h('tr', {},
      h('td', {}, h('b', {}, c.name)), h('td', { class: 'mono' }, c.image.length > 34 ? c.image.slice(0, 34) + '…' : c.image),
      h('td', {}, h('span', { class: `dot ${c.zustand === 'running' ? 'ok' : 'fail'}` }), c.status),
      h('td', {}, c.cpu), h('td', {}, c.speicher), h('td', { class: 'mono', style: 'max-width:18rem;word-break:break-word' }, c.ports),
      h('td', { style: 'white-space:nowrap' },
        h('button', { class: 'ghost small', onclick: () => showLogs(c.name) }, 'Logs'), ' ',
        c.zustand === 'running' ? [h('button', { class: 'ghost small', onclick: () => act(c.name, 'restart', 'Neu starten') }, 'Neustart'), ' ', h('button', { class: 'ghost small', onclick: () => act(c.name, 'stop', 'Stoppen') }, 'Stopp')]
          : h('button', { class: 'small', onclick: () => act(c.name, 'start', 'Starten') }, 'Start')))))));
}

repeat(load, 30000);
repeat(async () => {
  const data = await api('/api/processes');
  clear(processes,
    data.dienste_fehlgeschlagen.length ? h('p', { class: 'error' }, `Fehlgeschlagene Dienste: ${data.dienste_fehlgeschlagen.join(', ')}`) : h('p', { class: 'muted', style: 'margin:0 0 8px' }, 'Keine fehlgeschlagenen Systemdienste.'),
    h('table', {}, h('thead', {}, h('tr', {}, ['PID', 'Benutzer', 'CPU', 'Speicher', 'Läuft', 'Prozess'].map((t) => h('th', {}, t)))),
      h('tbody', {}, data.prozesse.map((p) => h('tr', {}, h('td', { class: 'mono' }, p.pid), h('td', {}, p.benutzer), h('td', {}, `${p.cpu} %`), h('td', {}, `${p.speicher} %`), h('td', {}, p.laeuft), h('td', { class: 'mono' }, p.name))))));
}, 10000);
