import { api, clear, fmt, h, startClock } from './common.js';

startClock();
const form = document.getElementById('notify-form');
const help = document.getElementById('notify-help');
const status = document.getElementById('notify-status');

function qrNode(svg) {
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
  return doc.documentElement;
}

function render(state) {
  clear(status, h('span', { class: `pill ${state.aktiv && state.topic ? 'ok' : 'warn'}` }, state.aktiv && state.topic ? 'aktiv' : 'aus'));
  const active = h('input', { type: 'checkbox', id: 'n-aktiv' }); active.checked = state.aktiv;
  const server = h('input', { id: 'n-server', value: state.server });
  const topic = h('input', { id: 'n-topic', value: state.topic, readonly: true, placeholder: 'noch kein Thema', class: 'mono' });
  const boxes = Object.entries(state.beschreibung).map(([key, label]) => {
    const box = h('input', { type: 'checkbox', dataset: { key } }); box.checked = state.ereignisse[key];
    return h('label', { class: 'check' }, box, label);
  });
  const error = h('p', { class: 'error' });
  const message = h('p', { class: 'muted' });

  async function save(extra = {}) {
    error.textContent = ''; message.textContent = '';
    const events = {};
    form.querySelectorAll('input[data-key]').forEach((box) => { events[box.dataset.key] = box.checked; });
    try {
      render(await api('/api/notify', { method: 'POST', body: { aktiv: active.checked, server: server.value, topic: topic.value, ereignisse: events, ...extra } }));
    } catch (e) { error.textContent = e.message; }
  }

  clear(form,
    h('div', { class: 'auth-form' },
      h('label', { class: 'check' }, active, 'Nachrichten aktiv'),
      h('label', {}, 'ntfy-Server', server),
      h('label', {}, 'Geheimes Thema (wie ein Passwort, nicht weitergeben)', topic),
      h('div', { class: 'row' },
        h('button', { class: 'ghost', type: 'button', onclick: () => save({ neues_thema: true }) }, state.topic ? 'Neues Thema erzeugen' : 'Thema erzeugen'),
        h('button', { class: 'ghost', type: 'button', onclick: async () => { message.textContent = 'Sende …'; try { await api('/api/notify/test', { method: 'POST' }); message.textContent = 'Testnachricht gesendet. Sie sollte gleich auf dem iPhone erscheinen.'; } catch (e) { message.textContent = ''; error.textContent = e.message; } } }, 'Testnachricht senden')),
      h('h3', { style: 'margin:6px 0 0;font-size:0.9rem' }, 'Wann soll eine Nachricht kommen?'),
      boxes,
      error, message,
      h('div', { class: 'row' }, h('button', { type: 'button', onclick: () => save() }, 'Speichern'))));

  clear(help,
    h('ol', { class: 'steps' },
      h('li', {}, 'Laden Sie die App ', h('b', {}, 'ntfy'), ' aus dem App Store (kostenlos).'),
      h('li', {}, 'Erzeugen Sie links ein Thema und speichern Sie. Tippen Sie in der App auf ', h('b', {}, '+'), ', dann „Thema abonnieren“, und tragen Sie das Thema ein. Oder scannen Sie den QR-Code mit der Kamera.'),
      h('li', {}, 'Erlauben Sie Mitteilungen. Für sofortige Zustellung auf dem iPhone nutzt ntfy den Apple-Push-Dienst über ntfy.sh.'),
      h('li', {}, 'Senden Sie links eine Testnachricht. Danach melden sich neue SSH-Anmeldungen und Terminzugriffe sofort.')),
    state.qr ? h('div', { class: 'qr' }, qrNode(state.qr)) : null,
    h('p', { class: 'muted' }, 'Tipp: In den iPhone-Einstellungen für ntfy „Zeitkritische Mitteilungen“ erlauben, dann kommen dringende Nachrichten auch im Fokus-Modus durch.'));
}

render(await api('/api/notify'));
