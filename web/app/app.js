// Flowwolf editor: app shell, routing and management pages. No build step, no dependencies.
'use strict';

// ---------- tiny DOM helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
function h(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => ((Math.random() * 16) | (c === 'x' ? 0 : 8)).toString(16)));
const ago = (iso) => {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' h ago';
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
};
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '');
const dur = (a, b) => {
  if (!a || !b) return '';
  const ms = new Date(b) - new Date(a);
  return ms < 1000 ? ms + 'ms' : (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + 's';
};

// ---------- icons (24x24 stroke) ----------
const ICONS = {
  sparkles: '<path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  cart: '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.5 12.5h12L22 7H6.2"/>',
  chart: '<path d="M4 20V4M4 20h16"/><path d="M8 16v-5M12 16V8M16 16v-3M20 16V6"/>',
  ticket: '<path d="M3 8a2 2 0 0 0 0 4v0a2 2 0 0 1 0 4v2h18v-2a2 2 0 0 1 0-4 2 2 0 0 0 0-4V6H3z"/><path d="M14 6v12" stroke-dasharray="2 2"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
  map: '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
  zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  book: '<path d="M4 4h6a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z"/><path d="M20 4h-4a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h5z"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  cloud: '<path d="M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  inbox: '<path d="M3 13l3-8h12l3 8v6H3z"/><path d="M3 13h5l1 2h6l1-2h5"/>',
  board: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8v8M12 8v5M17 8v10"/>',
  play: '<path d="M7 4v16l13-8z"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
  webhook: '<circle cx="6" cy="17" r="3"/><circle cx="18" cy="17" r="3"/><circle cx="12" cy="6" r="3"/><path d="M9 17h6M7.5 14.5l3-6M16.5 14.5l-3-6"/>',
  form: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  workflow: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><path d="M10 6.5h4a3 3 0 0 1 3 3V14"/>',
  alert: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  code: '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>', pen: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/>',
  branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10M18 9c0 5-7 4-11 8"/>',
  filter: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>', switch: '<path d="M3 12h6l4-6h8M9 12l4 6h8M13 12h8"/>',
  merge: '<path d="M6 4v4c0 3.3 2.7 6 6 6h7M16 11l3 3-3 3M6 20v-3"/>',
  loop: '<path d="m17 2 3 3-3 3"/><path d="M4 11V9a4 4 0 0 1 4-4h12M7 22l-3-3 3-3"/><path d="M20 13v2a4 4 0 0 1-4 4H4"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>', stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  reply: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 6 6v5"/>', split: '<path d="M12 3v6M12 9l-6 6v6M12 9l6 6v6"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>', sort: '<path d="M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M4 16V5a1 1 0 0 1 1-1h11"/>', sigma: '<path d="M18 4H6l6 8-6 8h12"/>',
  tag: '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  html: '<path d="M4 3h16l-1.5 16L12 21l-6.5-2z"/><path d="M15.5 8h-7l.3 4h6.4l-.4 4-2.8 1-2.8-1"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  redis: '<path d="m12 4 9 4-9 4-9-4z"/><path d="m3 12 9 4 9-4M3 16l9 4 9-4"/>', slack: '<path d="M9 3 7 21M17 3l-2 18M4 8h17M3 16h17"/>',
  telegram: '<path d="M21 3 3 10.5l7 2.5 2.5 7z"/><path d="m10 13 4-4"/>',
  discord: '<path d="M4 5h16v11H9l-5 4z"/><circle cx="9.5" cy="10.5" r="1"/><circle cx="14.5" cy="10.5" r="1"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>', note: '<path d="M5 3h14v12l-6 6H5z"/><path d="M13 21v-6h6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', trash: '<path d="M4 7h16M10 11v6M14 11v6M5 7l1 13h12l1-13M9 7V4h6v3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>', check: '<path d="m5 12 5 5 9-10"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M14 9l2 2"/>', list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>', zoomin: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M11 8v6M8 11h6"/>',
  zoomout: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M8 11h6"/>', fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  pin: '<path d="M12 17v5M9 3h6l-1 6 4 4H6l4-4z"/>', upload: '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>', download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
  power: '<path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0"/>', users: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0M16 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-5-6.7"/>',
  variable: '<path d="M8 4c-2 3-2 13 0 16M16 4c2 3 2 13 0 16M10 9l4 6M14 9l-4 6"/>', history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>', eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  github: '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
  notion: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M9 16V8l6 8V8"/>', table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/>',
  card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20M6 15h4"/>', chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/>',
  rss: '<path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
  back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  wolf: '<path d="M4 4l4 5h8l4-5v9l-8 7-8-7z"/><path d="M9 13h.01M15 13h.01"/>', menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5"/>',
};
// Brand colours close to black would vanish on the dark canvas; those icons use the text colour.
const inkOf = (c) => {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c || '');
  if (!m) return c || 'var(--text)';
  const h = m[1].length === 3 ? [...m[1]].map((x) => x + x).join('') : m[1];
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.16 ? 'var(--text)' : c;
};
const icon = (n, cls = 'i') => `<svg class="${cls}" viewBox="0 0 24 24">${ICONS[n] || ICONS.arrow}</svg>`;

// ---------- API ----------
async function api(method, path, body, opts = {}) {
  const r = await fetch('/rest' + path, {
    method,
    headers: body !== undefined ? { 'content-type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
    signal: opts.signal,
  });
  let j = null;
  try { j = await r.json(); } catch { /* empty body */ }
  if (r.status === 401 && !path.startsWith('/login')) {
    App.user = null;
    location.hash = '#/login';
    throw new Error('Please sign in');
  }
  if (!r.ok) { const e = new Error((j && j.message) || r.statusText); e.code = j && j.code; throw e; }
  return j ? j.data : null;
}

// ---------- toasts, modals, menus ----------
function toast(title, msg = '', err = false) {
  let box = $('.toasts');
  if (!box) document.body.append((box = h('<div class="toasts"></div>')));
  const t = h(`<div class="toast ${err ? 'err' : ''}"><b>${esc(title)}</b>${msg ? `<span class="muted">${esc(msg)}</span>` : ''}</div>`);
  box.append(t);
  setTimeout(() => t.remove(), err ? 7000 : 3500);
}
const fail = (e) => toast('Something went wrong', e.message || String(e), true);

function modal(title, body, buttons = [], opts = {}) {
  return new Promise((resolve) => {
    const o = h(`<div class="overlay"><div class="modal" style="max-width:${opts.width || 520}px"><div class="modal-head"><h2>${esc(title)}</h2>
      <button class="btn ghost sm icon" data-x>${icon('x')}</button></div><div class="modal-body"></div><div class="modal-foot"></div></div></div>`);
    const close = (v) => { o.remove(); document.removeEventListener('keydown', key); resolve(v); };
    const key = (e) => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', key);
    if (typeof body === 'string') $('.modal-body', o).innerHTML = body;
    else $('.modal-body', o).append(body);
    if (!buttons.length) $('.modal-foot', o).remove();
    for (const b of buttons) {
      const el = h(`<button class="btn ${b.cls || ''}">${esc(b.label)}</button>`);
      el.onclick = async () => {
        if (!b.action) return close(b.value ?? null);
        try {
          const v = await b.action(o);
          if (v !== false) close(v ?? true);
        } catch (e) { fail(e); }
      };
      $('.modal-foot', o).append(el);
    }
    $('[data-x]', o).onclick = () => close(null);
    o.addEventListener('mousedown', (e) => { if (e.target === o) close(null); });
    document.body.append(o);
    setTimeout(() => o.querySelector('input,textarea,select')?.focus(), 30);
  });
}
const confirmBox = (title, text, label = 'Delete') => modal(title, `<p style="margin:0">${esc(text)}</p>`, [{ label: 'Cancel', value: false }, { label, cls: 'primary', value: true }]);
async function prompt(title, label, value = '') {
  const body = h(`<div class="field"><label class="label">${esc(label)}</label><input class="input" value="${esc(value)}"></div>`);
  $('input', body).onkeydown = (e) => { if (e.key === 'Enter') body.closest('.modal').querySelector('.modal-foot .primary').click(); };
  return modal(title, body, [{ label: 'Cancel', value: null }, { label: 'Save', cls: 'primary', action: () => $('input', body).value }]);
}

function menu(x, y, items) {
  document.querySelectorAll('.menu').forEach((m) => m.remove());
  const m = h('<div class="menu"></div>');
  for (const it of items) {
    if (it === '-') { m.append(h('<hr>')); continue; }
    const b = h(`<button class="${it.danger ? 'btn-danger' : ''}" style="${it.danger ? 'color:var(--err)' : ''}">${it.icon ? icon(it.icon) : ''}<span>${esc(it.label)}</span></button>`);
    b.onclick = () => { m.remove(); it.action(); };
    m.append(b);
  }
  document.body.append(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + 'px';
  m.style.top = Math.min(y, innerHeight - r.height - 8) + 'px';
  setTimeout(() => document.addEventListener('mousedown', function off(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('mousedown', off); } }), 0);
}

function download(name, text) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
const pickFile = () => new Promise((res) => {
  const i = h('<input type="file" accept=".json,application/json">');
  i.onchange = () => i.files[0] ? i.files[0].text().then(res) : res(null);
  i.click();
});
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast('Copied to clipboard'); } catch { toast('Copy failed', t, true); }
}

