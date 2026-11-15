import { api, clear, confirmDialog, h, modal, requestElevation, startClock } from './common.js';

startClock();
const status = document.getElementById('term-status');
const container = document.getElementById('term');
const THEME = { background: '#182337', foreground: '#f6f4ef', cursor: '#c2a05e', cursorAccent: '#182337', selectionBackground: 'rgba(194,160,94,0.35)',
  black: '#182337', red: '#d9726b', green: '#7fc29b', yellow: '#c2a05e', blue: '#7f9fd6', magenta: '#b78fc4', cyan: '#6fc0c8', white: '#e8e4da',
  brightBlack: '#6b7488', brightRed: '#e88a84', brightGreen: '#9bd4b2', brightYellow: '#d8b878', brightBlue: '#9db6e4', brightMagenta: '#cba6d6', brightCyan: '#8dd0d6', brightWhite: '#ffffff' };
const mobile = window.matchMedia('(max-width: 640px)');

const term = new window.Terminal({ fontFamily: "'JetBrains Mono', monospace", fontSize: mobile.matches ? 12 : 14, cursorBlink: true, scrollback: 5000, theme: THEME, allowProposedApi: true });
const fit = new window.FitAddon.FitAddon();
term.loadAddon(fit);
term.open(container);
let socket = null;
let ctrl = false;

function setStatus(text, kind) { status.textContent = text; status.className = `pill ${kind || ''}`; }

async function connect() {
  if (socket && socket.readyState <= 1) return;
  setStatus('verbinde …', 'warn');
  try {
    const info = await api('/api/overview');
    if (!info.erhoeht) await requestElevation();
  } catch { setStatus('nicht freigegeben', 'fail'); showReconnect(); return; }
  fit.fit();
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${protocol}://${location.host}/ws/terminal?c=${term.cols}&r=${term.rows}`);
  socket.onopen = () => { setStatus('verbunden', 'ok'); term.focus(); };
  socket.onmessage = (event) => term.write(event.data);
  socket.onclose = () => { setStatus('getrennt', 'fail'); showReconnect(); };
  socket.onerror = () => setStatus('Fehler', 'fail');
}

function showReconnect() {
  term.write('\r\n\x1b[33m[Verbindung beendet – Taste drücken zum Verbinden]\x1b[0m\r\n');
  const disposable = term.onKey(() => { disposable.dispose(); connect(); });
}

function send(data) { if (socket && socket.readyState === 1) socket.send(JSON.stringify({ t: 'i', d: data })); }

// Strg-Taste der Tastenleiste: Der nächste Buchstabe wird als Steuerzeichen gesendet (Strg+C = "c")
term.onData((data) => {
  if (ctrl && data.length === 1) {
    ctrl = false; renderKeys();
    const code = data.toUpperCase().charCodeAt(0);
    send(code >= 64 && code <= 95 ? String.fromCharCode(code & 0x1f) : data);
    return;
  }
  send(data);
});

const resize = () => { fit.fit(); if (socket && socket.readyState === 1) socket.send(JSON.stringify({ t: 'r', c: term.cols, r: term.rows })); };
new ResizeObserver(resize).observe(container);
window.addEventListener('resize', resize);

// --- Handy: Höhe folgt der Bildschirmtastatur

function fitViewport() {
  const vv = window.visualViewport;
  document.documentElement.style.setProperty('--app-h', `${vv ? vv.height : window.innerHeight}px`);
  window.scrollTo(0, 0);
  resize();
}
if (window.visualViewport) { window.visualViewport.addEventListener('resize', fitViewport); window.visualViewport.addEventListener('scroll', () => window.scrollTo(0, 0)); }
fitViewport();

// --- Tastenleiste

const KEYS = [
  { label: 'Esc', send: '\x1b' }, { label: 'Tab', send: '\t' }, { label: 'Strg', toggle: true },
  { label: '↑', send: '\x1b[A' }, { label: '↓', send: '\x1b[B' }, { label: '←', send: '\x1b[D' }, { label: '→', send: '\x1b[C' },
  { label: '|', send: '|' }, { label: '~', send: '~' }, { label: '/', send: '/' }, { label: '-', send: '-' }, { label: '*', send: '*' }, { label: '$', send: '$' },
  { label: '^C', send: '\x03', title: 'Strg+C: Abbrechen' }, { label: '^D', send: '\x04', title: 'Strg+D: Beenden' }, { label: '^L', send: '\x0c', title: 'Strg+L: Bildschirm leeren' },
  { label: 'Bild↑', send: '\x1b[5~' }, { label: 'Bild↓', send: '\x1b[6~' }, { label: 'Einfügen', paste: true },
];
const keybar = document.getElementById('keybar');

