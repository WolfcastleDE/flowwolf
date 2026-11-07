// Runtime prelude for expressions and the Code node. Mirrors the n8n expression API.
'use strict';
var __in = [], __idx = 0, __ec = Object.create(null), __nc = Object.create(null), __static = {};
var $json = {}, $binary = {}, $itemIndex = 0, $runIndex = 0, $prevNode = {}, $workflow = {}, $execution = {}, $parameter = {}, $nodeName = '';
var __res, __err, __customData = {};

// ---- minimal Luxon-compatible DateTime ----
class DateTime {
  constructor(d) { this._d = d; }
  static now() { return new DateTime(new Date()); }
  static local() { return DateTime.now(); }
  static utc() { return DateTime.now(); }
  static fromISO(s) { return new DateTime(new Date(s)); }
  static fromSQL(s) { return new DateTime(new Date(String(s).replace(' ', 'T'))); }
  static fromMillis(n) { return new DateTime(new Date(n)); }
  static fromSeconds(n) { return new DateTime(new Date(n * 1000)); }
  static fromJSDate(d) { return new DateTime(new Date(d)); }
  static fromFormat(s) { return new DateTime(new Date(s)); }
  get isValid() { return !isNaN(this._d.getTime()); }
  get year() { return this._d.getFullYear(); }
  get month() { return this._d.getMonth() + 1; }
  get day() { return this._d.getDate(); }
  get hour() { return this._d.getHours(); }
  get minute() { return this._d.getMinutes(); }
  get second() { return this._d.getSeconds(); }
  get millisecond() { return this._d.getMilliseconds(); }
  get weekday() { return ((this._d.getDay() + 6) % 7) + 1; }
  get weekdayLong() { return DateTime._days[this._d.getDay()]; }
  get monthLong() { return DateTime._months[this._d.getMonth()]; }
  get ts() { return this._d.getTime(); }
  toISO() { return this._d.toISOString(); }
  toISODate() { return this.toFormat('yyyy-MM-dd'); }
  toSQL() { return this.toFormat('yyyy-MM-dd HH:mm:ss'); }
  toMillis() { return this._d.getTime(); }
  toSeconds() { return Math.floor(this._d.getTime() / 1000); }
  toUnixInteger() { return this.toSeconds(); }
  toJSDate() { return new Date(this._d); }
  toJSON() { return this.toISO(); }
  toString() { return this.toISO(); }
  toLocaleString(l, o) { return this._d.toLocaleString(l, o); }
  valueOf() { return this._d.getTime(); }
  setZone() { return this; }
  setLocale() { return this; }
  toUTC() { return this; }
  toLocal() { return this; }
  set(o) { const d = new Date(this._d); const m = { year: 'FullYear', month: 'Month', day: 'Date', hour: 'Hours', minute: 'Minutes', second: 'Seconds', millisecond: 'Milliseconds' };
    for (const [k, v] of Object.entries(o)) { if (m[k]) d['set' + m[k]](k === 'month' ? v - 1 : v); } return new DateTime(d); }
  plus(o) { return this._add(o, 1); }
  minus(o) { return this._add(o, -1); }
  _add(o, s) {
    if (typeof o === 'number') o = { milliseconds: o };
    const d = new Date(this._d);
    for (const [k, v] of Object.entries(o)) {
      const n = v * s;
      switch (k.replace(/s$/, '')) {
        case 'year': d.setFullYear(d.getFullYear() + n); break;
        case 'quarter': d.setMonth(d.getMonth() + 3 * n); break;
        case 'month': d.setMonth(d.getMonth() + n); break;
        case 'week': d.setDate(d.getDate() + 7 * n); break;
        case 'day': d.setDate(d.getDate() + n); break;
        case 'hour': d.setHours(d.getHours() + n); break;
        case 'minute': d.setMinutes(d.getMinutes() + n); break;
        case 'second': d.setSeconds(d.getSeconds() + n); break;
        case 'millisecond': d.setMilliseconds(d.getMilliseconds() + n); break;
      }
    }
    return new DateTime(d);
  }
  startOf(u) {
    const d = new Date(this._d);
    if (u === 'year') d.setMonth(0, 1);
    if (u === 'month') d.setDate(1);
    if (u === 'week') d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    if (['year', 'month', 'week', 'day'].includes(u)) d.setHours(0, 0, 0, 0);
    if (u === 'hour') d.setMinutes(0, 0, 0);
    if (u === 'minute') d.setSeconds(0, 0);
    if (u === 'second') d.setMilliseconds(0);
    return new DateTime(d);
  }
  endOf(u) { return this.startOf(u).plus({ [u]: 1 }).minus({ milliseconds: 1 }); }
  diff(other, unit) {
    const ms = this.toMillis() - new DateTime(new Date(other.valueOf())).toMillis();
    const f = { milliseconds: 1, seconds: 1e3, minutes: 6e4, hours: 36e5, days: 864e5, weeks: 6048e5, months: 2592e6, years: 31536e6 };
    const dur = { as: (u) => ms / f[u.replace(/s?$/, 's')], toMillis: () => ms };
    for (const u of [].concat(unit || 'milliseconds')) dur[u] = ms / f[u.replace(/s?$/, 's')];
    return dur;
  }
  diffToNow(unit) { return DateTime.now().diff(this, unit); }
  equals(o) { return this.toMillis() === o.valueOf(); }
  hasSame(o, u) { return this.startOf(u).toMillis() === new DateTime(new Date(o.valueOf())).startOf(u).toMillis(); }
  isBetween(a, b) { const t = this.toMillis(); return t >= a.valueOf() && t <= b.valueOf(); }
  toFormat(f) {
    const d = this._d, p = (n, l = 2) => String(n).padStart(l, '0');
    return f.replace(/'([^']*)'|yyyy|yy|MMMM|MMM|MM|M|dd|d|HH|H|hh|h|mm|m|ss|s|SSS|a|EEEE|EEE|ZZ|Z/g, (t, lit) => {
      if (lit !== undefined) return lit;
      switch (t) {
        case 'yyyy': return d.getFullYear(); case 'yy': return p(d.getFullYear() % 100);
        case 'MMMM': return DateTime._months[d.getMonth()]; case 'MMM': return DateTime._months[d.getMonth()].slice(0, 3);
        case 'MM': return p(d.getMonth() + 1); case 'M': return d.getMonth() + 1;
        case 'dd': return p(d.getDate()); case 'd': return d.getDate();
        case 'HH': return p(d.getHours()); case 'H': return d.getHours();
        case 'hh': return p(d.getHours() % 12 || 12); case 'h': return d.getHours() % 12 || 12;
        case 'mm': return p(d.getMinutes()); case 'm': return d.getMinutes();
        case 'ss': return p(d.getSeconds()); case 's': return d.getSeconds();
        case 'SSS': return p(d.getMilliseconds(), 3); case 'a': return d.getHours() < 12 ? 'AM' : 'PM';
        case 'EEEE': return DateTime._days[d.getDay()]; case 'EEE': return DateTime._days[d.getDay()].slice(0, 3);
        default: { const o = -d.getTimezoneOffset(); return (o >= 0 ? '+' : '-') + p(Math.floor(Math.abs(o) / 60)) + (t === 'ZZ' ? ':' : '') + p(Math.abs(o) % 60); }
      }
    });
  }
}
DateTime._days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
DateTime._months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
globalThis.DateTime = DateTime;

