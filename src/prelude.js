// Runtime prelude for expressions and the Code node. Mirrors the n8n expression API.
'use strict';
var __in = [], __idx = 0, __ec = Object.create(null), __nc = Object.create(null), __static = {};
var $json = {}, $binary = {}, $itemIndex = 0, $runIndex = 0, $prevNode = {}, $workflow = {}, $execution = {}, $parameter = {}, $nodeName = '';
var __res, __err, __customData = {};

// ---- Luxon-compatible DateTime with time zones ----
// The instant is kept in _d (a JS Date); _z is the IANA zone used for fields, formatting and calendar
// math. The default zone is the instance's GENERIC_TIMEZONE, as Luxon's default zone in n8n.
const __zoneDefault = () => (typeof __tzDefault === 'function' ? __tzDefault() : 'UTC');
const __zoneName = (z) => (!z || z === 'local' || z === 'system' || z === 'default') ? __zoneDefault() : (String(z).toLowerCase() === 'utc' ? 'UTC' : String(z));
const __offsetAt = (zone, ms) => (typeof __tzOffset === 'function' ? __tzOffset(zone, ms) : 0);
class DateTime {
  constructor(d, zone) { this._d = d instanceof Date ? new Date(d.getTime()) : new Date(d); this._z = __zoneName(zone); }
  // Wall-clock milliseconds (zone fields read as UTC) -> instant in that zone, DST-safe.
  static _fromWall(wall, zone) {
    const z = __zoneName(zone);
    let ms = wall - __offsetAt(z, wall) * 6e4;
    ms = wall - __offsetAt(z, ms) * 6e4;
    return new DateTime(new Date(ms), z);
  }
  static now() { return new DateTime(new Date()); }
  static local(...a) { return a.length ? DateTime._fromParts(a) : DateTime.now(); }
  static utc(...a) { return a.length ? DateTime._fromParts(a, 'UTC') : new DateTime(new Date(), 'UTC'); }
  static _fromParts(a, zone) { return DateTime._fromWall(Date.UTC(a[0], (a[1] || 1) - 1, a[2] || 1, a[3] || 0, a[4] || 0, a[5] || 0, a[6] || 0), zone); }
  static fromObject(o, opts) {
    const n = Object.assign({ year: new Date().getUTCFullYear(), month: 1, day: 1, hour: 0, minute: 0, second: 0, millisecond: 0 }, o || {});
    return DateTime._fromWall(Date.UTC(n.year, n.month - 1, n.day, n.hour, n.minute, n.second, n.millisecond), opts && opts.zone);
  }
  static fromISO(s, opts) {
    s = String(s).trim();
    const zone = opts && opts.zone;
    // With an offset or Z the instant is fixed; without one the wall time is read in the zone.
    if (/(?:[zZ]|[+-]\d\d(?::?\d\d)?)$/.test(s) && /T/.test(s)) return new DateTime(new Date(s), zone);
    const m = s.match(/^(\d{4})-(\d\d)(?:-(\d\d))?(?:[T ](\d\d)(?::(\d\d)(?::(\d\d)(?:[.,](\d+))?)?)?)?$/);
    if (!m) return new DateTime(new Date(NaN), zone);
    const msPart = Number(((m[7] || '0') + '00').slice(0, 3));
    return DateTime._fromWall(Date.UTC(+m[1], +m[2] - 1, +(m[3] || 1), +(m[4] || 0), +(m[5] || 0), +(m[6] || 0), msPart), zone);
  }
  static fromSQL(s, opts) { return DateTime.fromISO(String(s).trim().replace(' ', 'T'), opts); }
  static fromMillis(n, opts) { return new DateTime(new Date(Number(n)), opts && opts.zone); }
  static fromSeconds(n, opts) { return new DateTime(new Date(Number(n) * 1000), opts && opts.zone); }
  static fromJSDate(d, opts) { return new DateTime(new Date(d), opts && opts.zone); }
  static fromFormat(s, f, opts) { const d = DateTime.fromISO(s, opts); return d.isValid ? d : new DateTime(new Date(s), opts && opts.zone); }
  static fromHTTP(s, opts) { return new DateTime(new Date(s), opts && opts.zone); }
  static fromRFC2822(s, opts) { return new DateTime(new Date(s), opts && opts.zone); }
  static isDateTime(o) { return o instanceof DateTime; }
  get isValid() { return !isNaN(this._d.getTime()); }
  get zoneName() { return this._z; }
  get zone() { return { name: this._z, type: 'iana', isValid: true }; }
  get offset() { return __offsetAt(this._z, this._d.getTime()); }
  get isOffsetFixed() { return this._z === 'UTC'; }
  get _w() { return new Date(this._d.getTime() + this.offset * 6e4); }
  get year() { return this._w.getUTCFullYear(); }
  get month() { return this._w.getUTCMonth() + 1; }
  get day() { return this._w.getUTCDate(); }
  get hour() { return this._w.getUTCHours(); }
  get minute() { return this._w.getUTCMinutes(); }
  get second() { return this._w.getUTCSeconds(); }
  get millisecond() { return this._w.getUTCMilliseconds(); }
  get weekday() { return ((this._w.getUTCDay() + 6) % 7) + 1; }
  get weekdayLong() { return DateTime._days[this._w.getUTCDay()]; }
  get weekdayShort() { return DateTime._days[this._w.getUTCDay()].slice(0, 3); }
  get monthLong() { return DateTime._months[this._w.getUTCMonth()]; }
  get monthShort() { return DateTime._months[this._w.getUTCMonth()].slice(0, 3); }
  get quarter() { return Math.floor(this._w.getUTCMonth() / 3) + 1; }
  get daysInMonth() { const w = this._w; return new Date(Date.UTC(w.getUTCFullYear(), w.getUTCMonth() + 1, 0)).getUTCDate(); }
  get ordinal() { const w = this._w; return Math.round((Date.UTC(w.getUTCFullYear(), w.getUTCMonth(), w.getUTCDate()) - Date.UTC(w.getUTCFullYear(), 0, 1)) / 864e5) + 1; }
  get weekNumber() { const w = this._w; const t = new Date(Date.UTC(w.getUTCFullYear(), w.getUTCMonth(), w.getUTCDate())); t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
    return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7); }
  get ts() { return this._d.getTime(); }
  _offsetStr(colon = true) { const o = this.offset, p = (n) => String(n).padStart(2, '0');
    return (o >= 0 ? '+' : '-') + p(Math.floor(Math.abs(o) / 60)) + (colon ? ':' : '') + p(Math.abs(o) % 60); }
  toISO() { if (!this.isValid) return null; return this.toFormat("yyyy-MM-dd'T'HH:mm:ss.SSS") + (this._z === 'UTC' ? 'Z' : this._offsetStr()); }
  toISODate() { return this.isValid ? this.toFormat('yyyy-MM-dd') : null; }
  toISOTime() { return this.isValid ? this.toFormat('HH:mm:ss.SSS') + (this._z === 'UTC' ? 'Z' : this._offsetStr()) : null; }
  toSQL() { return this.toFormat('yyyy-MM-dd HH:mm:ss.SSS ') + this._offsetStr(); }
  toSQLDate() { return this.toFormat('yyyy-MM-dd'); }
  toMillis() { return this._d.getTime(); }
  toSeconds() { return this._d.getTime() / 1000; }
  toUnixInteger() { return Math.floor(this._d.getTime() / 1000); }
  toJSDate() { return new Date(this._d); }
  toJSON() { return this.toISO(); }
  toString() { return this.toISO(); }
  toObject() { return { year: this.year, month: this.month, day: this.day, hour: this.hour, minute: this.minute, second: this.second, millisecond: this.millisecond }; }
  toLocaleString(l, o) { try { return this._d.toLocaleString(l, Object.assign({ timeZone: this._z }, o || {})); } catch (e) { return this.toFormat('dd.MM.yyyy, HH:mm:ss'); } }
  valueOf() { return this._d.getTime(); }
  setZone(z, opts) {
    const zone = __zoneName(z);
    // keepLocalTime: same wall clock, different instant
    if (opts && opts.keepLocalTime) return DateTime._fromWall(this._w.getTime(), zone);
    return new DateTime(this._d, zone);
  }
  setLocale() { return this; }
  reconfigure() { return this; }
  toUTC() { return this.setZone('UTC'); }
  toLocal() { return this.setZone(__zoneDefault()); }
  set(o) {
    const w = this._w;
    const m = { year: 'FullYear', month: 'Month', day: 'Date', hour: 'Hours', minute: 'Minutes', second: 'Seconds', millisecond: 'Milliseconds' };
    for (const [k, v] of Object.entries(o || {})) { if (m[k]) w['setUTC' + m[k]](k === 'month' ? v - 1 : v); }
    return DateTime._fromWall(w.getTime(), this._z);
  }
  plus(o) { return this._add(o, 1); }
  minus(o) { return this._add(o, -1); }
  _add(o, s) {
    if (typeof o === 'number') o = { milliseconds: o };
    if (o && typeof o.toObject === 'function' && !(o instanceof DateTime)) o = o.toObject();
    // Calendar units move the wall clock in the zone (DST-safe); time units move the instant.
    const w = this._w; let cal = false, time = 0;
    for (const [k, v] of Object.entries(o || {})) {
      const n = Number(v) * s;
      switch (k.replace(/s$/, '')) {
        case 'year': w.setUTCFullYear(w.getUTCFullYear() + n); cal = true; break;
        case 'quarter': w.setUTCMonth(w.getUTCMonth() + 3 * n); cal = true; break;
        case 'month': w.setUTCMonth(w.getUTCMonth() + n); cal = true; break;
        case 'week': w.setUTCDate(w.getUTCDate() + 7 * n); cal = true; break;
        case 'day': w.setUTCDate(w.getUTCDate() + n); cal = true; break;
        case 'hour': time += n * 36e5; break;
        case 'minute': time += n * 6e4; break;
        case 'second': time += n * 1e3; break;
        case 'millisecond': time += n; break;
      }
    }
    const base = cal ? DateTime._fromWall(w.getTime(), this._z) : this;
    return new DateTime(new Date(base._d.getTime() + time), this._z);
  }
  startOf(u) {
    u = String(u).replace(/s$/, '');
    const w = this._w;
    if (u === 'year') w.setUTCMonth(0, 1);
    if (u === 'quarter') w.setUTCMonth(Math.floor(w.getUTCMonth() / 3) * 3, 1);
    if (u === 'month') w.setUTCDate(1);
    if (u === 'week') w.setUTCDate(w.getUTCDate() - ((w.getUTCDay() + 6) % 7));
    if (['year', 'quarter', 'month', 'week', 'day'].includes(u)) w.setUTCHours(0, 0, 0, 0);
    if (u === 'hour') w.setUTCMinutes(0, 0, 0);
    if (u === 'minute') w.setUTCSeconds(0, 0);
    if (u === 'second') w.setUTCMilliseconds(0);
    return DateTime._fromWall(w.getTime(), this._z);
  }
  endOf(u) { return this.startOf(u).plus({ [String(u).replace(/s$/, '')]: 1 }).minus({ milliseconds: 1 }); }
  diff(other, unit) {
    const ms = this.toMillis() - Number(other.valueOf());
    const f = { milliseconds: 1, seconds: 1e3, minutes: 6e4, hours: 36e5, days: 864e5, weeks: 6048e5, months: 2592e6, quarters: 7776e6, years: 31536e6 };
    const dur = { as: (u) => ms / f[u.replace(/s?$/, 's')], toMillis: () => ms };
    for (const u of [].concat(unit || 'milliseconds')) dur[u] = ms / f[u.replace(/s?$/, 's')];
    return dur;
  }
  diffNow(unit) { return this.diff(DateTime.now(), unit); }
  diffToNow(unit) { return DateTime.now().diff(this, unit); }
  equals(o) { return o instanceof DateTime && this.toMillis() === o.toMillis() && this._z === o._z; }
  hasSame(o, u) { return this.startOf(u).toMillis() === new DateTime(new Date(Number(o.valueOf())), this._z).startOf(u).toMillis(); }
  isBetween(a, b) { const t = this.toMillis(); return t >= Number(a.valueOf()) && t <= Number(b.valueOf()); }
  toFormat(f) {
    if (!this.isValid) return 'Invalid DateTime';
    const d = this._w, p = (n, l = 2) => String(n).padStart(l, '0');
    return String(f).replace(/'([^']*)'|yyyy|yy|MMMM|MMM|MM|M|dd|d|HH|H|hh|h|mm|m|ss|s|SSS|a|EEEE|EEE|ZZZ|ZZ|Z|z|kkkk|WW|W|ooo|o|q/g, (t, lit) => {
      if (lit !== undefined) return lit;
      switch (t) {
        case 'yyyy': case 'kkkk': return d.getUTCFullYear(); case 'yy': return p(d.getUTCFullYear() % 100);
        case 'MMMM': return DateTime._months[d.getUTCMonth()]; case 'MMM': return DateTime._months[d.getUTCMonth()].slice(0, 3);
        case 'MM': return p(d.getUTCMonth() + 1); case 'M': return d.getUTCMonth() + 1;
        case 'dd': return p(d.getUTCDate()); case 'd': return d.getUTCDate();
        case 'HH': return p(d.getUTCHours()); case 'H': return d.getUTCHours();
        case 'hh': return p(d.getUTCHours() % 12 || 12); case 'h': return d.getUTCHours() % 12 || 12;
        case 'mm': return p(d.getUTCMinutes()); case 'm': return d.getUTCMinutes();
        case 'ss': return p(d.getUTCSeconds()); case 's': return d.getUTCSeconds();
        case 'SSS': return p(d.getUTCMilliseconds(), 3); case 'a': return d.getUTCHours() < 12 ? 'AM' : 'PM';
        case 'EEEE': return DateTime._days[d.getUTCDay()]; case 'EEE': return DateTime._days[d.getUTCDay()].slice(0, 3);
        case 'WW': return p(this.weekNumber); case 'W': return this.weekNumber;
        case 'ooo': return p(this.ordinal, 3); case 'o': return this.ordinal;
        case 'q': return this.quarter;
        case 'z': return this._z;
        case 'ZZZ': return this._offsetStr(false);
        case 'ZZ': return this._offsetStr(true);
        default: { const o = this.offset; return (o >= 0 ? '+' : '-') + Math.floor(Math.abs(o) / 60) + (o % 60 ? ':' + p(Math.abs(o) % 60) : ''); }
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
