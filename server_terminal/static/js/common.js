// Gemeinsame Helfer: Elemente bauen (ohne innerHTML, damit nichts eingeschleust werden kann), Schnittstelle, Uhr, Dialoge.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
}

const TZ = 'Europe/Berlin';
const clockFormat = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const dateTimeFormat = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
const fullFormat = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const timeFormat = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit' });
const timeShort = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
const dayTime = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, weekday: 'short', hour: '2-digit', minute: '2-digit' });
const dateOnly = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' });

export const fmt = {
  clock: (ms) => clockFormat.format(ms),
  dateTime: (value) => dateTimeFormat.format(typeof value === 'string' ? new Date(value) : value),
  full: (value) => fullFormat.format(typeof value === 'string' ? new Date(value) : value),
  time: (ms) => timeFormat.format(ms),
  short: (ms) => timeShort.format(ms),
  dayTime: (ms) => dayTime.format(ms),
  date: (ms) => dateOnly.format(ms),
  bytes(value) {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let n = Math.abs(value), i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
  },
  rate: (value) => `${fmt.bytes(value)}/s`,
  percent: (value) => `${value.toFixed(value < 10 ? 1 : 0)} %`,
  duration(seconds) {
    const d = Math.floor(seconds / 86400), hh = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
    return d > 0 ? `${d} Tg ${hh} Std` : hh > 0 ? `${hh} Std ${m} Min` : `${m} Min`;
  },
};

// --- Schnittstelle

let elevatePromise = null;

export async function api(path, { method = 'GET', body = undefined, retried = false } = {}) {
  const options = { method, headers: { 'X-Requested-With': 'server-terminal' }, credentials: 'same-origin' };
  if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  const response = await fetch(path, options);
  if (response.status === 401) { location.href = '/login'; throw new Error('Nicht angemeldet'); }
  let data = {};
  try { data = await response.json(); } catch { /* keine JSON-Antwort */ }
  if (response.status === 403 && data.erhoehen && !retried) {
    await requestElevation();
    return api(path, { method, body, retried: true });
  }
  if (!response.ok) throw new Error(data.fehler || `Fehler ${response.status}`);
  return data;
}

export function requestElevation() {
  if (elevatePromise) return elevatePromise;
  elevatePromise = new Promise((resolve, reject) => {
    const input = h('input', { name: 'code', inputmode: 'numeric', autocomplete: 'one-time-code', placeholder: '123456', maxlength: '7' });
    const error = h('p', { class: 'error' });
    const close = modal('Bestätigung nötig', [
      h('p', {}, 'Für diese Aktion bitte den aktuellen Code aus der Authenticator-App eingeben.'), input, error,
    ], [
      h('button', { class: 'ghost', onclick: () => { close(); reject(new Error('Abgebrochen')); } }, 'Abbrechen'),
      h('button', { onclick: submit }, 'Bestätigen'),
    ]);
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') submit(); });
    input.focus();
    async function submit() {
      try {
        await api('/api/elevate', { method: 'POST', body: { code: input.value }, retried: true });
        close(); resolve();
      } catch (e) { error.textContent = e.message; input.select(); }
    }
  }).finally(() => { elevatePromise = null; });
  return elevatePromise;
}

// --- Dialoge

export function modal(title, content, buttons = [], { wide = false } = {}) {
  const root = document.getElementById('modal-root');
  const back = h('div', { class: 'modal-back' });
  const box = h('div', { class: `modal${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('h2', {}, title), ...[content].flat(), buttons.length ? h('div', { class: 'row' }, buttons) : null);
  back.append(box);
  root.append(back);
  const close = () => back.remove();
  back.addEventListener('mousedown', (event) => { if (event.target === back) close(); });
  document.addEventListener('keydown', function onKey(event) { if (event.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } });
  return close;
}

export function confirmDialog(title, text, { danger = false, label = 'Ja, ausführen' } = {}) {
  return new Promise((resolve) => {
    const close = modal(title, [h('p', {}, text)], [
      h('button', { class: 'ghost', onclick: () => { close(); resolve(false); } }, 'Abbrechen'),
      h('button', { class: danger ? 'danger' : '', onclick: () => { close(); resolve(true); } }, label),
    ]);
  });
}

// --- Uhr und Kopfzeile

export async function startClock() {
  const target = document.getElementById('clock');
  const host = document.getElementById('host');
  let offset = 0;
  const sync = async () => {
    try {
      const data = await api('/api/overview');
      offset = data.zeit * 1000 - Date.now();
      if (host) host.textContent = `${data.hostname} · ${data.betriebssystem}`;
    } catch { /* wird beim nächsten Mal erneut versucht */ }
  };
  await sync();
  const tick = () => { if (target) target.textContent = fmt.clock(Date.now() + offset); };
  tick();
  setInterval(tick, 1000);
  setInterval(sync, 5 * 60 * 1000);
}

export function repeat(fn, milliseconds) {
  let stopped = false;
  const run = async () => { if (stopped) return; try { await fn(); } catch { /* Fehler zeigt die Funktion selbst an */ } };
  run();
  const id = setInterval(() => { if (!document.hidden) run(); }, milliseconds);
  return () => { stopped = true; clearInterval(id); };
}