// ---------- app state & router ----------
const App = { user: null, settings: null, nodeTypes: [], credTypes: [], route: '', leaveGuard: null };
const typeOf = (name) => App.nodeTypes.find((t) => t.name === name) || { name, displayName: name.split('.').pop(), icon: 'arrow', color: '#888', properties: [], inputs: 1, outputs: [''], group: [] };
const credTypeOf = (name) => App.credTypes.find((t) => t.name === name) || { name, displayName: name, properties: [] };

async function boot() {
  const theme = localStorage.getItem('fw-theme');
  if (theme) document.documentElement.dataset.theme = theme;
  App.settings = await api('GET', '/settings');
  if (!App.settings.setupComplete) { location.hash = '#/setup'; return route(); }
  try { App.user = await api('GET', '/login'); } catch { App.user = null; }
  if (App.user) await loadTypes();
  route();
}
async function loadTypes() {
  [App.nodeTypes, App.credTypes] = await Promise.all([api('GET', '/node-types'), api('GET', '/credential-types')]);
}
addEventListener('hashchange', () => route());
addEventListener('beforeunload', (e) => { if (App.leaveGuard && App.leaveGuard()) { e.preventDefault(); e.returnValue = ''; } });

let lastHash = location.hash;
async function route() {
  const hash = location.hash || '#/workflows';
  if (App.leaveGuard && hash !== lastHash && App.leaveGuard()) {
    if (!(await confirmBox('Leave without saving?', 'Your changes will be lost.', 'Leave'))) { history.replaceState(null, '', lastHash); return; }
  }
  App.leaveGuard = null;
  lastHash = hash;
  const [path, qs] = hash.slice(1).split('?');
  const q = Object.fromEntries(new URLSearchParams(qs || ''));
  const parts = path.split('/').filter(Boolean);
  if (parts[0] === 'setup') return renderSetup();
  if (parts[0] === 'signup') return renderSignup(q.token || '');
  if (parts[0] === 'forgot-password') return renderForgot();
  if (parts[0] === 'change-password') return renderReset(q.token || '');
  if (parts[0] === 'login' || !App.user) return renderLogin();
  if (!App.nodeTypes.length) await loadTypes();
  const views = {
    workflows: () => pageWorkflows(),
    workflow: () => Editor.open(parts[1], q),
    execution: () => Editor.openExecution(parts[1]),
    executions: () => pageExecutions(q),
    credentials: () => pageCredentials(),
    variables: () => pageVariables(),
    settings: () => pageSettings(),
  };
  (views[parts[0]] || views.workflows)();
}

function shell(active, content, opts = {}) {
  const ini = ((App.user.firstName || App.user.email)[0] + ((App.user.lastName || '')[0] || '')).toUpperCase();
  const nav = [['workflows', 'workflow', 'Workflows'], ['credentials', 'key', 'Credentials'], ['executions', 'history', 'Executions'], ['variables', 'variable', 'Variables'], ['settings', 'settings', 'Settings']];
  const root = h(`<div class="shell ${opts.collapsed ? 'collapsed' : ''}">
    <aside class="side">
      <a class="logo" href="#/workflows"><span class="logo-mark">${icon('wolf')}</span><span>Flowwolf</span></a>
      ${nav.map(([k, ic, l]) => `<a class="nav ${active === k ? 'on' : ''}" href="#/${k}" title="${l}">${icon(ic)}<span>${l}</span></a>`).join('')}
      <div class="grow"></div>
      <button class="nav" data-theme title="Toggle theme">${icon('sun')}<span>Theme</span></button>
      <div class="me"><div class="avatar">${esc(ini)}</div><span class="grow" style="min-width:0;overflow:hidden;text-overflow:ellipsis;font-size:13px">${esc(App.user.firstName || App.user.email)}</span>
        <button class="btn ghost sm icon" data-logout title="Sign out">${icon('logout')}</button></div>
    </aside>
    <main class="main"></main></div>`);
  $('.main', root).append(content);
  $('[data-logout]', root).onclick = async () => { await api('POST', '/logout'); App.user = null; location.hash = '#/login'; };
  $('[data-theme]', root).onclick = () => {
    const dark = getComputedStyle(document.documentElement).colorScheme === 'dark';
    document.documentElement.dataset.theme = dark ? 'light' : 'dark';
    localStorage.setItem('fw-theme', document.documentElement.dataset.theme);
  };
  document.body.replaceChildren(root);
  return root;
}

// ---------- auth pages ----------
function authPage(title, sub, fields, btn, submit, foot = '') {
  const el = h(`<div class="auth"><form class="auth-card">
    <a class="logo" href="/"><span class="logo-mark">${icon('wolf')}</span><span>Flowwolf</span></a>
    <h1>${title}</h1><p class="muted" style="margin:0 0 20px">${sub}</p>
    ${fields.map(([n, l, t, ac, opt]) => `<div class="field"><label class="label">${l}</label><input class="input" name="${n}" type="${t}" autocomplete="${ac || n}" ${opt ? '' : 'required'}></div>`).join('')}
    <button class="btn primary" type="submit">${btn}</button><div class="err-text" role="alert"></div>${foot ? `<div class="auth-foot">${foot}</div>` : ''}</form></div>`);
  $('form', el).onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    $('.err-text', el).textContent = '';
    try {
      const user = await submit(data, el);
      if (user === undefined) return;
      App.user = user;
      await loadTypes();
      location.hash = '#/workflows';
      route();
    } catch (err) { $('.err-text', el).textContent = err.message; }
  };
  document.body.replaceChildren(el);
  $('input', el)?.focus();
  return el;
}