// ---- data access ----
function __setInput(s, c) {
  __in = JSON.parse(s);
  const x = JSON.parse(c);
  $runIndex = x.runIndex || 0; $prevNode = x.prevNode || {}; $parameter = x.parameters || {};
  $workflow = x.workflow || {}; $nodeName = x.node || '';
  $execution = Object.assign({}, x.execution || {}, { customData: { set: (k, v) => { __customData[k] = String(v); }, get: (k) => __customData[k], setAll: (o) => Object.assign(__customData, o), getAll: () => __customData } });
  __nc = Object.create(null);
  __setIndex(0);
  return JSON.stringify({ v: true });
}
function __setIndex(i) {
  __idx = Number(i) || 0;
  const it = __in[__idx] || { json: {} };
  $json = it.json; $binary = it.binary || {}; $itemIndex = __idx;
}
function __norm(v) {
  if (v instanceof DateTime) return v.toISO();
  if (v === undefined || typeof v === 'function') return null;
  if (typeof v === 'number' && !isFinite(v)) return null;
  return v;
}
function __evs(code, i) {
  try {
    __setIndex(i);
    let f = __ec[code];
    if (!f) f = __ec[code] = new Function('return (' + code + '\n);');
    return JSON.stringify({ v: __norm(f()) });
  } catch (e) {
    return JSON.stringify({ e: String((e && e.message) || e) });
  }
}
function __nodeData(name, branch, run) {
  const k = name + '|' + branch + '|' + run;
  let d = __nc[k];
  if (d === undefined) {
    const s = __nodeOut(name, branch, run);
    if (s === '!!') throw new Error(`Referenced node doesn't exist: "${name}"`);
    if (s === '!') throw new Error(`Node "${name}" hasn't been executed yet`);
    d = __nc[k] = JSON.parse(s);
  }
  return d;
}
function $(name) {
  return {
    all(branch = 0, run = -1) { return __nodeData(name, branch, run); },
    first(branch = 0, run = -1) { return this.all(branch, run)[0]; },
    last(branch = 0, run = -1) { const a = this.all(branch, run); return a[a.length - 1]; },
    get item() { const a = this.all(); return a[Math.min(__idx, a.length - 1)]; },
    itemMatching(i) { const a = this.all(); return a[Math.min(i, a.length - 1)]; },
    pairedItem(i) { return this.itemMatching(i === undefined ? __idx : i); },
    get params() { return JSON.parse(__nodeParams(name)); },
    get isExecuted() { return __nodeOut(name, 0, -1).charAt(0) !== '!'; },
    get json() { return (this.item || {}).json; },
  };
}
var $input = {
  all() { return __in; }, first() { return __in[0]; }, last() { return __in[__in.length - 1]; },
  get item() { return __in[__idx]; }, get params() { return $parameter; },
};
var $node = new Proxy({}, { get(_, name) { const n = $(name); return { get json() { return (n.item || {}).json; }, get binary() { return (n.item || {}).binary || {}; }, get parameter() { return n.params; }, runIndex: 0, get data() { return n.item; } }; } });
function $items(name, branch = 0, run = -1) { return name === undefined ? __in : $(name).all(branch, run); }
function $item(i) { return { $node: new Proxy({}, { get(_, name) { const n = $(name); return { get json() { return (n.itemMatching(i) || {}).json; } }; } }) }; }
Object.defineProperty(globalThis, '$env', { get() { return JSON.parse(__env()); } });
Object.defineProperty(globalThis, '$vars', { get() { return JSON.parse(__vars()); } });
Object.defineProperty(globalThis, '$now', { get() { return DateTime.now(); } });
Object.defineProperty(globalThis, '$today', { get() { return DateTime.now().startOf('day'); } });
function $if(c, a, b) { return c ? a : b; }
function __empty(v) { return v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length) || (typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length); }
function $ifEmpty(v, d) { return __empty(v) ? d : v; }
function $min(...a) { return Math.min(...a); }
function $max(...a) { return Math.max(...a); }
function $getWorkflowStaticData(t) { return t === 'node' ? (__static['node:' + $nodeName] ||= {}) : (__static.global ||= {}); }
function __setStatic(s) { __static = JSON.parse(s) || {}; return JSON.stringify({ v: true }); }
function __getStatic() { return JSON.stringify({ v: __static }); }
var console = { log: (...a) => __log(a.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join(' ')) };
console.info = console.warn = console.error = console.debug = console.log;
globalThis.btoa = (s) => __b64e(String(s));
globalThis.atob = (s) => __b64d(String(s));

