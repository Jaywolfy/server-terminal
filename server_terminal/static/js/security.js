import { api, clear, confirmDialog, fmt, h, modal, repeat, startClock } from './common.js';

startClock();
const RANGES = { 1: '1 Std', 24: '24 Std', 168: '7 Tage', 720: '30 Tage' };
let hours = 24, stop = null;
const summary = document.getElementById('sec-summary');
const ranges = document.getElementById('sec-ranges');
const origin = (e) => [e.flagge, [e.stadt, e.land].filter(Boolean).join(', ') || '–'].filter(Boolean).join(' ');

function renderRanges() {
  clear(ranges, Object.entries(RANGES).map(([key, label]) => h('button', { class: Number(key) === hours ? 'active' : '', onclick: () => { hours = Number(key); renderRanges(); start(); } }, label)));
}

function render(data) {
  const max = Math.max(...data.laender.map((c) => c.anzahl), 1);
  clear(summary,
    h('div', { class: 'stats3' }, h('div', {}, h('b', {}, data.fehlgeschlagen.toLocaleString('de-DE')), h('span', {}, 'fehlgeschlagen')), h('div', {}, h('b', {}, data.ips), h('span', {}, 'Adressen')), h('div', {}, h('b', {}, data.erfolgreich), h('span', {}, 'erfolgreich'))),
    !data.herkunft_aktiv ? h('p', { class: 'muted' }, 'Herkunft ist abgeschaltet (--no-geo).') : null,
    data.herkunft_fehler ? h('p', { class: 'error' }, data.herkunft_fehler) : null,
    h('div', { class: 'bars' }, data.laender.map((c) => h('div', { class: 'bar-row' }, h('span', {}, c.land), h('span', { class: 'track' }, h('i', { style: `width:${(100 * c.anzahl) / max}%` })), h('span', { class: 'num' }, c.anzahl.toLocaleString('de-DE'))))),
    data.haeufigste.length === 0 ? h('p', { class: 'empty' }, 'Keine fehlgeschlagenen Versuche in diesem Zeitraum.') : null,
    data.haeufigste.length ? h('h3', { style: 'margin:14px 0 6px;font-size:0.9rem' }, 'Häufigste Adressen') : null,
    data.haeufigste.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Adresse', 'Versuche', 'Herkunft', 'Anbieter', 'Benutzernamen', 'Zuletzt'].map((t) => h('th', {}, t)))),
      h('tbody', {}, data.haeufigste.map((e) => h('tr', {}, h('td', { class: 'mono' }, e.ip), h('td', {}, e.anzahl.toLocaleString('de-DE')), h('td', {}, origin(e)), h('td', {}, e.anbieter || '–'), h('td', { class: 'mono' }, e.benutzer.join(', ')), h('td', {}, fmt.dateTime(e.zuletzt)))))) : null,
    data.zuletzt.length ? h('h3', { style: 'margin:14px 0 6px;font-size:0.9rem' }, 'Letzte Versuche') : null,
    data.zuletzt.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Zeit', 'Benutzer', 'Adresse', 'Herkunft', 'Art'].map((t) => h('th', {}, t)))),
      h('tbody', {}, data.zuletzt.map((e) => h('tr', {}, h('td', {}, fmt.full(e.ts)), h('td', { class: 'mono' }, e.benutzer || '–'), h('td', { class: 'mono' }, e.ip), h('td', {}, origin(e)), h('td', {}, e.methode))))) : null);
  clear(document.getElementById('sec-ok'),
    data.erfolgreiche.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Zeit', 'Benutzer', 'Adresse', 'Herkunft', 'Art'].map((t) => h('th', {}, t)))),
      h('tbody', {}, data.erfolgreiche.map((e) => h('tr', {}, h('td', {}, fmt.full(e.ts)), h('td', { class: 'mono' }, e.benutzer), h('td', { class: 'mono' }, e.ip), h('td', {}, origin(e)), h('td', {}, e.methode))))) : h('p', { class: 'empty' }, 'Keine erfolgreichen Anmeldungen in diesem Zeitraum.'));
}

function start() {
  if (stop) stop();
  clear(summary, h('p', { class: 'empty' }, 'Lade …'));
  stop = repeat(async () => {
    try { render(await api(`/api/logins?stunden=${hours}`)); } catch (e) { clear(summary, h('p', { class: 'error' }, e.message)); }
  }, 60000);
}
renderRanges();
start();

async function unban(ip) {
  const ok = await confirmDialog('Adresse entsperren?', `${ip} wird in fail2ban entsperrt.`, { label: 'Entsperren' });
  if (!ok) return;
  try { await api('/api/unban', { method: 'POST', body: { ip } }); await loadBans(); } catch (e) { modal('Fehler', [h('p', { class: 'error' }, e.message)], []); }
}

async function loadBans() {
  const data = await api('/api/bans');
  clear(document.getElementById('ban-note'), `${data.gesperrt.length} gesperrt`);
  clear(document.getElementById('bans'), data.gesperrt.length
    ? h('ul', { class: 'rows' }, data.gesperrt.map((ip) => h('li', {}, h('span', { class: 'main mono' }, ip), h('button', { class: 'ghost small', onclick: () => unban(ip) }, 'Entsperren'))))
    : h('p', { class: 'empty' }, 'Aktuell ist keine Adresse gesperrt.'));
}
repeat(loadBans, 30000);

const AUDIT = { angemeldet: 'Angemeldet', abgemeldet: 'Abgemeldet', anmeldung_fehlgeschlagen: 'Anmeldung fehlgeschlagen', anmeldung_gesperrt: 'Anmeldung gesperrt', freigeschaltet: 'Freigeschaltet (2FA)', freischaltung_fehlgeschlagen: 'Freischaltung fehlgeschlagen', terminal_geoeffnet: 'Terminal geöffnet', terminal_geschlossen: 'Terminal geschlossen', updates_gestartet: 'Updates gestartet', updates_geprueft: 'Updates geprüft', neustart: 'Server-Neustart', entsperrt: 'IP entsperrt', konto_eingerichtet: 'Konto eingerichtet', einrichtung_falscher_code: 'Falscher Einrichtungscode' };
repeat(async () => {
  const data = await api('/api/audit');
  clear(document.getElementById('audit'), data.eintraege.length
    ? h('table', {}, h('thead', {}, h('tr', {}, ['Zeit', 'Ereignis', 'Adresse', 'Details'].map((t) => h('th', {}, t)))),
      h('tbody', {}, data.eintraege.map((e) => h('tr', {}, h('td', {}, fmt.full(e.zeit * 1000)), h('td', {}, AUDIT[e.ereignis] || e.ereignis.replace('docker_', 'Docker: ')), h('td', { class: 'mono' }, e.ip), h('td', { class: 'mono' }, e.detail)))))
    : h('p', { class: 'empty' }, 'Noch keine Einträge.'));
}, 20000);