/** Sign-in; asks for the two-factor code when the account has 2FA. */
function renderLogin() {
  let creds = null;
  const el = authPage('Welcome back', 'Sign in to your automation workspace.', [['email', 'Email', 'email', 'username'], ['password', 'Password', 'password', 'current-password']], 'Sign in',
    async (d, page) => {
      try { return await api('POST', '/login', creds ? { ...creds, ...d } : d); } catch (e) {
        if (e.code !== 998) throw e;
        if (creds) throw new Error('The code is wrong. Try again.');
        creds = d;
        $('.auth-card h1', page).textContent = 'Two-factor code';
        $('.auth-card > p', page).textContent = 'Enter the 6-digit code from your authenticator app.';
        page.querySelectorAll('.field').forEach((f) => f.remove());
        const f = h(`<div class="field"><label class="label" data-l>Code</label><input class="input" name="mfaCode" inputmode="numeric" autocomplete="one-time-code" required>
          <a href="#" class="sub" data-rc style="display:inline-block;margin-top:8px">Use a recovery code instead</a></div>`);
        $('[data-rc]', f).onclick = (ev) => { ev.preventDefault(); const i = $('input', f); i.name = i.name === 'mfaCode' ? 'mfaRecoveryCode' : 'mfaCode'; i.removeAttribute('inputmode');
          $('[data-l]', f).textContent = i.name === 'mfaCode' ? 'Code' : 'Recovery code'; ev.target.textContent = i.name === 'mfaCode' ? 'Use a recovery code instead' : 'Use the app code instead'; i.focus(); };
        $('button[type=submit]', page).before(f);
        $('button[type=submit]', page).textContent = 'Verify';
        $('input', f).focus();
        return undefined;
      }
    }, '<a href="#/forgot-password">Forgot your password?</a>');
  return el;
}
function renderForgot() {
  authPage('Reset your password', 'We email you a link to choose a new one. Without email set up, ask your admin for a reset link.', [['email', 'Email', 'email', 'username']], 'Send link',
    async (d, page) => { await api('POST', '/forgot-password', d); $('.err-text', page).textContent = ''; $('.auth-card > p', page).textContent = 'If this address has an account, a link is on its way.'; return undefined; },
    '<a href="#/login">Back to sign in</a>');
}
async function renderSignup(token) {
  let info;
  try { info = await api('GET', '/resolve-signup-token?token=' + encodeURIComponent(token)); } catch (e) {
    authPage('Invitation not valid', esc(e.message) + ' Ask your admin for a new invitation.', [], 'Go to sign in', async () => { location.hash = '#/login'; return undefined; }); return;
  }
  const by = [info.inviter.firstName, info.inviter.lastName].filter(Boolean).join(' ') || 'Your admin';
  authPage('Join Flowwolf', `${esc(by)} invited <b>${esc(info.email)}</b>. Choose your name and a password.`,
    [['firstName', 'First name', 'text', 'given-name'], ['lastName', 'Last name', 'text', 'family-name', true], ['password', 'Password (min. 8 characters)', 'password', 'new-password']],
    'Create account', (d) => api('POST', '/invitations/accept', { ...d, token }));
}
async function renderReset(token) {
  let info;
  try { info = await api('GET', '/resolve-password-token?token=' + encodeURIComponent(token)); } catch (e) {
    authPage('Link not valid', esc(e.message), [], 'Request a new link', async () => { location.hash = '#/forgot-password'; return undefined; }); return;
  }
  authPage('Choose a new password', 'You will be signed out on all devices.',
    [['password', 'New password (min. 8 characters)', 'password', 'new-password'], ...(info.mfaEnabled ? [['mfaCode', 'Two-factor code', 'text', 'one-time-code']] : [])],
    'Save password', async (d, page) => { await api('POST', '/change-password', { ...d, token }); toast('Password changed', 'Sign in with the new password'); location.hash = '#/login'; return undefined; });
}
const renderSetup = () => authPage('Set up owner account', 'You are the first user — this account will own the instance.',
  [['firstName', 'First name', 'text', 'given-name'], ['lastName', 'Last name', 'text', 'family-name'], ['email', 'Email', 'email'], ['password', 'Password (min. 8 characters)', 'password', 'new-password']],
  'Create account', (d) => api('POST', '/owner/setup', d));

// ---------- templates ----------
const TEMPLATES = [
  {
    name: 'Webhook → transform → respond', desc: 'A tiny API endpoint that greets whoever calls it.',
    wf: {
      nodes: [
        { name: 'Webhook', type: 'n8n-nodes-base.webhook', typeVersion: 2, position: [0, 0], parameters: { httpMethod: 'POST', path: 'hello', responseMode: 'responseNode' } },
        { name: 'Edit Fields', type: 'n8n-nodes-base.set', typeVersion: 3.4, position: [240, 0], parameters: { mode: 'manual', assignments: { assignments: [{ id: '1', name: 'greeting', type: 'string', value: '=Hello {{ $json.body.name ?? "stranger" }}!' }] }, includeOtherFields: false, options: {} } },
        { name: 'Respond to Webhook', type: 'n8n-nodes-base.respondToWebhook', typeVersion: 1.1, position: [480, 0], parameters: { respondWith: 'firstIncomingItem', options: {} } },
      ],
      connections: { Webhook: { main: [[{ node: 'Edit Fields', type: 'main', index: 0 }]] }, 'Edit Fields': { main: [[{ node: 'Respond to Webhook', type: 'main', index: 0 }]] } },
    },
  },
  {
    name: 'Fetch, filter & summarize', desc: 'Calls a public API, keeps the interesting items and summarizes them.',
    wf: {
      nodes: [
        { name: "When clicking 'Test workflow'", type: 'n8n-nodes-base.manualTrigger', typeVersion: 1, position: [0, 0], parameters: {} },
        { name: 'HTTP Request', type: 'n8n-nodes-base.httpRequest', typeVersion: 4.2, position: [240, 0], parameters: { method: 'GET', url: 'https://jsonplaceholder.typicode.com/todos', options: {} } },
        { name: 'If', type: 'n8n-nodes-base.if', typeVersion: 2, position: [480, 0], parameters: { conditions: { combinator: 'and', options: { caseSensitive: true }, conditions: [{ id: 'c1', leftValue: '={{ $json.completed }}', rightValue: '', operator: { type: 'boolean', operation: 'true', singleValue: true } }] } } },
        { name: 'Summarize', type: 'n8n-nodes-base.summarize', typeVersion: 1, position: [720, -100], parameters: { fieldsToSummarize: { values: [{ aggregation: 'count', field: 'id' }] }, fieldsToSplitBy: 'userId' } },
        { name: 'Limit', type: 'n8n-nodes-base.limit', typeVersion: 1, position: [720, 100], parameters: { maxItems: 5 } },
      ],
      connections: { "When clicking 'Test workflow'": { main: [[{ node: 'HTTP Request', type: 'main', index: 0 }]] }, 'HTTP Request': { main: [[{ node: 'If', type: 'main', index: 0 }]] }, If: { main: [[{ node: 'Summarize', type: 'main', index: 0 }], [{ node: 'Limit', type: 'main', index: 0 }]] } },
    },
  },
  {
    name: 'Daily report with Code', desc: 'A schedule that builds a report in JavaScript and loops over batches.',
    wf: {
      nodes: [
        { name: 'Schedule Trigger', type: 'n8n-nodes-base.scheduleTrigger', typeVersion: 1.2, position: [0, 0], parameters: { rule: { interval: [{ field: 'days', triggerAtHour: 8 }] } } },
        { name: 'Code', type: 'n8n-nodes-base.code', typeVersion: 2, position: [240, 0], parameters: { jsCode: "const days = ['Mon','Tue','Wed','Thu','Fri'];\nreturn days.map((d, i) => ({ json: { day: d, tasks: (i + 1) * 3, date: $now.plus({ days: i }).toISODate() } }));" } },
        { name: 'Loop Over Items', type: 'n8n-nodes-base.splitInBatches', typeVersion: 3, position: [480, 0], parameters: { batchSize: 2, options: {} } },
        { name: 'Edit Fields', type: 'n8n-nodes-base.set', typeVersion: 3.4, position: [720, 100], parameters: { mode: 'manual', includeOtherFields: true, include: 'all', assignments: { assignments: [{ id: 'a', name: 'busy', type: 'boolean', value: '={{ $json.tasks > 6 }}' }] }, options: {} } },
        { name: 'Aggregate', type: 'n8n-nodes-base.aggregate', typeVersion: 1, position: [720, -120], parameters: { aggregate: 'aggregateAllItemData', destinationFieldName: 'report' } },
      ],
      connections: { 'Schedule Trigger': { main: [[{ node: 'Code', type: 'main', index: 0 }]] }, Code: { main: [[{ node: 'Loop Over Items', type: 'main', index: 0 }]] }, 'Loop Over Items': { main: [[{ node: 'Aggregate', type: 'main', index: 0 }], [{ node: 'Edit Fields', type: 'main', index: 0 }]] }, 'Edit Fields': { main: [[{ node: 'Loop Over Items', type: 'main', index: 0 }]] } },
    },
  },
];

function nodeIcons(types) {
  return [...new Set(types)].filter((t) => t !== 'n8n-nodes-base.stickyNote').slice(0, 5).map((t) => {
    const d = typeOf(t);
    return `<span class="mini" style="background:${d.color}" title="${esc(d.displayName)}">${icon(d.icon)}</span>`;
  }).join('');
}

async function createWorkflow(wf) {
  const w = await api('POST', '/workflows', { name: wf.name || 'My workflow', nodes: wf.nodes || [], connections: wf.connections || {}, settings: wf.settings || { executionOrder: 'v1' }, pinData: wf.pinData || {} });
  location.hash = '#/workflow/' + w.id;
}