// ---- Code node ----
const __AF = (async function () {}).constructor;
function __startCode(code, each) {
  __res = undefined; __err = undefined;
  const run = async () => {
    if (!each) return new __AF('items', '$items', code)(__in, $items);
    const f = new __AF('item', code), out = [];
    for (let i = 0; i < __in.length; i++) {
      __setIndex(i);
      const r = await f(__in[i]);
      if (r !== undefined && r !== null) out.push(...(Array.isArray(r) ? r : [r]));
    }
    return out;
  };
  run().then((r) => { __res = r; }, (e) => { __err = String((e && e.message) || e); });
}
function __codeResult() {
  if (__err !== undefined) return JSON.stringify({ e: __err });
  if (__res === undefined) return JSON.stringify({ e: 'Code doesn\'t return items properly. Please return an array of objects.' });
  return JSON.stringify({ v: __res }, (k, v) => (v instanceof DateTime ? v.toISO() : v));
}

// ---- n8n data transformation helpers ----
const __ext = (p, o) => { for (const k in o) Object.defineProperty(p, k, { value: o[k], writable: true, configurable: true }); };
__ext(String.prototype, {
  isEmpty() { return this.length === 0; }, isNotEmpty() { return this.length > 0; },
  toNumber() { return Number(this); }, toInt() { return parseInt(this, 10); }, toFloat() { return parseFloat(this); },
  toBoolean() { return !['false', '0', '', 'no', 'n', 'off'].includes(this.toLowerCase().trim()); },
  toTitleCase() { return this.toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase()); },
  toSentenceCase() { return this.charAt(0).toUpperCase() + this.slice(1).toLowerCase(); },
  toSnakeCase() { return this.replace(/([a-z])([A-Z])/g, '$1_$2').replace(/[\s-]+/g, '_').toLowerCase(); },
  toCamelCase() { return this.toLowerCase().replace(/[\s_-]+(.)/g, (_, c) => c.toUpperCase()); },
  urlEncode() { return encodeURIComponent(this); }, urlDecode() { return decodeURIComponent(this); },
  extractEmail() { const m = this.match(/[\w.+-]+@[\w-]+\.[\w.-]+/); return m ? m[0] : undefined; },
  extractUrl() { const m = this.match(/https?:\/\/[^\s"'<>]+/); return m ? m[0] : undefined; },
  extractDomain() { const m = this.match(/(?:https?:\/\/)?(?:[^@\/\n]+@)?([^:\/?\n]+)/); return m ? m[1] : undefined; },
  isEmail() { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(this); },
  isUrl() { return /^https?:\/\/[^\s]+$/.test(this); },
  isNumeric() { return this.trim() !== '' && !isNaN(this); },
  removeTags() { return this.replace(/<[^>]*>/g, ''); },
  removeMarkdown() { return this.replace(/[*_`#>~]/g, ''); },
  toDateTime() { return DateTime.fromISO(this); }, toDate() { return new Date(this); },
  quote(q = '"') { return q + this.split(q).join('\\' + q) + q; },
  base64Encode() { return __b64e(String(this)); }, base64Decode() { return __b64d(String(this)); },
  parseJson() { return JSON.parse(this); },
  replaceSpecialChars() { return this.normalize('NFD').replace(/[̀-ͯ]/g, ''); },
});
__ext(Array.prototype, {
  first() { return this[0]; }, last() { return this[this.length - 1]; },
  isEmpty() { return !this.length; }, isNotEmpty() { return this.length > 0; },
  sum() { return this.reduce((a, b) => a + Number(b), 0); },
  average() { return this.length ? this.sum() / this.length : 0; },
  min() { return Math.min(...this); }, max() { return Math.max(...this); },
  unique(...k) { const s = new Set(); return this.filter((x) => { const key = JSON.stringify(k.length ? k.map((f) => x?.[f]) : x); return s.has(key) ? false : s.add(key); }); },
  removeDuplicates(...k) { return this.unique(...k); },
  compact() { return this.filter((v) => v !== null && v !== undefined && v !== ''); },
  pluck(...k) { return this.map((o) => (k.length === 1 ? o?.[k[0]] : Object.fromEntries(k.map((x) => [x, o?.[x]])))); },
  chunk(n) { const r = []; for (let i = 0; i < this.length; i += n) r.push(this.slice(i, i + n)); return r; },
  randomItem() { return this[Math.floor(Math.random() * this.length)]; },
  difference(o) { return this.filter((x) => !o.includes(x)); },
  intersection(o) { return this.filter((x) => o.includes(x)); },
  union(o) { return [...new Set([...this, ...o])]; },
  smartJoin(k, v) { return Object.fromEntries(this.map((o) => [o[k], o[v]])); },
  toJsonString() { return JSON.stringify(this); },
});
__ext(Number.prototype, {
  round(d = 0) { const f = 10 ** d; return Math.round(this * f) / f; },
  floor() { return Math.floor(this); }, ceil() { return Math.ceil(this); }, abs() { return Math.abs(this); },
  isEven() { return this % 2 === 0; }, isOdd() { return Math.abs(this % 2) === 1; },
  toBoolean() { return Number(this) !== 0; }, format(l, o) { return this.toLocaleString(l, o); },
  toDateTime(u = 'ms') { return u === 's' ? DateTime.fromSeconds(Number(this)) : DateTime.fromMillis(Number(this)); },
});
__ext(Object.prototype, {
  isEmpty() { return Object.keys(this).length === 0; }, isNotEmpty() { return Object.keys(this).length > 0; },
  hasField(k) { return k in this; },
  removeField(k) { const o = { ...this }; delete o[k]; return o; },
  compact() { return Object.fromEntries(Object.entries(this).filter(([, v]) => v !== null && v !== undefined && v !== '')); },
  toJsonString() { return JSON.stringify(this); },
  urlEncode() { return Object.entries(this).map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&'); },
  keepFieldsContaining(s) { return Object.fromEntries(Object.entries(this).filter(([, v]) => String(v).includes(s))); },
});