async function paste() {
  try { const text = await navigator.clipboard.readText(); if (text) term.paste(text); }
  catch { modal('Einfügen', [h('p', {}, 'Der Browser erlaubt den Zugriff auf die Zwischenablage nicht. Halten Sie im Terminal gedrückt und wählen Sie „Einfügen“.')], []); }
}

function renderKeys() {
  clear(keybar, KEYS.map((key) => {
    const button = h('button', { class: `ghost${key.toggle && ctrl ? ' on' : ''}`, type: 'button', title: key.title || key.label, 'aria-label': key.title || key.label });
    button.textContent = key.label;
    // pointerdown + preventDefault: Das Terminal behält den Fokus, die Bildschirmtastatur bleibt offen
    button.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      if (key.toggle) { ctrl = !ctrl; renderKeys(); }
      else if (key.paste) paste();
      else send(key.send);
      term.focus();
    });
    return button;
  }));
}
renderKeys();

// --- Befehle (neben dem Terminal, auf dem Handy im Auswahlfenster)

async function loadCommands() { return (await api('/api/commands')).befehle; }

function pick(command, run) {
  if (!socket || socket.readyState !== 1) { connect(); return; }
  term.paste(command + (run ? '\r' : ''));
  term.focus();
}

function renderCommands(target, commands, after = () => {}) {
  const groups = new Map();
  for (const c of commands) { if (!groups.has(c.gruppe)) groups.set(c.gruppe, []); groups.get(c.gruppe).push(c); }
  clear(target, [...groups].map(([group, items]) => [
    h('div', { class: 'cmd-group' }, group),
    items.map((c) => h('div', { class: 'cmd' },
      h('button', { class: 'name', title: `${c.befehl}\n\nKlick: einfügen · ▶: einfügen und ausführen`, onclick: () => { pick(c.befehl, false); after(); } }, c.name),
      h('button', { class: 'ghost small', title: 'Einfügen und ausführen', 'aria-label': `${c.name} ausführen`, onclick: () => { pick(c.befehl, true); after(); } }, '▶'),
      c.eigen ? h('button', { class: 'ghost small', title: 'Löschen', 'aria-label': `${c.name} löschen`, onclick: async () => { if (await confirmDialog('Befehl löschen?', c.name, { danger: true, label: 'Löschen' })) { await api(`/api/commands/${c.id}`, { method: 'DELETE' }); refresh(); } } }, '✕') : null)),
  ]));
}

const list = document.getElementById('cmds');
let sheet = null;
async function refresh() {
  const commands = await loadCommands();
  renderCommands(list, commands);
  if (sheet) renderCommands(sheet.target, commands, sheet.close);
}

function addCommandDialog() {
  const group = h('input', { placeholder: 'Eigene', value: 'Eigene' }), name = h('input', { placeholder: 'z. B. Backup prüfen' }), command = h('textarea', { rows: '3', placeholder: 'Befehl' });
  const error = h('p', { class: 'error' });
  const close = modal('Neuer Befehl', [h('div', { class: 'auth-form' }, h('label', {}, 'Gruppe', group), h('label', {}, 'Name', name), h('label', {}, 'Befehl', command)), error], [
    h('button', { class: 'ghost', onclick: () => close() }, 'Abbrechen'),
    h('button', { onclick: async () => { try { await api('/api/commands', { method: 'POST', body: { gruppe: group.value, name: name.value, befehl: command.value } }); close(); refresh(); } catch (e) { error.textContent = e.message; } } }, 'Speichern'),
  ]);
  name.focus();
}
document.getElementById('cmd-add').addEventListener('click', addCommandDialog);

document.getElementById('cmd-open').addEventListener('click', async () => {
  const target = h('div', { class: 'sheet-list' });
  const close = modal('Befehle', [target], [h('button', { class: 'ghost small', onclick: () => { close(); sheet = null; addCommandDialog(); } }, '+ Neuer Befehl'), h('button', { class: 'ghost', onclick: () => { close(); sheet = null; } }, 'Schließen')]);
  sheet = { target, close: () => { close(); sheet = null; } };
  renderCommands(target, await loadCommands(), sheet.close);
});

refresh();
connect();