async function importWorkflow() {
  const body = h(`<div><p class="muted" style="margin-top:0">Paste a workflow exported from Flowwolf or n8n, or choose a file.</p>
    <textarea class="input code" rows="12" placeholder='{"nodes": [...], "connections": {...}}'></textarea></div>`);
  const r = await modal('Import workflow', body, [
    { label: 'Choose file…', action: async () => { const t = await pickFile(); if (t) $('textarea', body).value = t; return false; } },
    { label: 'Import', cls: 'primary', action: () => JSON.parse($('textarea', body).value) },
  ]);
  if (r) await createWorkflow(r).catch(fail);
}

// ---------- pages ----------
async function pageWorkflows() {
  const page = h(`<div class="page"><div class="page-head"><h1>Workflows</h1>
    <div class="search">${icon('search')}<input class="input" placeholder="Search workflows"></div>
    <button class="btn" data-import>${icon('upload')}Import</button>
    <button class="btn primary" data-new>${icon('plus')}Create workflow</button></div><div class="chips" data-folders style="margin:-8px 0 16px"></div><div data-list></div></div>`);
  shell('workflows', page);
  let folder = sessionStorage.getItem('fw-folder') || '';
  $('[data-new]', page).onclick = () => (location.hash = '#/workflow/new' + (folder ? '?folder=' + encodeURIComponent(folder) : ''));
  $('[data-import]', page).onclick = importWorkflow;
  let all = [];
  const draw = () => {
    const q = $('input', page).value.toLowerCase();
    // Folder chips: "All" plus every folder path (and its parents) that holds workflows.
    const folders = [...new Set(all.flatMap((w) => (w.folder || '').split('/').filter(Boolean).map((_, i, a) => a.slice(0, i + 1).join('/'))))].sort();
    if (folder && !folders.includes(folder)) folder = '';
    const fbox = $('[data-folders]', page);
    fbox.innerHTML = folders.length ? [['', 'All workflows'], ...folders.map((f) => [f, f])].map(([v, l]) => `<button class="chip ${v === folder ? 'on' : ''}" data-f="${esc(v)}">${v ? icon('layers', 'i') : ''} ${esc(l)}</button>`).join('') : '';
    fbox.querySelectorAll('[data-f]').forEach((b) => (b.onclick = () => { folder = b.dataset.f; sessionStorage.setItem('fw-folder', folder); draw(); }));
    const inFolder = (w) => !folder || (w.folder || '') === folder || (w.folder || '').startsWith(folder + '/');
    const list = all.filter((w) => w.name.toLowerCase().includes(q) && inFolder(w));
    const box = $('[data-list]', page);
    if (!all.length) {
      box.innerHTML = `<div class="card empty"><h2>Build your first workflow</h2><p class="muted">Start from scratch or pick a template. Workflows exported from n8n can be imported as they are.</p>
        <button class="btn primary" data-new2>${icon('plus')}Start from scratch</button>
        <div class="tpl-grid">${TEMPLATES.map((t, i) => `<div class="card tpl" data-t="${i}"><div class="icons">${nodeIcons(t.wf.nodes.map((n) => n.type))}</div><b>${esc(t.name)}</b><div class="muted" style="font-size:13px">${esc(t.desc)}</div></div>`).join('')}</div></div>`;
      $('[data-new2]', box).onclick = () => (location.hash = '#/workflow/new');
      box.querySelectorAll('[data-t]').forEach((el) => (el.onclick = () => { const t = TEMPLATES[el.dataset.t]; createWorkflow({ ...clone(t.wf), name: t.name }).catch(fail); }));
      return;
    }
    box.replaceChildren(h(`<div class="list">${list.map((w) => `<div class="row" data-id="${w.id}">
      <div class="icons">${nodeIcons(w.nodeTypes || [])}</div>
      <div class="grow"><div class="t">${esc(w.name)}</div><div class="sub">${w.folder ? esc(w.folder) + ' · ' : ''}Updated ${ago(w.updatedAt)} · Created ${new Date(w.createdAt).toLocaleDateString()}</div></div>
      <span class="pill ${w.active ? 'ok' : ''}">${w.active ? 'Active' : 'Inactive'}</span>
      <button class="switch ${w.active ? 'on' : ''}" data-toggle title="Activate / deactivate"></button>
      <button class="btn ghost sm icon" data-more>${icon('more')}</button></div>`).join('') || '<div class="empty muted">No workflows match your search.</div>'}</div>`));
    box.querySelectorAll('.row').forEach((row) => {
      const w = all.find((x) => x.id === row.dataset.id);
      row.onclick = (e) => { if (!e.target.closest('button')) location.hash = '#/workflow/' + w.id; };
      $('[data-toggle]', row).onclick = async () => {
        try { const r = await api('POST', `/workflows/${w.id}/${w.active ? 'deactivate' : 'activate'}`); w.active = r.active; draw(); toast(w.active ? 'Workflow activated' : 'Workflow deactivated'); } catch (e) { fail(e); }
      };
      $('[data-more]', row).onclick = (e) => menu(e.clientX, e.clientY, [
        { label: 'Open', icon: 'edit', action: () => (location.hash = '#/workflow/' + w.id) },
        { label: 'Executions', icon: 'history', action: () => (location.hash = '#/executions?workflowId=' + w.id) },
        { label: 'Move to folder…', icon: 'layers', action: async () => {
          const f = await prompt('Move to folder', 'Folder path (empty = top level)', w.folder || folder);
          if (f === null) return;
          try { await api('PATCH', '/workflows/' + w.id, { folder: f.trim().replace(/^\/+|\/+$/g, '') }); w.folder = f.trim().replace(/^\/+|\/+$/g, ''); draw(); toast('Moved'); } catch (e) { fail(e); }
        } },
        { label: 'Duplicate', icon: 'copy', action: async () => { const full = await api('GET', '/workflows/' + w.id); await createWorkflow({ ...full, name: full.name + ' (copy)' }); } },
        { label: 'Download', icon: 'download', action: async () => { const full = await api('GET', '/workflows/' + w.id); download(w.name + '.json', JSON.stringify(full, null, 2)); } },
        '-',
        { label: 'Delete', icon: 'trash', danger: true, action: async () => { if (await confirmBox('Delete workflow?', `"${w.name}" will be deleted permanently.`)) { await api('DELETE', '/workflows/' + w.id); all = all.filter((x) => x.id !== w.id); draw(); } } },
      ]);
    });
  };
  $('input', page).oninput = draw;
  try { all = await api('GET', '/workflows'); } catch (e) { fail(e); }
  draw();
}

function statusPill(s) {
  const map = { success: ['ok', 'Succeeded'], error: ['err', 'Failed'], crashed: ['err', 'Crashed'], running: ['run', 'Running'], canceled: ['', 'Canceled'], waiting: ['run', 'Waiting'], queued: ['', 'Queued'], canceling: ['', 'Canceling'] };
  const [c, l] = map[s] || ['', s];
  return `<span class="pill ${c}"><span class="dot"></span>${l}</span>`;
}

async function pageExecutions(q) {
  const page = h(`<div class="page"><div class="page-head"><h1>Executions</h1>
    <select class="input" style="width:220px" data-wf><option value="">All workflows</option></select>
    <select class="input" style="width:150px" data-st><option value="">Any status</option><option value="success">Succeeded</option><option value="error">Failed</option><option value="running">Running</option><option value="canceled">Canceled</option></select>
    <button class="btn" data-refresh>${icon('history')}Refresh</button></div><div class="list" data-list></div>
    <div style="text-align:center;margin-top:16px"><button class="btn hidden" data-more>Load more</button></div></div>`);
  shell('executions', page);
  const wfs = await api('GET', '/workflows').catch(() => []);
  $('[data-wf]', page).append(...wfs.map((w) => h(`<option value="${w.id}">${esc(w.name)}</option>`)));
  $('[data-wf]', page).value = q.workflowId || '';
  let rows = [];
  const load = async (more) => {
    const p = new URLSearchParams({ limit: 50 });
    if ($('[data-wf]', page).value) p.set('workflowId', $('[data-wf]', page).value);
    if ($('[data-st]', page).value) p.set('status', $('[data-st]', page).value);
    if (more && rows.length) p.set('lastId', rows[rows.length - 1].id);
    const got = await api('GET', '/executions?' + p).catch((e) => (fail(e), []));
    rows = more ? rows.concat(got) : got;
    $('[data-more]', page).classList.toggle('hidden', got.length < 50);
    const box = $('[data-list]', page);
    if (!rows.length) { box.innerHTML = '<div class="empty"><h2>No executions yet</h2><p class="muted">Run a workflow and its executions show up here.</p></div>'; return; }
    box.innerHTML = `<table class="tbl"><thead><tr><th>Workflow</th><th>Status</th><th>Started</th><th>Run time</th><th>Mode</th><th>ID</th><th></th></tr></thead><tbody>${rows.map((x) => `<tr class="click" data-id="${x.id}">
      <td><b>${esc(x.workflowName || '—')}</b></td><td>${statusPill(x.status)}</td><td>${fmtDate(x.startedAt)}</td><td>${x.status === 'running' ? '…' : dur(x.startedAt, x.stoppedAt)}</td>
      <td class="muted">${esc(x.mode)}${x.retryOf ? ' (retry of #' + x.retryOf + ')' : ''}</td><td class="muted">#${x.id}</td>
      <td style="text-align:right;white-space:nowrap">${x.status === 'running' ? `<button class="btn sm" data-stop>Stop</button>` : ''}${x.status === 'error' ? `<button class="btn sm" data-retry>Retry</button>` : ''}
      <button class="btn ghost sm icon" data-del title="Delete">${icon('trash')}</button></td></tr>`).join('')}</tbody></table>`;
    box.querySelectorAll('tr[data-id]').forEach((tr) => {
      const id = tr.dataset.id;
      tr.onclick = (e) => { if (!e.target.closest('button')) location.hash = '#/execution/' + id; };
      tr.querySelector('[data-del]').onclick = async () => { await api('DELETE', '/executions/' + id).catch(fail); load(); };
      tr.querySelector('[data-stop]')?.addEventListener('click', async () => { await api('POST', `/executions/${id}/stop`).catch(fail); setTimeout(load, 600); });
      tr.querySelector('[data-retry]')?.addEventListener('click', async () => { try { const r = await api('POST', `/executions/${id}/retry`, {}); toast('Retry finished', 'Status: ' + r.status, r.status !== 'success'); load(); } catch (e) { fail(e); } });
    });
  };
  $('[data-wf]', page).onchange = () => load();
  $('[data-st]', page).onchange = () => load();
  $('[data-refresh]', page).onclick = () => load();
  $('[data-more]', page).onclick = () => load(true);
  load();
}

// ----- credentials -----
async function credentialModal(existing, type) {
  type = type || existing.type;
  const ct = credTypeOf(type);
  const data = existing ? (await api('GET', '/credentials/' + existing.id)).data : {};
  const body = h(`<div><div class="field"><label class="label">Name</label><input class="input" data-name value="${esc(existing ? existing.name : ct.displayName + ' account')}"></div><div data-form></div></div>`);
  const values = { ...Object.fromEntries(ct.properties.map((p) => [p.name, p.default])), ...data };
  const draw = () => {
    const f = $('[data-form]', body);
    f.innerHTML = '';
    for (const p of ct.properties) {
      if (p.type === 'hidden' || !Params.visible(p, values, ct.properties, values)) continue;
      const fld = h(`<div class="field"><label class="label">${esc(p.displayName)}</label></div>`);
      let inp;
      if (p.type === 'boolean') {
        inp = h(`<button type="button" class="switch ${values[p.name] ? 'on' : ''}"></button>`);
        inp.onclick = () => { values[p.name] = !values[p.name]; draw(); };
      } else if (p.type === 'json') {
        inp = h(`<textarea class="input code" rows="5"></textarea>`);
        inp.value = values[p.name] ?? '';
        inp.oninput = () => (values[p.name] = inp.value);
      } else {
        const masked = values[p.name] === '__FLOWWOLF_MASKED__';
        inp = h(`<input class="input" type="${p.typeOptions?.password ? 'password' : p.type === 'number' ? 'number' : 'text'}" placeholder="${esc(masked ? '•••••••• (unchanged)' : p.placeholder || '')}">`);
        inp.value = masked ? '' : values[p.name] ?? '';
        inp.oninput = () => (values[p.name] = p.type === 'number' ? Number(inp.value) : inp.value);
      }
      fld.append(inp);
      f.append(fld);
    }
    if (isOAuth && values.grantType !== 'clientCredentials') f.append(oauthBox());
  };
  // OAuth2: show the redirect URL to register at the provider and a connect button.
  const isOAuth = type === 'oAuth2Api' || type.endsWith('OAuth2Api');
  let saved = existing;
  const save = async () => {
    const payload = { name: $('[data-name]', body).value, type, data: values };
    saved = saved ? await api('PATCH', '/credentials/' + saved.id, payload).then(() => saved) : await api('POST', '/credentials', payload);
    return saved;
  };
  const oauthBox = () => {
    const redirect = location.origin + '/rest/oauth2-credential/callback';
    const box = h(`<div class="field"><label class="label">OAuth Redirect URL</label><div class="keybox"><span class="mono" style="flex:1">${esc(redirect)}</span><button class="btn sm" type="button" data-c>Copy</button></div>
      <div class="hint">Register this URL in the app settings of the provider.</div>
      <div style="display:flex;align-items:center;gap:10px;margin-top:12px"><button class="btn primary sm" type="button" data-connect>${icon('key')}${values.oauthConnected ? 'Reconnect' : 'Connect account'}</button>
      <span class="pill ${values.oauthConnected ? 'ok' : ''}">${values.oauthConnected ? 'Connected' : 'Not connected'}</span></div></div>`);
    $('[data-c]', box).onclick = () => copyText(redirect);
    $('[data-connect]', box).onclick = async () => {
      try {
        const c = await save();
        const { url } = await api('GET', '/oauth2-credential/auth?id=' + encodeURIComponent(c.id));
        const win = window.open(url, 'fw-oauth', 'width=560,height=720');
        const done = async (ev) => {
          if (ev.origin !== location.origin || ev.data?.type !== 'oauth') return;
          removeEventListener('message', done);
          const fresh = (await api('GET', '/credentials/' + c.id)).data;
          values.oauthConnected = fresh.oauthConnected;
          toast(fresh.oauthConnected ? 'Account connected' : 'Connection failed', '', !fresh.oauthConnected);
          draw();
        };
        addEventListener('message', done);
        if (!win) toast('Pop-up blocked', 'Allow pop-ups for this site and try again.', true);
      } catch (e) { fail(e); }
    };
    return box;
  };
  draw();
  return modal(existing ? 'Edit credential' : 'New ' + ct.displayName + ' credential', body, [
    { label: 'Cancel', value: null },
    {
      label: 'Save', cls: 'primary',
      action: () => save(),
    },
  ]);
}

// Share a workflow or credential with other users (table: "workflows" | "credentials").
async function shareModal(table, id, name) {
  const [info, users] = await Promise.all([api('GET', `/${table}/${id}/share`), api('GET', '/users')]);
  const roles = Object.fromEntries((info.shares || []).map((s) => [s.userId, s.role]));
  const owner = users.find((x) => x.id === info.ownerId);
  const others = users.filter((x) => x.id !== info.ownerId);
  const label = (x) => esc([x.firstName, x.lastName].join(' ').trim() || x.email);
  const body = h(`<div><p class="muted" style="margin-top:0">Owner: <b>${owner ? label(owner) : '—'}</b>. Shared users can open, edit and run it${table === 'credentials' ? ' in their workflows (the secret itself stays hidden)' : ''}.</p>
    <div class="list">${others.map((x) => `<div class="row" style="cursor:default"><div class="avatar">${esc((x.firstName || x.email)[0].toUpperCase())}</div>
      <div class="grow"><div class="t">${label(x)}</div><div class="sub">${esc(x.email)} · ${esc(x.role)}</div></div>
      <select class="input" style="width:150px" data-user="${x.id}" ${info.canManage ? '' : 'disabled'}><option value="">No access</option><option value="viewer">Can view</option><option value="editor">Can edit</option></select></div>`).join('') ||
      '<div class="empty muted" style="padding:24px">No other users yet. Add them under Settings → Users.</div>'}</div>
    ${info.canManage ? '' : '<p class="hint">Only the owner can change who has access.</p>'}</div>`);
  body.querySelectorAll('[data-user]').forEach((s) => (s.value = roles[s.dataset.user] || ''));
  const r = await modal(`Share “${name}”`, body, info.canManage ? [{ label: 'Cancel', value: null }, {
    label: 'Save', cls: 'primary', action: () => api('PUT', `/${table}/${id}/share`, { shares: [...body.querySelectorAll('[data-user]')].filter((s) => s.value).map((s) => ({ userId: s.dataset.user, role: s.value })) }),
  }] : [{ label: 'Close', cls: 'primary' }]);
  if (r && info.canManage) toast('Sharing updated');
}

async function chooseCredentialType() {
  const body = h(`<div><input class="input" placeholder="Search credential types" style="margin-bottom:10px"><div data-l style="max-height:380px;overflow:auto"></div></div>`);
  return new Promise((res) => {
    let done = false;
    const draw = () => {
      const q = $('input', body).value.toLowerCase();
      const l = $('[data-l]', body);
      l.innerHTML = App.credTypes.filter((c) => c.displayName.toLowerCase().includes(q)).map((c) => `<div class="citem" data-n="${c.name}"><div class="ic">${icon('key')}</div><div><b>${esc(c.displayName)}</b><small>${esc(c.name)}</small></div></div>`).join('');
      l.querySelectorAll('[data-n]').forEach((el) => (el.onclick = () => { done = true; body.closest('.overlay').remove(); res(el.dataset.n); }));
    };
    $('input', body).oninput = draw;
    draw();
    modal('Add credential', body).then(() => { if (!done) res(null); });
  });
}

async function pageCredentials() {
  const page = h(`<div class="page"><div class="page-head"><h1>Credentials</h1><button class="btn primary" data-new>${icon('plus')}Add credential</button></div><div data-list></div></div>`);
  shell('credentials', page);
  const load = async () => {
    const list = await api('GET', '/credentials').catch((e) => (fail(e), []));
    const box = $('[data-list]', page);
    if (!list.length) { box.innerHTML = `<div class="card empty"><h2>No credentials yet</h2><p class="muted">Credentials are encrypted with AES-256-GCM and only decrypted while a node runs.</p></div>`; return; }
    box.replaceChildren(h(`<div class="list">${list.map((c) => `<div class="row" data-id="${c.id}"><span class="mini" style="background:#5b5f6b">${icon('key')}</span>
      <div class="grow"><div class="t">${esc(c.name)}</div><div class="sub">${esc(credTypeOf(c.type).displayName)} · updated ${ago(c.updatedAt)}</div></div>
      <button class="btn ghost sm icon" data-share title="Share">${icon('users')}</button><button class="btn ghost sm icon" data-del>${icon('trash')}</button></div>`).join('')}</div>`));
    box.querySelectorAll('.row').forEach((row) => {
      const c = list.find((x) => x.id === row.dataset.id);
      row.onclick = async (e) => { if (e.target.closest('[data-del],[data-share]')) return; if (await credentialModal(c)) load(); };
      $('[data-share]', row).onclick = () => shareModal('credentials', c.id, c.name).catch(fail);
      $('[data-del]', row).onclick = async () => { if (await confirmBox('Delete credential?', `"${c.name}" will be deleted. Nodes using it will fail.`)) { await api('DELETE', '/credentials/' + c.id).catch(fail); load(); } };
    });
  };
  $('[data-new]', page).onclick = async () => { const t = await chooseCredentialType(); if (t && (await credentialModal(null, t))) load(); };
  load();
}

async function pageVariables() {
  const page = h(`<div class="page"><div class="page-head"><h1>Variables</h1><button class="btn primary" data-new>${icon('plus')}Add variable</button></div>
    <p class="muted" style="margin-top:-12px">Use them in any expression as <span class="mono">{{ $vars.KEY }}</span>.</p><div data-list></div></div>`);
  shell('variables', page);
  const edit = async (v) => {
    const body = h(`<div><div class="field"><label class="label">Key</label><input class="input mono" data-k value="${esc(v?.key || '')}" placeholder="API_BASE_URL"></div>
      <div class="field"><label class="label">Value</label><input class="input" data-v value="${esc(v?.value || '')}"></div></div>`);
    const r = await modal(v ? 'Edit variable' : 'New variable', body, [{ label: 'Cancel', value: null }, {
      label: 'Save', cls: 'primary', action: () => {
        const d = { key: $('[data-k]', body).value.trim(), value: $('[data-v]', body).value };
        return v ? api('PATCH', '/variables/' + v.id, d) : api('POST', '/variables', d);
      },
    }]);
    if (r) load();
  };
  const load = async () => {
    const list = await api('GET', '/variables').catch((e) => (fail(e), []));
    const box = $('[data-list]', page);
    if (!list.length) { box.innerHTML = '<div class="card empty"><h2>No variables</h2><p class="muted">Store values you reuse across workflows, like base URLs or IDs.</p></div>'; return; }
    box.innerHTML = `<div class="list"><table class="tbl"><thead><tr><th>Key</th><th>Value</th><th>Usage</th><th></th></tr></thead><tbody>${list.map((v) => `<tr class="click" data-id="${v.id}">
      <td class="mono">${esc(v.key)}</td><td>${esc(v.value)}</td><td class="mono muted">{{ $vars.${esc(v.key)} }}</td><td style="text-align:right"><button class="btn ghost sm icon" data-del>${icon('trash')}</button></td></tr>`).join('')}</tbody></table></div>`;
    box.querySelectorAll('tr[data-id]').forEach((tr) => {
      const v = list.find((x) => x.id === tr.dataset.id);
      tr.onclick = (e) => { if (!e.target.closest('[data-del]')) edit(v); };
      $('[data-del]', tr).onclick = async () => { await api('DELETE', '/variables/' + v.id).catch(fail); load(); };
    });
  };
  $('[data-new]', page).onclick = () => edit(null);
  load();
}

async function pageSettings() {
  const isOwner = ['owner', 'admin'].includes(App.user.role);
  const page = h(`<div class="page" style="max-width:820px"><div class="page-head"><h1>Settings</h1></div>
    <div class="section-title">Personal</div>
    <div class="card" style="padding:20px"><div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div class="field"><label class="label">First name</label><input class="input" data-fn value="${esc(App.user.firstName)}"></div>
      <div class="field"><label class="label">Last name</label><input class="input" data-ln value="${esc(App.user.lastName)}"></div></div>
      <div class="field"><label class="label">Email</label><input class="input" value="${esc(App.user.email)}" disabled></div>
      <div style="display:flex;gap:8px"><button class="btn primary" data-save>Save</button><button class="btn" data-pw>Change password</button></div></div>
    <div class="section-title">Two-factor authentication</div>
    <div class="card" style="padding:20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap"><div class="grow" data-mfa-state style="min-width:220px">…</div><button class="btn" data-mfa></button></div>
    <div class="section-title">API keys</div>
    <p class="muted" style="margin-top:-6px">Use the public API at <span class="mono">/api/v1</span> with the header <span class="mono">X-N8N-API-KEY</span> — compatible with n8n API clients.</p>
    <div data-keys></div><button class="btn" data-newkey style="margin-top:10px">${icon('plus')}Create API key</button>
    ${isOwner ? `<div class="section-title">Users</div><div data-users></div><div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap"><button class="btn primary" data-invite>${icon('plus')}Invite users</button><button class="btn" data-newuser>Add user with password</button></div>` : ''}
    ${isOwner ? `<div class="section-title">Source control (Git)</div>
    <p class="muted" style="margin-top:-6px">Push workflows, credential stubs (no secrets) and variable keys to a Git repository (any server over SSH, or the GitHub / Gitea API), and pull them into another instance.</p>
    <div class="card" style="padding:20px" data-sc>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="field"><label class="label">Provider</label><select class="input" data-p><option value="ssh">Git over SSH (any server)</option><option value="github">GitHub API</option><option value="gitea">Gitea / Forgejo API</option></select></div>
        <div class="field" data-only="ssh"><label class="label">Repository URL</label><input class="input" data-url placeholder="git@github.com:owner/automations.git"></div>
        <div class="field" data-only="api"><label class="label">API URL</label><input class="input" data-api placeholder="https://api.github.com"></div>
        <div class="field" data-only="api"><label class="label">Repository</label><input class="input" data-repo placeholder="owner/automations"></div>
        <div class="field"><label class="label">Branch</label><div class="cred-row"><input class="input" data-branch placeholder="main" list="fw-branches"><datalist id="fw-branches"></datalist>
          <button class="btn icon" type="button" data-loadb title="Load branches">${icon('history')}</button></div></div>
        <div class="field"><label class="label">Folder (optional)</label><input class="input" data-folder placeholder="flowwolf"></div>
        <div class="field" data-only="api"><label class="label">Access token</label><input class="input" type="password" data-token placeholder="unchanged"></div>
      </div>
      <div data-only="ssh" style="margin-bottom:14px"><label class="label">Deploy key (add it to the repository with write access)</label>
        <div class="keybox"><span class="mono" style="flex:1;word-break:break-all" data-pub>No key yet</span><button class="btn sm" type="button" data-copy>Copy</button><button class="btn sm" type="button" data-gen>Generate key</button></div>
        <div class="sub muted" style="margin-top:6px;font-size:12.5px" data-host></div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" data-scsave>Save</button><button class="btn" data-scpush>${icon('upload')}Push</button><button class="btn" data-scpull>${icon('download')}Pull</button></div></div>` : ''}
    <div class="section-title">About</div>
    <div class="card" style="padding:20px;font-size:13.5px;line-height:1.9">Version <b>${esc(App.settings.version)}</b><br>Production webhooks: <span class="mono">${esc(App.settings.webhookUrl)}/…</span><br>
    Test webhooks: <span class="mono">${esc(App.settings.webhookTestUrl)}/…</span><br>Timezone: <b>${esc(App.settings.timezone)}</b></div></div>`);
  shell('settings', page);
  $('[data-save]', page).onclick = async () => {
    try { await api('PATCH', '/me', { firstName: $('[data-fn]', page).value, lastName: $('[data-ln]', page).value }); App.user = await api('GET', '/login'); toast('Saved'); pageSettings(); } catch (e) { fail(e); }
  };
  $('[data-pw]', page).onclick = async () => {
    const body = h(`<div><div class="field"><label class="label">Current password</label><input class="input" type="password" data-c></div><div class="field"><label class="label">New password</label><input class="input" type="password" data-n></div></div>`);
    if (await modal('Change password', body, [{ label: 'Cancel', value: null }, { label: 'Change', cls: 'primary', action: () => api('PATCH', '/me', { currentPassword: $('[data-c]', body).value, newPassword: $('[data-n]', body).value }) }])) toast('Password changed');
  };
  const loadKeys = async () => {
    const keys = await api('GET', '/api-keys').catch(() => []);
    $('[data-keys]', page).innerHTML = keys.length ? `<div class="list">${keys.map((k) => `<div class="row" style="cursor:default" data-id="${k.id}"><span class="mini" style="background:#5b5f6b">${icon('key')}</span>
      <div class="grow"><div class="t">${esc(k.label || 'API key')}</div><div class="sub mono">${esc(k.apiKey)} · created ${ago(k.createdAt)}</div></div><button class="btn ghost sm icon" data-del>${icon('trash')}</button></div>`).join('')}</div>` : '<div class="card empty muted" style="padding:24px">No API keys yet.</div>';
    $('[data-keys]', page).querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => { await api('DELETE', '/api-keys/' + b.closest('.row').dataset.id).catch(fail); loadKeys(); }));
  };
  $('[data-newkey]', page).onclick = async () => {
    const label = await prompt('Create API key', 'Label', 'My integration');
    if (label === null) return;
    const k = await api('POST', '/api-keys', { label }).catch(fail);
    if (!k) return;
    const body = h(`<div><p style="margin-top:0">Copy the key now — it will not be shown again.</p><div class="keybox"><span class="mono" style="flex:1">${esc(k.apiKey)}</span><button class="btn sm" data-c>Copy</button></div></div>`);
    $('[data-c]', body).onclick = () => copyText(k.apiKey);
    modal('API key created', body, [{ label: 'Done', cls: 'primary' }]);
    loadKeys();
  };
  loadKeys();
  const loadMfa = async () => {
    const me = (await api('GET', '/users').catch(() => [])).find((u) => u.id === App.user.id);
    const on = !!(me && me.mfaEnabled);
    $('[data-mfa-state]', page).innerHTML = on ? '<b>On.</b> <span class="muted">Sign-ins need a code from your authenticator app.</span>' : '<b>Off.</b> <span class="muted">Add a code from an authenticator app to every sign-in.</span>';
    $('[data-mfa]', page).textContent = on ? 'Turn off' : 'Set up';
    $('[data-mfa]', page).onclick = on ? async () => {
      const body = h('<div class="field"><label class="label">Code from the app or a recovery code</label><input class="input" autocomplete="one-time-code"></div>');
      const done = await modal('Turn off two-factor authentication', body, [{ label: 'Cancel', value: null }, { label: 'Turn off', cls: 'primary', action: () => { const v = $('input', body).value.trim();
        return api('POST', '/mfa/disable', /^\d{6}$/.test(v) ? { mfaCode: v } : { mfaRecoveryCode: v }); } }]);
      if (done) { toast('Two-factor authentication is off'); loadMfa(); }
    } : async () => {
      const s = await api('GET', '/mfa/qr').catch(fail);
      if (!s) return;
      const body = h(`<div class="mfa-setup"><p style="margin-top:0">1. Scan the code with an authenticator app (or enter the key by hand).</p>
        <div class="mfa-qr">${s.qrSvg}</div><div class="keybox"><span class="mono" style="flex:1;word-break:break-all">${esc(s.secret)}</span><button class="btn sm" data-ck>Copy</button></div>
        <p>2. Keep these recovery codes somewhere safe — each one signs you in once without the app.</p>
        <div class="mono recovery">${s.recoveryCodes.map(esc).join('<br>')}</div><button class="btn sm" data-cr style="margin-top:8px">Copy codes</button>
        <div class="field" style="margin-top:16px"><label class="label">3. Enter the 6-digit code the app shows</label><input class="input" inputmode="numeric" autocomplete="one-time-code" data-code></div></div>`);
      $('[data-ck]', body).onclick = () => copyText(s.secret);
      $('[data-cr]', body).onclick = () => copyText(s.recoveryCodes.join('\n'));
      const done = await modal('Set up two-factor authentication', body, [{ label: 'Cancel', value: null }, { label: 'Turn on', cls: 'primary', action: () => api('POST', '/mfa/enable', { mfaCode: $('[data-code]', body).value.trim() }) }]);
      if (done) { toast('Two-factor authentication is on'); loadMfa(); }
    };
  };
  loadMfa();
  if (isOwner) {
    const sc = $('[data-sc]', page);
    api('GET', '/source-control/preferences').then((p) => {
      $('[data-p]', sc).value = p.provider || 'ssh';
      $('[data-url]', sc).value = p.repositoryUrl || '';
      $('[data-pub]', sc).textContent = p.publicKey || 'No key yet';
      $('[data-host]', sc).innerHTML = p.hostKey ? `Server host key pinned: <span class="mono">SHA256:${esc(p.hostKey)}</span> · <a href="#" data-reset>reset</a>` : 'The server host key is pinned on first contact.';
      const reset = $('[data-reset]', sc);
      if (reset) reset.onclick = async (e) => { e.preventDefault(); await api('PUT', '/source-control/preferences', Object.assign(scBody(), { resetHostKey: true })).catch(fail); toast('Host key reset'); pageSettings(); };
      showProvider();
      $('[data-api]', sc).value = p.apiUrl || '';
      $('[data-repo]', sc).value = p.repository || '';
      $('[data-branch]', sc).value = p.branch || '';
      $('[data-folder]', sc).value = p.folder || '';
      $('[data-token]', sc).placeholder = p.tokenSet ? '•••••••• (unchanged)' : 'Personal access token';
    }).catch(() => {});
    const showProvider = () => sc.querySelectorAll('[data-only]').forEach((el) => (el.style.display = (el.dataset.only === 'ssh') === ($('[data-p]', sc).value === 'ssh') ? '' : 'none'));
    $('[data-p]', sc).onchange = showProvider;
    const scBody = () => ({ provider: $('[data-p]', sc).value, apiUrl: $('[data-api]', sc).value, repository: $('[data-repo]', sc).value, repositoryUrl: $('[data-url]', sc).value,
      branch: $('[data-branch]', sc).value, folder: $('[data-folder]', sc).value, token: $('[data-token]', sc).value });
    $('[data-copy]', sc).onclick = () => copyText($('[data-pub]', sc).textContent);
    $('[data-gen]', sc).onclick = async () => {
      if ($('[data-pub]', sc).textContent.startsWith('ssh-') && !(await confirmBox('Replace the deploy key?', 'The old key stops working; add the new public key to the repository.', 'Generate'))) return;
      try { const r = await api('POST', '/source-control/generate-key-pair'); $('[data-pub]', sc).textContent = r.publicKey; toast('Key generated', 'Add the public key as a deploy key with write access'); } catch (e) { fail(e); }
    };
    $('[data-loadb]', sc).onclick = async () => {
      try {
        const list = await api('GET', '/source-control/branches');
        $('#fw-branches').innerHTML = list.map((b) => `<option value="${esc(b)}">`).join('');
        toast('Branches loaded', list.join(', '));
      } catch (e) { fail(e); }
    };
    $('[data-scsave]', sc).onclick = async () => {
      try {
        await api('PUT', '/source-control/preferences', scBody());
        toast('Source control saved');
      } catch (e) { fail(e); }
    };
    $('[data-scpush]', sc).onclick = async () => {
      const msg = await prompt('Push to Git', 'Commit message', 'Update workflows');
      if (msg === null) return;
      try { const r = await api('POST', '/source-control/push', { message: msg }); toast('Pushed', `${r.workflows} workflows, ${r.credentials} credential stubs, ${r.variables ? 'variables' : 'no variable'} changes`); } catch (e) { fail(e); }
    };
    $('[data-scpull]', sc).onclick = async () => {
      try { const r = await api('POST', '/source-control/pull'); toast('Pulled', `${r.workflowsCreated} new, ${r.workflowsUpdated} updated workflows, ${r.credentialStubs} credential stubs, ${r.variables} variables`); } catch (e) { fail(e); }
    };
    const loadUsers = async () => {
      const users = await api('GET', '/users').catch(() => []);
      const name = (u) => [u.firstName, u.lastName].join(' ').trim();
      $('[data-users]', page).innerHTML = `<div class="list">${users.map((u) => `<div class="row" style="cursor:default" data-id="${u.id}"><div class="avatar">${esc((u.firstName || u.email)[0].toUpperCase())}</div>
        <div class="grow" style="min-width:0"><div class="t">${esc(name(u) || u.email)}${u.id === App.user.id ? ' <span class="muted">(you)</span>' : ''}</div>
          <div class="sub">${esc(u.email)}${u.isPending ? ' · <span class="pill warn">Invited</span>' : ''}${u.mfaEnabled ? ' · 2FA' : ''}</div></div>
        ${u.role === 'owner' || u.id === App.user.id ? `<span class="pill">${esc(u.role)}</span>` : `<select class="input sm" data-role aria-label="Role of ${esc(u.email)}"><option value="member">Member</option><option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Admin</option></select>`}
        ${u.role !== 'owner' && u.id !== App.user.id ? `<button class="btn ghost sm icon" data-more title="More">${icon('more')}</button>` : ''}</div>`).join('')}</div>`;
      $('[data-users]', page).querySelectorAll('[data-role]').forEach((s) => (s.onchange = async () => {
        try { await api('PATCH', `/users/${s.closest('.row').dataset.id}/role`, { newRoleName: 'global:' + s.value }); toast('Role changed'); } catch (e) { fail(e); loadUsers(); }
      }));
      $('[data-users]', page).querySelectorAll('[data-more]').forEach((b) => (b.onclick = (ev) => {
        const u = users.find((x) => x.id === b.closest('.row').dataset.id);
        const r = b.getBoundingClientRect();
        menu(r.left, r.bottom + 4, [
          u.isPending ? { label: 'Copy new invitation link', icon: 'link', action: async () => { const x = await api('POST', `/users/${u.id}/reinvite`).catch(fail); if (x) copyText(x.inviteAcceptUrl); } }
            : { label: 'Copy password reset link', icon: 'key', action: async () => { const x = await api('GET', `/users/${u.id}/password-reset-link`).catch(fail); if (x) copyText(x.link); } },
          '-',
          { label: u.isPending ? 'Withdraw invitation' : 'Delete user', icon: 'trash', danger: true, action: () => deleteUser(u, users) },
        ]);
      }));
    };
    const deleteUser = async (u, users) => {
      const others = users.filter((x) => x.id !== u.id && !x.isPending);
      const body = h(`<div><p style="margin-top:0">${esc(u.email)} loses access immediately.</p>${u.isPending ? '' : `
        <label class="radio"><input type="radio" name="what" value="transfer" checked> Transfer their workflows and credentials to
          <select class="input sm" data-to>${others.map((x) => `<option value="${x.id}" ${x.role === 'owner' ? 'selected' : ''}>${esc([x.firstName, x.lastName].join(' ').trim() || x.email)}</option>`).join('')}</select></label>
        <label class="radio"><input type="radio" name="what" value="delete"> Delete their workflows and credentials</label>`}</div>`);
      const done = await modal(u.isPending ? 'Withdraw invitation?' : 'Delete user?', body, [{ label: 'Cancel', value: null }, { label: u.isPending ? 'Withdraw' : 'Delete user', cls: 'primary', action: () => {
        const transfer = !u.isPending && $('input[name=what]:checked', body).value === 'transfer';
        return api('DELETE', `/users/${u.id}${transfer ? '?transferId=' + $('[data-to]', body).value : ''}`);
      } }]);
      if (done) { toast(u.isPending ? 'Invitation withdrawn' : 'User deleted'); loadUsers(); }
    };
    $('[data-invite]', page).onclick = async () => {
      const body = h(`<div><div class="field"><label class="label">Email addresses</label><textarea class="input" rows="3" data-e placeholder="ada@example.com, linus@example.com"></textarea></div>
        <div class="field"><label class="label">Role</label><select class="input" data-r><option value="member">Member — sees own and shared workflows</option><option value="admin">Admin — manages users and sees everything</option></select></div></div>`);
      const res = await modal('Invite users', body, [{ label: 'Cancel', value: null }, { label: 'Invite', cls: 'primary', action: () => api('POST', '/invitations',
        $('[data-e]', body).value.split(/[\s,;]+/).filter(Boolean).map((email) => ({ email, role: 'global:' + $('[data-r]', body).value }))) }]);
      if (!res) return;
      loadUsers();
      const list = h(`<div><p style="margin-top:0">${res.some((x) => x.user.emailSent) ? 'Invitations were emailed.' : 'Email is not set up — send these links yourself. Each works once and for 7 days.'}</p>
        ${res.map((x) => `<div class="keybox" style="margin-bottom:8px"><span style="flex:1;min-width:0"><b>${esc(x.user.email)}</b><br><span class="${x.error ? 'err-text' : 'mono sub'}" style="word-break:break-all">${esc(x.error || x.user.inviteAcceptUrl)}</span></span>
          ${x.error ? '' : `<button class="btn sm" data-copy="${esc(x.user.inviteAcceptUrl)}">Copy</button>`}</div>`).join('')}</div>`);
      list.querySelectorAll('[data-copy]').forEach((b) => (b.onclick = () => copyText(b.dataset.copy)));
      modal('Invitations', list, [{ label: 'Done', cls: 'primary' }]);
    };
    $('[data-newuser]', page).onclick = async () => {
      const body = h(`<div><div style="display:grid;grid-template-columns:1fr 1fr;gap:12px"><div class="field"><label class="label">First name</label><input class="input" data-fn></div><div class="field"><label class="label">Last name</label><input class="input" data-ln></div></div>
        <div class="field"><label class="label">Email</label><input class="input" type="email" data-e></div><div class="field"><label class="label">Initial password</label><input class="input" type="password" data-p></div>
        <div class="field"><label class="label">Role</label><select class="input" data-r><option value="member">Member</option><option value="admin">Admin</option></select></div></div>`);
      if (await modal('Add user', body, [{ label: 'Cancel', value: null }, { label: 'Add user', cls: 'primary', action: () => api('POST', '/users', { firstName: $('[data-fn]', body).value, lastName: $('[data-ln]', body).value, email: $('[data-e]', body).value, password: $('[data-p]', body).value, role: $('[data-r]', body).value }) }])) loadUsers();
    };
    loadUsers();
  }
}

document.addEventListener('DOMContentLoaded', () => boot().catch((e) => { document.body.innerHTML = `<div class="auth"><div class="auth-card"><h1>Cannot reach server</h1><p class="muted">${esc(e.message)}</p></div></div>`; }));
