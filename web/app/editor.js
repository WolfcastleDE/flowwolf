// Flowwolf editor: parameter forms, canvas, node creator and node details view (NDV).
'use strict';

// =====================================================================
// Parameter forms (n8n INodeProperties compatible)
// =====================================================================
const OPS = (() => {
  const base = [['exists', 'exists'], ['notExists', 'does not exist'], ['empty', 'is empty'], ['notEmpty', 'is not empty']];
  const eq = [['equals', 'is equal to'], ['notEquals', 'is not equal to']];
  return {
    string: [...base, ...eq, ['contains', 'contains'], ['notContains', 'does not contain'], ['startsWith', 'starts with'], ['notStartsWith', 'does not start with'],
      ['endsWith', 'ends with'], ['notEndsWith', 'does not end with'], ['regex', 'matches regex'], ['notRegex', 'does not match regex']],
    number: [...base, ...eq, ['gt', 'is greater than'], ['lt', 'is less than'], ['gte', 'is greater than or equal to'], ['lte', 'is less than or equal to']],
    dateTime: [...base, ...eq, ['after', 'is after'], ['before', 'is before'], ['afterOrEquals', 'is after or equal to'], ['beforeOrEquals', 'is before or equal to']],
    boolean: [...base, ['true', 'is true'], ['false', 'is false'], ...eq],
    array: [...base, ['contains', 'contains'], ['notContains', 'does not contain'], ['lengthEquals', 'length equal to'], ['lengthNotEquals', 'length not equal to'],
      ['lengthGt', 'length greater than'], ['lengthLt', 'length less than'], ['lengthGte', 'length greater than or equal to'], ['lengthLte', 'length less than or equal to']],
    object: base,
  };
})();
const UNARY = new Set(['exists', 'notExists', 'empty', 'notEmpty', 'true', 'false']);
const isExpr = (v) => typeof v === 'string' && v.startsWith('=');

const Params = {
  // Defaults of the parameters that are visible with the defaults chosen so far
  // (a name such as "operation" may exist once per resource).
  defaults(props) {
    const o = {};
    for (const p of props) {
      if (p.type === 'notice' || o[p.name] !== undefined || !Params.visible(p, o, props, o)) continue;
      o[p.name] = clone(p.default);
    }
    return o;
  },
  visible(p, obj, siblings, root, rootProps = siblings) {
    const d = p.displayOptions;
    if (!d) return true;
    const get = (k) => {
      if (k.startsWith('/')) {
        const n = k.slice(1);
        return root[n] !== undefined ? root[n] : rootProps.find((x) => x.name === n)?.default;
      }
      return obj[k] !== undefined ? obj[k] : siblings.find((x) => x.name === k)?.default;
    };
    const eq = (a, b) => String(a) === String(b);
    for (const [k, vals] of Object.entries(d.show || {})) if (!vals.some((x) => eq(x, get(k)))) return false;
    for (const [k, vals] of Object.entries(d.hide || {})) if (vals.some((x) => eq(x, get(k)))) return false;
    return true;
  },

  // Renders `props` editing `obj` in place. ctx = {root, rootProps, change(rerender), readOnly}
  render(box, props, obj, ctx) {
    for (const p of props) {
      if (p.type === 'hidden' || !Params.visible(p, obj, props, ctx.root, ctx.rootProps)) continue;
      box.append(Params.field(p, obj, props, ctx));
    }
  },

  field(p, obj, siblings, ctx) {
    const wrap = h(`<div class="p"><div class="lbl"><span>${esc(p.displayName)}</span>${p.required ? '<span class="req">*</span>' : ''}<span class="grow"></span></div></div>`);
    const val = () => (obj[p.name] !== undefined ? obj[p.name] : p.default);
    const scalar = ['string', 'number', 'json', 'dateTime', 'resourceLocator', 'options', 'boolean', 'workflowSelector'].includes(p.type) && !p.noExpression;
    if (scalar && !ctx.readOnly) {
      const cur = p.type === 'resourceLocator' ? val()?.value : val();
      const fx = h(`<span class="fx ${isExpr(cur) ? 'on-expr' : ''}"><button class="${isExpr(cur) ? '' : 'on'}">Fixed</button><button class="${isExpr(cur) ? 'on' : ''}">Expression</button></span>`);
      const [bFixed, bExpr] = fx.querySelectorAll('button');
      const setRaw = (v) => (p.type === 'resourceLocator' ? (obj[p.name] = { __rl: true, value: v, mode: 'id' }) : (obj[p.name] = v));
      bExpr.onclick = () => { if (!isExpr(cur)) { setRaw('=' + (cur === undefined || cur === null || cur === '' ? '' : typeof cur === 'object' ? JSON.stringify(cur) : String(cur))); ctx.change(true); } };
      bFixed.onclick = () => { if (isExpr(cur)) { const t = cur.slice(1); setRaw(/\{\{/.test(t) ? (p.type === 'boolean' ? false : p.type === 'number' ? 0 : '') : t); ctx.change(true); } };
      wrap.querySelector('.lbl').append(fx);
    }
    wrap.append(Params.input(p, obj, ctx));
    if (p.description && !['collection', 'fixedCollection'].includes(p.type)) wrap.append(h(`<div class="desc">${esc(p.description)}</div>`));
    return wrap;
  },

  // Expression-aware text input used by plain fields, filters and assignments.
  text(get, set, ctx, opts = {}) {
    const v = get();
    if (isExpr(v)) {
      const t = h(`<textarea class="input expr" rows="${opts.rows || 1}" spellcheck="false"></textarea>`);
      t.value = v.slice(1);
      t.oninput = () => { set('=' + t.value); autoGrow(t); };
      setTimeout(() => autoGrow(t), 0);
      Params.dropTarget(t, (expr) => { insertAtCursor(t, expr); set('=' + t.value); });
      return t;
    }
    let el;
    if (opts.rows > 1 || opts.code) {
      el = h(`<textarea class="input ${opts.code ? 'code' : ''}" rows="${opts.rows || 4}" spellcheck="false"></textarea>`);
      if (opts.code) el.onkeydown = (e) => { if (e.key === 'Tab') { e.preventDefault(); insertAtCursor(el, '  '); el.dispatchEvent(new Event('input')); } };
    } else {
      el = h(`<input class="input" type="${opts.type || 'text'}" spellcheck="false">`);
    }
    el.placeholder = opts.placeholder || '';
    el.value = v ?? '';
    el.oninput = () => {
      if (!opts.noExpr && el.value.includes('{{')) {
        // Switch to expression mode in place so typing continues without losing focus.
        const caret = el.selectionStart;
        set('=' + el.value);
        const t = Params.text(get, set, ctx, opts);
        el.replaceWith(t);
        t.focus();
        t.selectionStart = t.selectionEnd = caret;
        t.closest('.p')?.querySelectorAll('.fx button').forEach((b, i) => b.classList.toggle('on', i === 1));
        return;
      }
      set(opts.type === 'number' && el.value !== '' && !isNaN(el.value) ? Number(el.value) : el.value);
    };
    if (!opts.noExpr) Params.dropTarget(el, (expr) => { set('=' + (el.value || '') + expr); ctx.change(true); });
    return el;
  },

  dropTarget(el, onDrop) {
    el.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('application/x-fw-field')) { e.preventDefault(); el.classList.add('drop-on'); } });
    el.addEventListener('dragleave', () => el.classList.remove('drop-on'));
    el.addEventListener('drop', (e) => {
      const f = e.dataTransfer.getData('application/x-fw-field');
      if (!f) return;
      e.preventDefault();
      e.stopPropagation();
      el.classList.remove('drop-on');
      onDrop(`{{ ${JSON.parse(f).expr} }}`);
    });
  },

  input(p, obj, ctx) {
    const get = () => (obj[p.name] !== undefined ? obj[p.name] : clone(p.default));
    const set = (v) => { obj[p.name] = v; ctx.change(false); };
    const ro = ctx.readOnly;
    const v = get();
    const t = p.type;
    if (isExpr(v) && t !== 'filter' && t !== 'assignmentCollection') return Params.text(get, set, ctx, { rows: 2 });
    switch (t) {
      case 'string': {
        const code = p.typeOptions?.editor;
        return Params.text(get, set, ctx, { rows: p.typeOptions?.rows || (code ? 12 : 1), code: !!code, placeholder: p.placeholder, type: p.typeOptions?.password ? 'password' : 'text', noExpr: p.noExpression });
      }
      case 'number': return Params.text(get, set, ctx, { type: 'number', placeholder: p.placeholder });
      case 'json': return Params.text(() => (typeof get() === 'string' ? get() : JSON.stringify(get(), null, 2)), set, ctx, { rows: 6, code: true });
      case 'dateTime': {
        const el = h('<input class="input" type="datetime-local">');
        el.value = v ? String(v).slice(0, 16) : '';
        el.onchange = () => set(el.value ? new Date(el.value).toISOString() : '');
        return el;
      }
      case 'resourceLocator': {
        const cur = typeof v === 'object' && v ? v.value : v;
        if (isExpr(cur)) return Params.text(() => cur, (x) => set({ __rl: true, value: x, mode: 'id' }), ctx, {});
        return Params.text(() => cur, (x) => set({ __rl: true, value: x, mode: 'id' }), ctx, { placeholder: 'ID' });
      }
      case 'boolean': {
        const b = h(`<button type="button" class="switch ${v ? 'on' : ''}"></button>`);
        b.onclick = () => { if (!ro) { obj[p.name] = !get(); ctx.change(true); } };
        return b;
      }
      case 'options': {
        const s = h(`<select class="input">${(p.options || []).map((o, i) => `<option value="${i}" title="${esc(o.description || '')}">${esc(o.name)}</option>`).join('')}</select>`);
        const idx = (p.options || []).findIndex((o) => String(o.value) === String(v));
        s.value = String(Math.max(0, idx));
        s.onchange = () => { obj[p.name] = p.options[Number(s.value)].value; ctx.change(true); };
        return s;
      }
      case 'credentialType': {
        const s = h(`<select class="input"><option value="">Select…</option>${App.credTypes.map((c) => `<option value="${c.name}">${esc(c.displayName)}</option>`).join('')}</select>`);
        s.value = v || '';
        s.onchange = () => { obj[p.name] = s.value; ctx.change(true); };
        return s;
      }
      case 'workflowSelector': {
        const s = h('<select class="input"><option value="">Loading…</option></select>');
        const cur = typeof v === 'object' && v ? v.value : v;
        api('GET', '/workflows').then((list) => {
          s.innerHTML = '<option value="">Select a workflow…</option>' + list.filter((w) => w.id !== E.wf.id).map((w) => `<option value="${w.id}">${esc(w.name)}</option>`).join('');
          s.value = cur || '';
        });
        s.onchange = () => { obj[p.name] = { __rl: true, value: s.value, mode: 'list', cachedResultName: s.selectedOptions[0]?.textContent }; ctx.change(false); };
        return s;
      }
      case 'multiOptions': {
        const arr = Array.isArray(v) ? v : [];
        const box = h('<div class="chips"></div>');
        for (const o of p.options || []) {
          const c = h(`<button type="button" class="chip ${arr.some((x) => String(x) === String(o.value)) ? 'on' : ''}">${esc(o.name)}</button>`);
          c.onclick = () => { if (ro) return; const cur = Array.isArray(get()) ? get() : []; obj[p.name] = cur.some((x) => String(x) === String(o.value)) ? cur.filter((x) => String(x) !== String(o.value)) : [...cur, o.value]; ctx.change(true); };
          box.append(c);
        }
        return box;
      }
      case 'notice': return h(`<div class="notice">${esc(p.displayName)}</div>`);
      case 'collection': return Params.collection(p, obj, ctx);
      case 'fixedCollection': return Params.fixedCollection(p, obj, ctx);
      case 'filter': return Params.filter(p, obj, ctx);
      case 'assignmentCollection': return Params.assignments(p, obj, ctx);
      default: return Params.text(get, set, ctx, {});
    }
  },

  collection(p, obj, ctx) {
    const cur = obj[p.name] && typeof obj[p.name] === 'object' ? obj[p.name] : (obj[p.name] = {});
    const box = h('<div></div>');
    const set = (p.options || []).filter((o) => cur[o.name] !== undefined);
    if (set.length) {
      const c = h('<div class="coll"></div>');
      for (const o of set) {
        const f = Params.field(o, cur, p.options, ctx);
        f.classList.add('opt-field');
        if (!ctx.readOnly) {
          const rm = h(`<button class="rm" title="Remove">${icon('trash')}</button>`);
          rm.onclick = () => { delete cur[o.name]; ctx.change(true); };
          f.append(rm);
        }
        c.append(f);
      }
      box.append(c);
    }
    const rest = (p.options || []).filter((o) => cur[o.name] === undefined && Params.visible(o, cur, p.options, ctx.root, ctx.rootProps));
    if (rest.length && !ctx.readOnly) {
      const s = h(`<select class="input" style="margin-top:8px"><option value="">${esc(p.placeholder || 'Add option')}</option>${rest.map((o) => `<option value="${o.name}">${esc(o.displayName)}</option>`).join('')}</select>`);
      s.onchange = () => { const o = rest.find((x) => x.name === s.value); cur[o.name] = clone(o.type === 'fixedCollection' ? {} : o.default); ctx.change(true); };
      box.append(s);
    }
    return box;
  },

  fixedCollection(p, obj, ctx) {
    const cur = obj[p.name] && typeof obj[p.name] === 'object' ? obj[p.name] : (obj[p.name] = {});
    const multi = p.typeOptions?.multipleValues;
    const box = h('<div></div>');
    for (const g of p.options || []) {
      const fresh = () => Params.defaults(g.values);
      const entry = (o, remove) => {
        const e = h('<div class="entry"></div>');
        Params.render(e, g.values, o, ctx);
        if (!ctx.readOnly) {
          const rm = h(`<button class="rm" title="Remove">${icon('trash')}</button>`);
          rm.onclick = remove;
          e.append(rm);
        }
        return e;
      };
      if ((p.options || []).length > 1 && (cur[g.name] !== undefined)) box.append(h(`<div class="hint" style="margin:6px 0">${esc(g.displayName)}</div>`));
      if (multi) {
        const list = Array.isArray(cur[g.name]) ? cur[g.name] : (cur[g.name] = cur[g.name] && typeof cur[g.name] === 'object' ? [cur[g.name]] : []);
        list.forEach((o, i) => box.append(entry(o, () => { list.splice(i, 1); ctx.change(true); })));
        if (!ctx.readOnly) {
          const add = h(`<button class="btn sm" style="width:100%;justify-content:center">${icon('plus')}${esc(p.placeholder || 'Add ' + (g.displayName || p.displayName))}</button>`);
          add.onclick = () => { list.push(fresh()); ctx.change(true); };
          box.append(add);
        }
      } else if (cur[g.name]) {
        box.append(entry(cur[g.name], () => { delete cur[g.name]; ctx.change(true); }));
      } else if (!ctx.readOnly) {
        const add = h(`<button class="btn sm" style="width:100%;justify-content:center;margin-bottom:6px">${icon('plus')}Add ${esc(g.displayName || p.displayName)}</button>`);
        add.onclick = () => { cur[g.name] = fresh(); ctx.change(true); };
        box.append(add);
      }
    }
    return box;
  },

  filter(p, obj, ctx) {
    const f = obj[p.name] && Array.isArray(obj[p.name].conditions) ? obj[p.name] : (obj[p.name] = { options: { caseSensitive: true, leftValue: '', typeValidation: 'strict' }, conditions: [], combinator: 'and' });
    if (!f.conditions.length) f.conditions.push({ id: uuid(), leftValue: '', rightValue: '', operator: { type: 'string', operation: 'equals' } });
    const box = h('<div></div>');
    f.conditions.forEach((c, i) => {
      const op = c.operator || (c.operator = { type: 'string', operation: 'equals' });
      const unary = UNARY.has(op.operation);
      const row = h(`<div class="cond ${unary ? 'unary' : ''}"></div>`);
      row.append(Params.text(() => c.leftValue, (v) => { c.leftValue = v; ctx.change(false); }, ctx, { placeholder: 'value1' }));
      const sel = h(`<select class="input">${Object.entries(OPS).map(([t, ops]) => `<optgroup label="${t[0].toUpperCase() + t.slice(1)}">${ops.map(([o, l]) => `<option value="${t}:${o}">${esc(l)}</option>`).join('')}</optgroup>`).join('')}</select>`);
      sel.value = `${op.type}:${op.operation}`;
      sel.onchange = () => {
        const [t, o] = sel.value.split(':');
        c.operator = { type: t, operation: o };
        if (UNARY.has(o)) c.operator.singleValue = true;
        ctx.change(true);
      };
      row.append(sel);
      if (!unary) row.append(Params.text(() => c.rightValue, (v) => { c.rightValue = v; ctx.change(false); }, ctx, { placeholder: 'value2' }));
      const rm = h(`<button class="rm" title="Remove condition">${icon('trash')}</button>`);
      rm.onclick = () => { f.conditions.splice(i, 1); ctx.change(true); };
      row.append(rm);
      box.append(row);
      if (i < f.conditions.length - 1) {
        const comb = h(`<select class="input" style="width:90px;height:28px;margin:0 0 8px;font-size:12px"><option value="and">AND</option><option value="or">OR</option></select>`);
        comb.value = f.combinator || 'and';
        comb.onchange = () => { f.combinator = comb.value; ctx.change(true); };
        box.append(comb);
      }
    });
    if (!ctx.readOnly) {
      const add = h(`<button class="btn sm">${icon('plus')}Add condition</button>`);
      add.onclick = () => { f.conditions.push({ id: uuid(), leftValue: '', rightValue: '', operator: { type: 'string', operation: 'equals' } }); ctx.change(true); };
      const cs = h(`<label style="display:inline-flex;align-items:center;gap:8px;margin-left:12px;font-size:12.5px" class="muted"><button type="button" class="switch ${f.options?.caseSensitive === false ? '' : 'on'}"></button>Case sensitive</label>`);
      $('button', cs).onclick = () => { f.options = { ...(f.options || {}), caseSensitive: f.options?.caseSensitive === false }; ctx.change(true); };
      box.append(add, cs);
    }
    return box;
  },

  assignments(p, obj, ctx) {
    const a = obj[p.name] && Array.isArray(obj[p.name].assignments) ? obj[p.name] : (obj[p.name] = { assignments: [] });
    const box = h('<div></div>');
    if (!a.assignments.length) box.append(h(`<div class="hint" style="padding:14px;border:1px dashed var(--line-2);border-radius:10px;text-align:center;margin-bottom:8px">Drag input fields here or add a field</div>`));
    a.assignments.forEach((x, i) => {
      const row = h('<div class="assign"></div>');
      row.append(Params.text(() => x.name, (v) => { x.name = v; ctx.change(false); }, ctx, { placeholder: 'name', noExpr: true }));
      const ty = h(`<select class="input">${['string', 'number', 'boolean', 'array', 'object'].map((t) => `<option>${t}</option>`).join('')}</select>`);
      ty.value = x.type || 'string';
      ty.onchange = () => { x.type = ty.value; ctx.change(true); };
      row.append(ty, Params.text(() => x.value, (v) => { x.value = v; ctx.change(false); }, ctx, { placeholder: 'value' }));
      const rm = h(`<button class="rm">${icon('trash')}</button>`);
      rm.onclick = () => { a.assignments.splice(i, 1); ctx.change(true); };
      row.append(rm);
      box.append(row);
    });
    if (!ctx.readOnly) {
      const add = h(`<button class="btn sm">${icon('plus')}Add field</button>`);
      add.onclick = () => { a.assignments.push({ id: uuid(), name: '', value: '', type: 'string' }); ctx.change(true); };
      box.append(add);
      box.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('application/x-fw-field')) { e.preventDefault(); box.classList.add('drop-on'); } });
      box.addEventListener('dragleave', () => box.classList.remove('drop-on'));
      box.addEventListener('drop', (e) => {
        const f = e.dataTransfer.getData('application/x-fw-field');
        if (!f) return;
        e.preventDefault();
        const d = JSON.parse(f);
        a.assignments.push({ id: uuid(), name: d.name, value: `={{ ${d.expr} }}`, type: d.type || 'string' });
        ctx.change(true);
      });
    }
    return box;
  },
};

function autoGrow(t) {
  t.style.height = 'auto';
  t.style.height = Math.min(t.scrollHeight + 2, 320) + 'px';
}
function insertAtCursor(el, text) {
  const s = el.selectionStart ?? el.value.length;
  const e = el.selectionEnd ?? el.value.length;
  el.value = el.value.slice(0, s) + text + el.value.slice(e);
  el.selectionStart = el.selectionEnd = s + text.length;
  el.focus();
}

// =====================================================================
// Editor state & helpers
// =====================================================================
const NODE = 96;
const GRID = 20;
let E = null; // current editor state
let push = null; // EventSource

const isTriggerType = (t) => { const d = typeOf(t); return (d.group || []).includes('trigger'); };
const isHookType = (t) => ['n8n-nodes-base.webhook', 'n8n-nodes-base.formTrigger', '@n8n/n8n-nodes-langchain.chatTrigger'].includes(t);
const hookBase = (t, test) => (t === 'n8n-nodes-base.formTrigger' ? App.settings.formUrl : t === '@n8n/n8n-nodes-langchain.chatTrigger' ? App.settings.formUrl.replace(/\/form$/, '/chat') : test ? App.settings.webhookTestUrl.replace(/-test$/, '') : App.settings.webhookUrl) + (test ? '-test' : '');
const nodeBy = (name) => E.wf.nodes.find((n) => n.name === name);
const lastRun = (name) => { const r = E.runData?.[name]; return r && r.length ? r[r.length - 1] : null; };

function outputsOf(n) {
  const d = typeOf(n.type);
  let outs;
  if (d.outputs === 'dynamic:switch') {
    const p = n.parameters || {};
    if (p.mode === 'expression') outs = Array.from({ length: Number(p.numberOutputs ?? 4) }, (_, i) => String(i));
    else {
      outs = (p.rules?.values || [{}]).map((r, i) => (r.renameOutput && r.outputKey) || String(i));
      if (p.options?.fallbackOutput === 'extra') outs.push('Fallback');
    }
  } else if (d.outputs === 'dynamic:categories') {
    const p = n.parameters || {};
    if (n.type.endsWith('.sentimentAnalysis')) outs = String(p.options?.categories ?? 'Positive, Neutral, Negative').split(',').map((c) => c.trim()).filter(Boolean);
    else {
      outs = (p.categories?.categories || []).map((c) => c.category || '');
      if (p.options?.fallback === 'other') outs.push('Other');
    }
    if (!outs.length) outs = [''];
  } else outs = Array.isArray(d.outputs) ? [...d.outputs] : [''];
  if (n.type === 'n8n-nodes-base.filter' && n.parameters?.options?.allowFailingItems) outs = ['Kept', 'Discarded'];
  if (n.onError === 'continueErrorOutput') outs.push('error');
  return outs;
}
const inputsOf = (n) => (n.type === 'n8n-nodes-base.stickyNote' ? 0 : typeOf(n.type).inputs ?? 1);

function hookPath(n) {
  const p = String(n.parameters?.path || '').replace(/^\/+|\/+$/g, '');
  if (!p) return n.webhookId || '';
  return p.includes(':') && n.webhookId ? `${n.webhookId}/${p}` : p;
}

function subtitle(n) {
  const p = n.parameters || {};
  if (n.type === 'n8n-nodes-base.httpRequest') return `${p.method || 'GET'}: ${String(p.url || '').replace(/^=/, '').slice(0, 40)}`;
  if (n.type === 'n8n-nodes-base.webhook') return `${p.httpMethod || 'GET'}: /${hookPath(n)}`;
  if (p.operation) return String(p.operation).replace(/([A-Z])/g, ' $1').toLowerCase();
  if (p.mode && typeof p.mode === 'string' && n.type !== 'n8n-nodes-base.set') return String(p.mode).replace(/([A-Z])/g, ' $1').toLowerCase();
  return '';
}

function uniqueName(base) {
  const names = new Set(E.wf.nodes.map((n) => n.name));
  if (!names.has(base)) return base;
  const stem = base.replace(/\d+$/, '');
  for (let i = 1; ; i++) if (!names.has(stem + i)) return stem + i;
}

function snapshot() { return JSON.stringify({ nodes: E.wf.nodes, connections: E.wf.connections, pinData: E.wf.pinData }); }
function remember() {
  E.history.push(snapshot());
  if (E.history.length > 80) E.history.shift();
  E.future = [];
}
function restore(s) {
  const o = JSON.parse(s);
  Object.assign(E.wf, o);
  E.sel.clear();
  dirty();
}
function undo() { if (!E.history.length) return; E.future.push(snapshot()); restore(E.history.pop()); }
function redo() { if (!E.future.length) return; E.history.push(snapshot()); restore(E.future.pop()); }
function dirty() {
  E.dirty = true;
  const s = $('.saved');
  if (s) s.textContent = 'Unsaved changes';
  draw();
}

function connList(src, out) {
  const c = (E.wf.connections[src] ||= {});
  const main = (c.main ||= []);
  while (main.length <= out) main.push([]);
  if (!main[out]) main[out] = [];
  return main[out];
}
function connect(src, out, dst, input = 0) {
  if (src === dst) return;
  const l = connList(src, out);
  if (l.some((c) => c.node === dst && c.index === input)) return;
  l.push({ node: dst, type: 'main', index: input });
}
function disconnect(src, out, dst, input) {
  const l = E.wf.connections[src]?.main?.[out];
  if (!l) return;
  const i = l.findIndex((c) => c.node === dst && c.index === input);
  if (i >= 0) l.splice(i, 1);
}
function parentsOf(name) {
  const r = [];
  for (const [src, m] of Object.entries(E.wf.connections || {})) (m.main || []).forEach((l, out) => (l || []).forEach((c) => { if (c.node === name) r.push({ src, out, index: c.index }); }));
  return r;
}

function deleteNodes(names) {
  if (!names.length) return;
  remember();
  const del = new Set(names);
  // Reconnect a single parent to the children of a deleted single-input node, like n8n.
  if (names.length === 1) {
    const ps = parentsOf(names[0]);
    const kids = (E.wf.connections[names[0]]?.main?.[0] || []);
    if (ps.length === 1 && kids.length) kids.forEach((k) => connect(ps[0].src, ps[0].out, k.node, k.index));
  }
  E.wf.nodes = E.wf.nodes.filter((n) => !del.has(n.name));
  for (const n of names) { delete E.wf.connections[n]; delete E.wf.pinData?.[n]; }
  for (const m of Object.values(E.wf.connections)) for (const t of Object.keys(m)) (m[t] || []).forEach((l, i) => { if (l) m[t][i] = l.filter((c) => !del.has(c.node)); });
  names.forEach((n) => E.sel.delete(n));
  dirty();
}

function renameNode(oldName, newName) {
  newName = newName.trim();
  if (!newName || newName === oldName) return oldName;
  if (nodeBy(newName)) { toast('Name already used', `A node called "${newName}" exists`, true); return oldName; }
  const n = nodeBy(oldName);
  n.name = newName;
  if (E.wf.connections[oldName]) { E.wf.connections[newName] = E.wf.connections[oldName]; delete E.wf.connections[oldName]; }
  for (const m of Object.values(E.wf.connections)) for (const t of Object.keys(m)) (m[t] || []).forEach((l) => (l || []).forEach((c) => { if (c.node === oldName) c.node = newName; }));
  for (const obj of [E.wf.pinData, E.runData, E.status]) if (obj && obj[oldName] !== undefined) { obj[newName] = obj[oldName]; delete obj[oldName]; }
  // Update expression references such as $('Old') and $node["Old"].
  const q = oldName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`(\\$\\(\\s*['"\`]|\\$node\\[\\s*['"\`]|\\$items\\(\\s*['"\`])${q}(['"\`])`, 'g');
  for (const x of E.wf.nodes) x.parameters = JSON.parse(JSON.stringify(x.parameters || {}).replace(re, (_, a, b) => a + newName.replace(/"/g, '\\"') + b));
  if (E.sel.delete(oldName)) E.sel.add(newName);
  return newName;
}

function newNode(type, pos) {
  const d = typeOf(type);
  const n = { id: uuid(), name: uniqueName(d.defaults?.name || d.displayName), type, typeVersion: d.version, position: pos, parameters: Params.defaults(d.properties || []) };
  for (const k of Object.keys(n.parameters)) if (n.parameters[k] === undefined) delete n.parameters[k];
  if (isHookType(type)) n.webhookId = uuid();
  if (type === 'n8n-nodes-base.webhook') n.parameters.path = n.webhookId;
  if (type === 'n8n-nodes-base.stickyNote') { n.parameters.width = 260; n.parameters.height = 180; }
  return n;
}

// =====================================================================
// Editor views
// =====================================================================
const Editor = {
  async open(id, q) {
    let wf;
    if (id === 'new' || !id) wf = { id: '', name: 'My workflow', nodes: [], connections: {}, settings: { executionOrder: 'v1' }, pinData: {}, active: false, folder: q.folder || '' };
    else {
      try { wf = await api('GET', '/workflows/' + id); } catch (e) { fail(e); location.hash = '#/workflows'; return; }
    }
    wf.connections ||= {};
    wf.pinData ||= {};
    wf.settings ||= {};
    Editor.mount(wf, { readOnly: wf.canEdit === false });
    if (wf.canEdit === false) toast('View only', 'This workflow was shared with you for viewing.');
    if (q.executionId) Editor.loadExecutionData(q.executionId);
  },

  async openExecution(id) {
    let ex;
    try { ex = await api('GET', '/executions/' + id); } catch (e) { fail(e); location.hash = '#/executions'; return; }
    const wf = ex.workflowData || { nodes: [], connections: {} };
    wf.connections ||= {};
    wf.pinData ||= {};
    Editor.mount(wf, { readOnly: true, execution: ex });
    Editor.applyExecution(ex, false);
  },

  async loadExecutionData(id) {
    try { Editor.applyExecution(await api('GET', '/executions/' + id), false); } catch (e) { fail(e); }
  },

  mount(wf, opts) {
    E = { wf, readOnly: opts.readOnly, execution: opts.execution || null, runData: {}, status: {}, counts: {}, sel: new Set(), selEdge: null,
      view: { x: 120, y: 160, k: 1 }, dirty: false, history: [], future: [], running: null, listenCtl: null, dataMode: localStorage.getItem('fw-dm') || 'schema' };
    const ex = E.execution;
    const root = h(`<div class="editor">
      <div class="ed-top">
        <a class="btn ghost sm icon" href="#/${ex ? 'executions' : 'workflows'}" title="Back">${icon('back')}</a>
        <input class="ed-name" value="${esc(wf.name)}" ${E.readOnly ? 'disabled' : ''}>
        <span class="saved">${ex ? statusPill(ex.status) + ` <span class="muted" style="margin-left:6px">Execution #${ex.id} · ${fmtDate(ex.startedAt)}</span>` : wf.id ? 'Saved' : 'Not saved yet'}</span>
        <span class="grow"></span>
        ${ex ? `<button class="btn sm" data-openwf>${icon('edit')}Open workflow</button>${ex.status === 'error' ? `<button class="btn sm" data-retry>Retry</button>` : ''}` : `
        <a class="btn ghost sm" data-execs>${icon('history')}Executions</a>
        <span class="muted" style="font-size:13px">${wf.active ? 'Active' : 'Inactive'}</span><button class="switch ${wf.active ? 'on' : ''}" data-active title="Activate"></button>
        <button class="btn primary sm" data-save>Save</button>
        <button class="btn ghost sm icon" data-more>${icon('more')}</button>`}
      </div>
      ${E.readOnly ? `<div class="readonly-banner">${icon('eye')}You are viewing a past execution (read-only). Click a node to inspect its data.</div>` : ''}
      <div class="canvas"><div class="world"><svg class="edges"></svg></div>
        <div class="canvas-ctl">
          <button class="btn icon" data-fit title="Fit view (1)">${icon('fit')}</button>
          <button class="btn icon" data-zin title="Zoom in">${icon('zoomin')}</button>
          <button class="btn icon" data-zout title="Zoom out">${icon('zoomout')}</button>
          ${E.readOnly ? '' : `<button class="btn icon" data-tidy title="Tidy up">${icon('layers')}</button><button class="btn icon" data-sticky title="Add sticky note (Shift+S)">${icon('note')}</button>`}
        </div>
        <div class="run-bar">${E.readOnly ? '' : `<button class="btn primary" data-run>${icon('play')}Test workflow</button><button class="btn hidden" data-stop>${icon('stop')}Stop</button>`}</div>
        ${E.readOnly ? '' : `<button class="btn icon" data-add style="position:absolute;right:16px;top:16px;z-index:3;width:42px;height:42px" title="Add node (Tab)">${icon('plus')}</button>`}
      </div></div>`);
    shell('workflows', root, { collapsed: true });
    E.root = root;
    E.canvas = $('.canvas', root);
    E.world = $('.world', root);
    E.svg = $('.edges', root);
    const name = $('.ed-name', root);
    name.onchange = () => { E.wf.name = name.value.trim() || 'My workflow'; dirty(); };
    name.onkeydown = (e) => { if (e.key === 'Enter') name.blur(); };
    if (!E.readOnly) {
      $('[data-save]', root).onclick = () => Editor.save();
      $('[data-active]', root).onclick = () => Editor.toggleActive();
      $('[data-more]', root).onclick = (e) => Editor.moreMenu(e);
      $('[data-execs]', root).onclick = () => { location.hash = '#/executions?workflowId=' + (E.wf.id || ''); };
      $('[data-run]', root).onclick = () => Editor.run();
      $('[data-stop]', root).onclick = () => Editor.stop();
      $('[data-add]', root).onclick = () => Creator.open();
      $('[data-tidy]', root).onclick = () => Editor.tidy();
      $('[data-sticky]', root).onclick = () => Editor.addSticky();
      App.leaveGuard = () => E && E.dirty && !E.readOnly;
    } else {
      $('[data-openwf]', root).onclick = () => (location.hash = '#/workflow/' + E.wf.id);
      $('[data-retry]', root)?.addEventListener('click', async () => { try { const r = await api('POST', `/executions/${E.execution.id}/retry`, {}); toast('Retry finished', 'Status: ' + r.status, r.status !== 'success'); location.hash = '#/execution/' + r.executionId; } catch (e) { fail(e); } });
    }
    $('[data-fit]', root).onclick = () => Editor.fit();
    $('[data-zin]', root).onclick = () => Editor.zoom(1.2);
    $('[data-zout]', root).onclick = () => Editor.zoom(1 / 1.2);
    E.savedSnap = snapshot();
    Canvas.bind();
    Editor.connectPush();
    draw();
    requestAnimationFrame(() => Editor.fit(true));
  },

  serialize() {
    return { name: E.wf.name, nodes: E.wf.nodes, connections: E.wf.connections, settings: E.wf.settings, pinData: E.wf.pinData, id: E.wf.id || undefined, active: E.wf.active, folder: E.wf.folder || '' };
  },

  async save(silent) {
    if (E.readOnly) return;
    try {
      const body = Editor.serialize();
      let w;
      if (!E.wf.id) {
        w = await api('POST', '/workflows', body);
        E.wf.id = w.id;
        history.replaceState(null, '', '#/workflow/' + w.id);
        lastHash = location.hash;
      } else {
        delete body.active;
        w = await api('PATCH', '/workflows/' + E.wf.id, body);
      }
      E.wf.nodes = w.nodes;
      E.wf.updatedAt = w.updatedAt;
      E.dirty = false;
      E.savedSnap = snapshot();
      $('.saved').textContent = 'Saved';
      if (!silent) toast('Workflow saved');
      draw();
      return true;
    } catch (e) { fail(e); return false; }
  },

  async toggleActive() {
    if (E.dirty || !E.wf.id) if (!(await Editor.save(true))) return;
    try {
      const w = await api('POST', `/workflows/${E.wf.id}/${E.wf.active ? 'deactivate' : 'activate'}`);
      E.wf.active = w.active;
      $('[data-active]').classList.toggle('on', w.active);
      $('[data-active]').previousElementSibling.textContent = w.active ? 'Active' : 'Inactive';
      toast(w.active ? 'Workflow activated' : 'Workflow deactivated', w.active ? 'Triggers, webhooks and schedules are now live.' : '');
    } catch (e) { toast('Could not activate', e.message, true); }
  },

  moreMenu(e) {
    menu(e.clientX, e.clientY, [
      { label: 'Workflow settings', icon: 'settings', action: () => Editor.settingsModal() },
      { label: 'Share…', icon: 'users', action: async () => { if (!E.wf.id && !(await Editor.save(true))) return; shareModal('workflows', E.wf.id, E.wf.name).catch(fail); } },
      { label: 'Download', icon: 'download', action: () => download((E.wf.name || 'workflow') + '.json', JSON.stringify(Editor.serialize(), null, 2)) },
      { label: 'Import from file…', icon: 'upload', action: async () => { const t = await pickFile(); if (t) Editor.pasteJson(t, true); } },
      { label: 'Import from JSON…', icon: 'code', action: async () => {
        const body = h('<textarea class="input code" rows="12" placeholder="Paste workflow or node JSON"></textarea>');
        const r = await modal('Import JSON', body, [{ label: 'Cancel', value: null }, { label: 'Import', cls: 'primary', action: () => body.value }]);
        if (r) Editor.pasteJson(r, true);
      } },
      { label: 'Duplicate', icon: 'copy', action: () => createWorkflow({ ...Editor.serialize(), id: undefined, name: E.wf.name + ' (copy)' }).catch(fail) },
      '-',
      { label: 'Delete workflow', icon: 'trash', danger: true, action: async () => {
        if (!E.wf.id) return (location.hash = '#/workflows');
        if (await confirmBox('Delete workflow?', `"${E.wf.name}" will be deleted permanently.`)) { await api('DELETE', '/workflows/' + E.wf.id).catch(fail); E.dirty = false; location.hash = '#/workflows'; }
      } },
    ]);
  },

  async settingsModal() {
    const wfs = await api('GET', '/workflows').catch(() => []);
    const s = E.wf.settings || {};
    const body = h(`<div>
      <div class="field"><label class="label">Error workflow</label><select class="input" data-err><option value="">No error workflow</option>${wfs.filter((w) => w.id !== E.wf.id).map((w) => `<option value="${w.id}">${esc(w.name)}</option>`).join('')}</select>
      <div class="hint">Runs when this workflow fails in production. It must start with an Error Trigger.</div></div>
      <div class="field"><label class="label">Timezone</label><input class="input" data-tz placeholder="Default (${esc(App.settings.timezone)})" value="${esc(s.timezone && s.timezone !== 'DEFAULT' ? s.timezone : '')}">
      <div class="hint">IANA name such as Europe/Berlin. Used by schedules and date nodes.</div></div>
      <div class="field"><label class="label">Execution order</label><input class="input" value="v1 (depth first, like n8n)" disabled></div></div>`);
    $('[data-err]', body).value = s.errorWorkflow || '';
    const ok = await modal('Workflow settings', body, [{ label: 'Cancel', value: null }, { label: 'Save', cls: 'primary', value: true }]);
    if (!ok) return;
    E.wf.settings = { ...s, executionOrder: 'v1', errorWorkflow: $('[data-err]', body).value || undefined, timezone: $('[data-tz]', body).value.trim() || 'DEFAULT' };
    dirty();
  },

  // ---------- view ----------
  applyView() { E.world.style.transform = `translate(${E.view.x}px,${E.view.y}px) scale(${E.view.k})`; E.canvas.style.backgroundSize = `${GRID * E.view.k}px ${GRID * E.view.k}px`; E.canvas.style.backgroundPosition = `${E.view.x}px ${E.view.y}px`; },
  zoom(f, cx, cy) {
    const r = E.canvas.getBoundingClientRect();
    cx ??= r.width / 2; cy ??= r.height / 2;
    const k = Math.min(2.5, Math.max(0.2, E.view.k * f));
    E.view.x = cx - ((cx - E.view.x) * k) / E.view.k;
    E.view.y = cy - ((cy - E.view.y) * k) / E.view.k;
    E.view.k = k;
    Editor.applyView();
  },
  fit(initial) {
    const ns = E.wf.nodes;
    const r = E.canvas.getBoundingClientRect();
    if (!ns.length) { E.view = { x: r.width / 2 - NODE / 2, y: r.height / 2 - NODE, k: 1 }; return Editor.applyView(); }
    const xs = ns.map((n) => n.position[0]), ys = ns.map((n) => n.position[1]);
    const x0 = Math.min(...xs) - 60, y0 = Math.min(...ys) - 60, x1 = Math.max(...xs) + NODE + 120, y1 = Math.max(...ys) + NODE + 100;
    const k = Math.min(initial ? 1 : 1.4, Math.max(0.25, Math.min(r.width / (x1 - x0), (r.height - 80) / (y1 - y0))));
    E.view = { k, x: (r.width - (x1 - x0) * k) / 2 - x0 * k, y: (r.height - 60 - (y1 - y0) * k) / 2 - y0 * k };
    Editor.applyView();
  },
  tidy() {
    remember();
    const conns = E.wf.connections;
    const nodes = E.wf.nodes.filter((n) => n.type !== 'n8n-nodes-base.stickyNote');
    const level = {};
    const roots = nodes.filter((n) => !parentsOf(n.name).length);
    const queue = roots.map((n) => [n.name, 0]);
    let guard = 0;
    while (queue.length && guard++ < 5000) {
      const [name, l] = queue.shift();
      if (level[name] !== undefined && level[name] >= l) continue;
      if (level[name] !== undefined && l > nodes.length) continue;
      level[name] = l;
      for (const out of conns[name]?.main || []) for (const c of out || []) if ((level[c.node] ?? -1) < l + 1 && l + 1 <= nodes.length) queue.push([c.node, l + 1]);
    }
    const cols = {};
    for (const n of nodes) (cols[level[n.name] ?? 0] ||= []).push(n);
    for (const [l, list] of Object.entries(cols)) list.forEach((n, i) => (n.position = [Number(l) * 240, (i - (list.length - 1) / 2) * 180]));
    dirty();
    Editor.fit();
  },
  addSticky() {
    remember();
    const r = E.canvas.getBoundingClientRect();
    const n = newNode('n8n-nodes-base.stickyNote', snapPos((r.width / 2 - E.view.x) / E.view.k - 130, (r.height / 2 - E.view.y) / E.view.k - 90));
    E.wf.nodes.push(n);
    dirty();
  },

  // ---------- clipboard ----------
  copySelection() {
    const names = [...E.sel];
    if (!names.length) return null;
    const set = new Set(names);
    const connections = {};
    for (const n of names) {
      const m = E.wf.connections[n]?.main;
      if (!m) continue;
      const main = m.map((l) => (l || []).filter((c) => set.has(c.node)));
      if (main.some((l) => l.length)) connections[n] = { main };
    }
    return JSON.stringify({ nodes: E.wf.nodes.filter((n) => set.has(n.name)), connections }, null, 2);
  },
  pasteJson(text, replaceName) {
    let d;
    try { d = JSON.parse(text); } catch { return false; }
    if (!d || !Array.isArray(d.nodes)) return false;
    remember();
    const rename = {};
    const minX = Math.min(...d.nodes.map((n) => n.position?.[0] ?? 0));
    const minY = Math.min(...d.nodes.map((n) => n.position?.[1] ?? 0));
    const r = E.canvas.getBoundingClientRect();
    const ox = E.wf.nodes.length ? (r.width / 2 - E.view.x) / E.view.k - 100 : 0;
    const oy = E.wf.nodes.length ? (r.height / 2 - E.view.y) / E.view.k - 100 : 0;
    E.sel.clear();
    for (const n of d.nodes) {
      const nn = clone(n);
      nn.name = uniqueName(n.name);
      rename[n.name] = nn.name;
      nn.id = uuid();
      nn.position = snapPos((n.position?.[0] ?? 0) - minX + ox, (n.position?.[1] ?? 0) - minY + oy);
      if (isHookType(nn.type) && (!nn.webhookId || E.wf.nodes.some((x) => x.webhookId === nn.webhookId))) nn.webhookId = uuid();
      nn.parameters ||= {};
      E.wf.nodes.push(nn);
      E.sel.add(nn.name);
    }
    for (const [src, m] of Object.entries(d.connections || {})) (m.main || []).forEach((l, out) => (l || []).forEach((c) => { if (rename[src] && rename[c.node]) connect(rename[src], out, rename[c.node], c.index || 0); }));
    if (replaceName && d.name && !E.wf.nodes.length) E.wf.name = d.name;
    if (d.pinData) for (const [k, v] of Object.entries(d.pinData)) if (rename[k]) E.wf.pinData[rename[k]] = v;
    dirty();
    if (replaceName) Editor.fit();
    return true;
  },

  // ---------- running ----------
  async run(destination) {
    if (E.readOnly || E.running) return;
    if (!E.wf.nodes.length) return toast('Add a node first', '', true);
    if ((E.dirty || !E.wf.id) && !(await Editor.save(true))) return;
    const enabled = E.wf.nodes.filter((n) => !n.disabled);
    const dest = destination ? nodeBy(destination) : null;
    const direct = enabled.some((n) => isTriggerType(n.type) && !isHookType(n.type));
    const hooks = enabled.filter((n) => isHookType(n.type));
    const needsListen = (dest && isHookType(dest.type) && !E.wf.pinData[dest.name]) || (!direct && hooks.length && !hooks.some((n) => E.wf.pinData[n.name]));
    if (needsListen) return Editor.listen();
    E.status = {}; E.counts = {}; E.runData = {};
    draw();
    try {
      const r = await api('POST', '/workflows/run', { workflowData: Editor.serialize(), destinationNode: destination });
      await Editor.follow(r.executionId);
    } catch (e) { fail(e); }
  },

  async follow(id) {
    E.running = id;
    $('[data-run]')?.classList.add('hidden');
    $('[data-stop]')?.classList.remove('hidden');
    let ex;
    for (;;) {
      await new Promise((r) => setTimeout(r, 350));
      if (!E || E.running !== id) return;
      try { ex = await api('GET', '/executions/' + id); } catch { continue; }
      if (ex.status !== 'running') break;
    }
    E.running = null;
    $('[data-run]')?.classList.remove('hidden');
    $('[data-stop]')?.classList.add('hidden');
    Editor.applyExecution(ex, true);
  },

  applyExecution(ex, notify) {
    const rd = ex.data?.resultData || {};
    E.runData = rd.runData || {};
    E.status = {};
    E.counts = {};
    for (const [n, runs] of Object.entries(E.runData)) {
      const t = runs[runs.length - 1];
      E.status[n] = t.executionStatus === 'error' ? 'error' : 'ok';
    }
    draw();
    NDV.refresh();
    if (!notify) return;
    if (ex.status === 'success') toast('Workflow executed successfully');
    else if (ex.status === 'canceled') toast('Execution stopped', '', true);
    else toast(rd.error?.node ? `Problem in node ‘${rd.error.node}‘` : 'Workflow failed', rd.error?.message || '', true);
  },

  async stop() {
    if (E.listenCtl) return Editor.stopListen();
    if (E.running) await api('POST', `/executions/${E.running}/stop`).catch(() => {});
  },

  async listen() {
    const wf = Editor.serialize();
    const hooks = E.wf.nodes.filter((n) => !n.disabled && isHookType(n.type));
    const base = App.settings.webhookTestUrl;
    const formBase = App.settings.formUrl.replace(/\/form$/, '/form-test');
    const banner = h(`<div class="listen"><div style="display:flex;align-items:center;gap:10px"><span class="pill run"><span class="dot"></span>Listening</span>
      <b style="flex:1">Waiting for a test event…</b><button class="btn sm" data-x>Stop listening</button></div>
      ${hooks.map((n) => {
        const url = `${hookBase(n.type, true)}/${hookPath(n)}`;
        const m = n.type === 'n8n-nodes-base.formTrigger' ? 'FORM' : n.type.endsWith('chatTrigger') ? 'CHAT' : n.parameters?.httpMethod || 'GET';
        return `<div class="u"><b>${esc(m)}</b><span style="flex:1">${esc(url)}</span><button class="btn sm" data-copy="${esc(url)}">Copy</button>${m !== 'POST' && m !== 'PUT' && m !== 'PATCH' && m !== 'DELETE' ? `<a class="btn sm" target="_blank" href="${esc(url)}">Open</a>` : ''}</div>`;
      }).join('')}
      <div class="hint">Call the test URL once; the workflow runs with that request. Test URLs stay registered for 2 minutes.</div></div>`);
    banner.querySelectorAll('[data-copy]').forEach((b) => (b.onclick = () => copyText(b.dataset.copy)));
    $('[data-x]', banner).onclick = () => Editor.stopListen();
    E.canvas.append(banner);
    E.listenCtl = new AbortController();
    $('[data-run]')?.classList.add('hidden');
    $('[data-stop]')?.classList.remove('hidden');
    try {
      const r = await api('POST', '/workflows/listen', { workflowData: wf }, { signal: E.listenCtl.signal });
      banner.remove();
      E.listenCtl = null;
      if (r.executionId) { E.status = {}; E.runData = {}; draw(); await Editor.follow(r.executionId); }
      else toast('No test event received', 'Listening stopped after 2 minutes.', true);
    } catch (e) {
      if (e.name !== 'AbortError') fail(e);
    } finally {
      banner.remove();
      if (E) E.listenCtl = null;
      if (E && !E.running) { $('[data-run]')?.classList.remove('hidden'); $('[data-stop]')?.classList.add('hidden'); }
    }
  },
  stopListen() {
    E.listenCtl?.abort();
    api('POST', '/workflows/listen/stop', { workflowId: E.wf.id }).catch(() => {});
  },

  connectPush() {
    if (push) return;
    push = new EventSource('/rest/push');
    push.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch { return; }
      if (ev.type === 'console') { console.log('[Flowwolf]', ev.data.message); return; }
      if (!E || !E.running || ev.data.executionId !== String(E.running)) return;
      if (ev.type === 'nodeExecuteBefore') E.status[ev.data.nodeName] = 'running';
      if (ev.type === 'nodeExecuteAfter') { E.status[ev.data.nodeName] = ev.data.status === 'error' ? 'error' : 'ok'; E.counts[ev.data.nodeName] = ev.data.itemCounts; }
      draw();
    };
  },
};
const snapPos = (x, y) => [Math.round(x / GRID) * GRID, Math.round(y / GRID) * GRID];

// =====================================================================
// Canvas rendering & interaction
// =====================================================================
function handlePos(n, kind, i) {
  const count = kind === 'in' ? inputsOf(n) : outputsOf(n).length;
  return [n.position[0] + (kind === 'in' ? 0 : NODE), n.position[1] + (NODE * (i + 1)) / (count + 1)];
}
function edgePath(x1, y1, x2, y2) {
  if (x2 >= x1 + 30) {
    const dx = Math.max(40, (x2 - x1) / 2);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
  }
  // Backwards (loop) edge: go around below the nodes.
  const yb = Math.max(y1, y2) + 90;
  return `M${x1},${y1} C${x1 + 70},${y1} ${x1 + 70},${yb} ${x1},${yb} L${x2},${yb} C${x2 - 70},${yb} ${x2 - 70},${y2} ${x2},${y2}`;
}
function itemCount(src, out) {
  const t = lastRun(src);
  if (t) return t.data?.main?.[out]?.length ?? 0;
  return E.counts[src]?.[out];
}

function draw() {
  if (!E || !E.world) return;
  const world = E.world;
  world.querySelectorAll('.node,.sticky').forEach((el) => el.remove());
  E.canvas.querySelector('.canvas-empty')?.remove();
  if (!E.wf.nodes.length && !E.readOnly) {
    const em = h(`<div class="canvas-empty"><div class="add">${icon('plus')}</div><b>Add first step…</b><div class="muted" style="font-size:13px">Choose a trigger to start your workflow</div></div>`);
    $('.add', em).onclick = () => Creator.open();
    E.canvas.append(em);
  }
  // edges
  let svg = '';
  for (const [src, m] of Object.entries(E.wf.connections || {})) {
    const sn = nodeBy(src);
    if (!sn) continue;
    (m.main || []).forEach((list, out) => (list || []).forEach((c) => {
      const tn = nodeBy(c.node);
      if (!tn) return;
      const [x1, y1] = handlePos(sn, 'out', out);
      const [x2, y2] = handlePos(tn, 'in', c.index || 0);
      const d = edgePath(x1, y1, x2, y2);
      const cnt = itemCount(src, out);
      const sel = E.selEdge && E.selEdge.src === src && E.selEdge.out === out && E.selEdge.dst === c.node && E.selEdge.index === (c.index || 0);
      const mx = (x1 + x2) / 2, my = x2 >= x1 + 30 ? (y1 + y2) / 2 : Math.max(y1, y2) + 90;
      const key = esc(JSON.stringify([src, out, c.node, c.index || 0]));
      svg += `<g data-edge="${key}"><path class="edge-hit" d="${d}"/><path class="edge ${cnt ? 'ok' : ''} ${sel ? 'sel' : ''}" d="${d}"/>
        ${cnt !== undefined ? `<text class="edge-label" x="${mx}" y="${my - 8}" text-anchor="middle">${cnt} item${cnt === 1 ? '' : 's'}</text>` : ''}
        ${E.readOnly ? '' : `<g class="edge-del" transform="translate(${mx},${my + 10})" opacity="${sel ? 1 : 0}"><circle r="10" fill="var(--panel)" stroke="var(--err)"/><path d="M-3.5 -3.5 3.5 3.5M3.5 -3.5 -3.5 3.5" stroke="var(--err)" stroke-width="2"/></g>`}</g>`;
    }));
  }
  const aiLinks = [];
  for (const [src, m] of Object.entries(E.wf.connections || {})) {
    for (const [type, lists] of Object.entries(m)) {
      if (!type.startsWith('ai_')) continue;
      (lists || []).forEach((list) => (list || []).forEach((c) => { const sn = nodeBy(src), tn = nodeBy(c.node); if (sn && tn) aiLinks.push({ sn, tn, type }); }));
    }
  }
  // Each root spreads its sub-node lines along its bottom edge, ordered by the sub-nodes' x.
  const byRoot = {};
  aiLinks.forEach((l) => (byRoot[l.tn.name] ||= []).push(l));
  for (const links of Object.values(byRoot)) {
    links.sort((a, b) => a.sn.position[0] - b.sn.position[0]);
    const ins = typeOf(links[0].tn.type).aiInputs || [];
    links.forEach((l, k) => {
      const x1 = l.sn.position[0] + NODE / 2, y1 = l.sn.position[1];
      const slot = ins.indexOf(l.type);
      let x2, y2;
      if (slot >= 0 && !E.readOnly) {
        // End at the "+ Type" button of the root; several sub-nodes of one type fan out slightly.
        const same = links.filter((o) => o.type === l.type);
        x2 = l.tn.position[0] + NODE / 2 + (slot - (ins.length - 1) / 2) * SUB_W + (same.indexOf(l) - (same.length - 1) / 2) * 10;
        y2 = l.tn.position[1] + NODE + 46 + 22;
      } else {
        x2 = l.tn.position[0] + NODE / 2 + (k - (links.length - 1) / 2) * Math.min(18, (NODE - 16) / Math.max(1, links.length - 1));
        y2 = l.tn.position[1] + NODE + 26;
      }
      const dy = Math.max(30, (y1 - y2) / 2);
      svg += `<g class="ai-edge"><path class="edge ai" d="M${x1},${y1} C${x1},${y1 - dy} ${x2},${y2 + dy} ${x2},${y2}"/>
        <text class="edge-label" x="${x1}" y="${y1 - 10}" text-anchor="middle">${esc(l.type.slice(3).replace(/([A-Z])/g, ' $1').toLowerCase())}</text></g>`;
    });
  }
  E.svg.innerHTML = svg + '<path class="edge temp" d="" style="stroke:var(--brand);stroke-dasharray:6 4"/>';
  E.svg.querySelectorAll('[data-edge]').forEach((g) => {
    const [src, out, dst, index] = JSON.parse(g.dataset.edge);
    g.addEventListener('mouseenter', () => { const d = g.querySelector('.edge-del'); if (d) d.setAttribute('opacity', 1); });
    g.addEventListener('mouseleave', () => { const d = g.querySelector('.edge-del'); if (d && !(E.selEdge && E.selEdge.src === src && E.selEdge.dst === dst)) d.setAttribute('opacity', 0); });
    g.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      if (e.target.closest('.edge-del')) { remember(); disconnect(src, out, dst, index); E.selEdge = null; dirty(); return; }
      E.selEdge = { src, out, dst, index };
      E.sel.clear();
      draw();
    });
  });

  // nodes
  for (const n of E.wf.nodes) {
    if (n.type === 'n8n-nodes-base.stickyNote') {
      const p = n.parameters || {};
      const st = h(`<div class="sticky ${E.sel.has(n.name) ? 'sel' : ''}" style="left:${n.position[0]}px;top:${n.position[1]}px;width:${p.width || 260}px;height:${p.height || 180}px"></div>`);
      st.innerHTML = esc(p.content ?? '').replace(/^### (.*)$/gm, '<h3>$1</h3>').replace(/^## (.*)$/gm, '<h2>$1</h2>').replace(/^# (.*)$/gm, '<h1>$1</h1>').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
      st.dataset.node = n.name;
      world.append(st);
      continue;
    }
    const d = typeOf(n.type);
    const trig = isTriggerType(n.type);
    const pinned = E.wf.pinData?.[n.name];
    const status = E.status[n.name];
    const t = lastRun(n.name);
    const total = t ? (t.data?.main || []).reduce((a, o) => a + (o?.length || 0), 0) : (E.counts[n.name] || []).reduce((a, b) => a + b, 0);
    const el = h(`<div class="node ${trig ? 'trigger' : ''} ${E.sel.has(n.name) ? 'sel' : ''} ${status || ''} ${n.disabled ? 'disabled' : ''}" style="left:${n.position[0]}px;top:${n.position[1]}px" data-node="${esc(n.name)}">
      <div class="box" style="color:${inkOf(d.color)}">${icon(d.icon)}</div>
      ${status === 'ok' || status === 'error' ? `<div class="badge">${icon(status === 'ok' ? 'check' : 'alert')}${status === 'ok' && total ? total : ''}</div>` : ''}
      ${pinned ? `<div class="pin-b" title="Pinned data">${icon('pin')}</div>` : ''}
      <div class="title">${esc(n.name)}${n.disabled ? ' (deactivated)' : ''}<span class="subtitle">${esc(subtitle(n))}</span>${n.notesInFlow && n.notes ? `<span class="subtitle" style="font-style:italic">${esc(n.notes)}</span>` : ''}</div>
      ${E.readOnly ? '' : `<div class="node-actions"><button data-a="run" title="Test step">${icon('play')}</button><button data-a="disable" title="Deactivate (D)">${icon('power')}</button><button data-a="delete" title="Delete">${icon('trash')}</button></div>`}</div>`);
    const nin = inputsOf(n);
    for (let i = 0; i < nin; i++) {
      const hd = h(`<div class="handle in" data-in="${i}"></div>`);
      hd.style.left = '0px';
      hd.style.top = (NODE * (i + 1)) / (nin + 1) + 'px';
      el.append(hd);
    }
    const outs = outputsOf(n);
    const used = E.wf.connections[n.name]?.main || [];
    outs.forEach((label, i) => {
      const y = (NODE * (i + 1)) / (outs.length + 1);
      const hd = h(`<div class="handle out" data-out="${i}" style="left:${NODE}px;top:${y}px"></div>`);
      el.append(hd);
      if (label) el.append(h(`<div class="handle-label" style="left:${NODE + 12}px;top:${y - 11}px;${label === 'error' ? 'color:var(--err)' : ''}">${esc(label)}</div>`));
      if (!E.readOnly && !(used[i] && used[i].length)) {
        const plus = h(`<div class="plus" style="left:${NODE + 7}px;top:${y}px;${label ? 'margin-top:12px' : ''}"><span>${icon('plus')}</span></div>`);
        plus.onclick = (e) => { e.stopPropagation(); Creator.open({ from: n.name, out: i }); };
        el.append(plus);
      }
    });
    const aiIns = d.aiInputs || [];
    if (!E.readOnly && aiIns.length) {
      const row = h('<div class="sub-adds"></div>');
      aiIns.forEach((t) => {
        const b = h(`<button class="sub-add" type="button" title="Add ${esc(AI_LABEL[t] || t)}">${icon('plus')}<span>${esc(AI_LABEL[t] || t.slice(3))}</span></button>`);
        b.onclick = (e) => { e.stopPropagation(); Creator.open({ subFor: n.name, subType: t }); };
        row.append(b);
      });
      el.append(row);
    }
    world.append(el);
  }
  Editor.applyView();
}

const Canvas = {
  bind() {
    const cv = E.canvas;
    const toWorld = (e) => {
      const r = cv.getBoundingClientRect();
      return [(e.clientX - r.left - E.view.x) / E.view.k, (e.clientY - r.top - E.view.y) / E.view.k];
    };
    cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = cv.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) Editor.zoom(Math.exp(-e.deltaY * 0.0022), e.clientX - r.left, e.clientY - r.top);
      else { E.view.x -= e.deltaX; E.view.y -= e.deltaY; Editor.applyView(); }
    }, { passive: false });

    cv.addEventListener('dblclick', (e) => {
      const el = e.target.closest('[data-node]');
      if (!el) return;
      const n = nodeBy(el.dataset.node);
      if (n.type === 'n8n-nodes-base.stickyNote') return Canvas.editSticky(n);
      NDV.open(n.name);
    });

    cv.addEventListener('pointerdown', (e) => {
      if (e.button === 2) return;
      if (e.target.closest('.canvas-ctl,.run-bar,.listen,[data-add],.canvas-empty,.plus,.sub-add')) return;
      const action = e.target.closest('[data-a]');
      const nodeEl = e.target.closest('[data-node]');
      if (action && nodeEl) {
        e.stopPropagation();
        const name = nodeEl.dataset.node;
        if (action.dataset.a === 'run') Editor.run(name);
        if (action.dataset.a === 'delete') deleteNodes([name]);
        if (action.dataset.a === 'disable') { remember(); const n = nodeBy(name); n.disabled = !n.disabled; dirty(); }
        return;
      }
      const outH = e.target.closest('.handle.out');
      if (outH && nodeEl && !E.readOnly) return Canvas.dragConnection(e, nodeEl.dataset.node, Number(outH.dataset.out), toWorld);
      if (nodeEl) return Canvas.dragNodes(e, nodeEl.dataset.node, toWorld);
      E.selEdge = null;
      if (e.shiftKey) return Canvas.rubberBand(e, toWorld);
      Canvas.pan(e);
    });

    cv.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const nodeEl = e.target.closest('[data-node]');
      if (E.readOnly) return;
      if (nodeEl) {
        const name = nodeEl.dataset.node;
        if (!E.sel.has(name)) { E.sel = new Set([name]); draw(); }
        const n = nodeBy(name);
        return menu(e.clientX, e.clientY, [
          { label: 'Open', icon: 'edit', action: () => NDV.open(name) },
          { label: 'Test step', icon: 'play', action: () => Editor.run(name) },
          { label: 'Rename', icon: 'tag', action: async () => { const v = await prompt('Rename node', 'Name', name); if (v) { remember(); renameNode(name, v); dirty(); } } },
          { label: n.disabled ? 'Activate' : 'Deactivate', icon: 'power', action: () => { remember(); n.disabled = !n.disabled; dirty(); } },
          { label: E.wf.pinData[name] ? 'Unpin data' : 'Pin data', icon: 'pin', action: () => NDV.togglePin(name) },
          { label: 'Duplicate', icon: 'copy', action: () => { const t = Editor.copySelection(); if (t) Editor.pasteJson(t); } },
          { label: 'Copy', icon: 'copy', action: () => { const t = Editor.copySelection(); if (t) copyText(t); } },
          '-',
          { label: 'Delete', icon: 'trash', danger: true, action: () => deleteNodes([...E.sel]) },
        ]);
      }
      const [wx, wy] = toWorld(e);
      menu(e.clientX, e.clientY, [
        { label: 'Add node', icon: 'plus', action: () => Creator.open({ pos: snapPos(wx, wy) }) },
        { label: 'Add sticky note', icon: 'note', action: () => { remember(); E.wf.nodes.push(newNode('n8n-nodes-base.stickyNote', snapPos(wx, wy))); dirty(); } },
        { label: 'Tidy up', icon: 'layers', action: () => Editor.tidy() },
        { label: 'Select all', icon: 'check', action: () => { E.sel = new Set(E.wf.nodes.map((n) => n.name)); draw(); } },
      ]);
    });
  },

  pan(e) {
    const sx = e.clientX, sy = e.clientY, vx = E.view.x, vy = E.view.y;
    let moved = false;
    E.canvas.classList.add('panning');
    const mv = (ev) => { moved = moved || Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 3; E.view.x = vx + ev.clientX - sx; E.view.y = vy + ev.clientY - sy; Editor.applyView(); };
    const up = () => {
      E.canvas.classList.remove('panning');
      removeEventListener('pointermove', mv);
      removeEventListener('pointerup', up);
      if (!moved && (E.sel.size || E.selEdge)) { E.sel.clear(); E.selEdge = null; draw(); }
    };
    addEventListener('pointermove', mv);
    addEventListener('pointerup', up);
  },

  rubberBand(e, toWorld) {
    const [x0, y0] = toWorld(e);
    const rb = h('<div class="rubber"></div>');
    E.world.append(rb);
    const mv = (ev) => {
      const [x1, y1] = toWorld(ev);
      const [l, t, r, b] = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
      Object.assign(rb.style, { left: l + 'px', top: t + 'px', width: r - l + 'px', height: b - t + 'px' });
      E.sel = new Set(E.wf.nodes.filter((n) => n.position[0] + NODE > l && n.position[0] < r && n.position[1] + NODE > t && n.position[1] < b).map((n) => n.name));
      E.world.querySelectorAll('[data-node]').forEach((el) => el.classList.toggle('sel', E.sel.has(el.dataset.node)));
    };
    const up = () => { rb.remove(); removeEventListener('pointermove', mv); removeEventListener('pointerup', up); draw(); };
    addEventListener('pointermove', mv);
    addEventListener('pointerup', up);
  },

  dragNodes(e, name, toWorld) {
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      E.sel.has(name) ? E.sel.delete(name) : E.sel.add(name);
      return draw();
    }
    if (!E.sel.has(name)) { E.sel = new Set([name]); E.selEdge = null; draw(); }
    if (E.readOnly) return;
    const [sx, sy] = toWorld(e);
    const nodes = E.wf.nodes.filter((n) => E.sel.has(n.name));
    const start = nodes.map((n) => [...n.position]);
    let moved = false;
    const mv = (ev) => {
      const [x, y] = toWorld(ev);
      if (!moved && Math.abs(x - sx) + Math.abs(y - sy) < 4) return;
      if (!moved) { remember(); moved = true; }
      nodes.forEach((n, i) => (n.position = snapPos(start[i][0] + x - sx, start[i][1] + y - sy)));
      draw();
    };
    const up = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', up); if (moved) dirty(); };
    addEventListener('pointermove', mv);
    addEventListener('pointerup', up);
  },

  dragConnection(e, src, out, toWorld) {
    e.stopPropagation();
    const sn = nodeBy(src);
    const [x1, y1] = handlePos(sn, 'out', out);
    const temp = E.svg.querySelector('.temp');
    let last = [x1, y1];
    const mv = (ev) => { last = toWorld(ev); temp.setAttribute('d', edgePath(x1, y1, last[0], last[1])); };
    const up = (ev) => {
      removeEventListener('pointermove', mv);
      removeEventListener('pointerup', up);
      temp.setAttribute('d', '');
      const target = document.elementFromPoint(ev.clientX, ev.clientY);
      const nodeEl = target?.closest?.('[data-node]');
      if (nodeEl && nodeEl.dataset.node !== src) {
        const tn = nodeBy(nodeEl.dataset.node);
        if (!inputsOf(tn)) return toast('This node has no inputs', '', true);
        const inH = target.closest('.handle.in');
        let idx = inH ? Number(inH.dataset.in) : 0;
        if (!inH && inputsOf(tn) > 1) { // choose the input closest to the drop point
          const ys = Array.from({ length: inputsOf(tn) }, (_, i) => handlePos(tn, 'in', i)[1]);
          idx = ys.reduce((b, y, i) => (Math.abs(y - last[1]) < Math.abs(ys[b] - last[1]) ? i : b), 0);
        }
        remember();
        connect(src, out, tn.name, idx);
        dirty();
      } else if (!nodeEl && Math.abs(last[0] - x1) + Math.abs(last[1] - y1) > 30) {
        Creator.open({ from: src, out, pos: snapPos(last[0], last[1] - NODE / 2) });
      }
    };
    addEventListener('pointermove', mv);
    addEventListener('pointerup', up);
  },

  async editSticky(n) {
    if (E.readOnly) return;
    const body = h(`<div><textarea class="input" rows="8"></textarea><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">
      <div><label class="label">Width</label><input class="input" type="number" data-w value="${n.parameters.width || 260}"></div>
      <div><label class="label">Height</label><input class="input" type="number" data-h value="${n.parameters.height || 180}"></div></div></div>`);
    $('textarea', body).value = n.parameters.content ?? '';
    const ok = await modal('Edit sticky note', body, [{ label: 'Delete', cls: 'danger', value: 'del' }, { label: 'Save', cls: 'primary', value: 'ok' }]);
    if (ok === 'del') return deleteNodes([n.name]);
    if (ok !== 'ok') return;
    remember();
    Object.assign(n.parameters, { content: $('textarea', body).value, width: Number($('[data-w]', body).value), height: Number($('[data-h]', body).value) });
    dirty();
  },
};

// keyboard & clipboard
document.addEventListener('keydown', (e) => {
  if (!E || !document.contains(E.root) || $('.overlay') || $('.ndv')) return;
  if (e.target.closest('input,textarea,select,[contenteditable]')) return;
  const mod = e.ctrlKey || e.metaKey;
  const k = e.key.toLowerCase();
  if (mod && k === 's') { e.preventDefault(); Editor.save(); return; }
  if (E.readOnly) return;
  if (mod && k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if (mod && k === 'y') { e.preventDefault(); redo(); }
  else if (mod && k === 'a') { e.preventDefault(); E.sel = new Set(E.wf.nodes.map((n) => n.name)); draw(); }
  else if (mod && k === 'd') { e.preventDefault(); const t = Editor.copySelection(); if (t) Editor.pasteJson(t); }
  else if (mod && k === 'enter') { e.preventDefault(); Editor.run(); }
  else if (k === 'delete' || k === 'backspace') {
    if (E.selEdge) { remember(); const s = E.selEdge; disconnect(s.src, s.out, s.dst, s.index); E.selEdge = null; dirty(); }
    else deleteNodes([...E.sel]);
  } else if (k === 'tab') { e.preventDefault(); Creator.open(E.sel.size === 1 ? { from: [...E.sel][0], out: 0 } : {}); }
  else if (k === 'enter' && E.sel.size === 1) NDV.open([...E.sel][0]);
  else if (k === 'd' && !mod && E.sel.size) { remember(); E.wf.nodes.filter((n) => E.sel.has(n.name)).forEach((n) => (n.disabled = !n.disabled)); dirty(); }
  else if (k === 'p' && !mod && E.sel.size === 1) NDV.togglePin([...E.sel][0]);
  else if (k === 'f2' && E.sel.size === 1) { const name = [...E.sel][0]; prompt('Rename node', 'Name', name).then((v) => { if (v) { remember(); renameNode(name, v); dirty(); } }); }
  else if (k === '1') Editor.fit();
  else if (k === '0') { E.view.k = 1; Editor.applyView(); }
  else if (k === '+' || k === '=') Editor.zoom(1.2);
  else if (k === '-') Editor.zoom(1 / 1.2);
  else if (k === 's' && e.shiftKey) Editor.addSticky();
});
document.addEventListener('copy', (e) => {
  if (!E || E.readOnly || !document.contains(E.root) || $('.ndv') || e.target.closest?.('input,textarea')) return;
  const t = Editor.copySelection();
  if (!t) return;
  e.clipboardData.setData('text/plain', t);
  e.preventDefault();
  toast(`Copied ${E.sel.size} node${E.sel.size === 1 ? '' : 's'}`);
});
document.addEventListener('paste', (e) => {
  if (!E || E.readOnly || !document.contains(E.root) || $('.ndv') || $('.overlay') || e.target.closest?.('input,textarea')) return;
  if (Editor.pasteJson(e.clipboardData.getData('text/plain'), !E.wf.nodes.length)) e.preventDefault();
});

// =====================================================================
// Node creator
// =====================================================================
// Width of a "+ Type" button under a root plus the gap between buttons (see .sub-add).
const SUB_W = 76;
const AI_LABEL = { ai_languageModel: 'Model', ai_memory: 'Memory', ai_tool: 'Tool', ai_outputParser: 'Parser', ai_embedding: 'Embeddings', ai_document: 'Loader',
  ai_textSplitter: 'Splitter', ai_vectorStore: 'Vector store', ai_retriever: 'Retriever' };
// A vector store can also hang below an agent (as a tool) or a retriever.
const fitsSub = (t, sub) => t.subNodeType === sub || (/\.vectorStore[A-Z]/.test(t.name) && (sub === 'ai_vectorStore' || sub === 'ai_tool'));

const Creator = {
  open(opts = {}) {
    if (E.readOnly) return;
    E.root.querySelector('.creator')?.remove();
    const hasTrigger = E.wf.nodes.some((n) => isTriggerType(n.type));
    const triggersFirst = !hasTrigger && !opts.from && !opts.subType;
    const panel = h(`<div class="creator"><div class="creator-head"><div style="display:flex;align-items:center"><h3 style="flex:1">${opts.subType ? `Add ${esc((AI_LABEL[opts.subType] || 'sub-node').toLowerCase())} to ${esc(opts.subFor)}` : triggersFirst ? 'What triggers this workflow?' : 'What happens next?'}</h3>
      <button class="btn ghost sm icon" data-x>${icon('x')}</button></div><div class="search">${icon('search')}<input class="input" style="width:100%" placeholder="Search nodes…"></div></div><div class="creator-list"></div></div>`);
    E.root.append(panel);
    const input = $('input', panel);
    let active = 0;
    const order = ['Triggers', 'AI', 'Core', 'Flow', 'Data transformation', 'Files', 'Communication', 'Data & Storage'];
    const list = () => {
      const q = input.value.toLowerCase().trim();
      // Name matches rank above description matches; triggers cannot follow another node.
      const score = (t) => (!q ? 0 : t.displayName.toLowerCase().startsWith(q) ? 0 : t.displayName.toLowerCase().includes(q) ? 1 : 2);
      return App.nodeTypes
        .filter((t) => !t.hidden && !(opts.from && isTriggerType(t.name)))
        .filter((t) => (opts.subType ? fitsSub(t, opts.subType) : !t.subNodeType))
        .filter((t) => !q || t.displayName.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q) || t.name.toLowerCase().includes(q))
        .filter((t) => q || !triggersFirst || isTriggerType(t.name))
        .sort((a, b) => (q ? score(a) - score(b) : 0) || order.indexOf(a.category) - order.indexOf(b.category));
    };
    const render = () => {
      const items = list();
      const box = $('.creator-list', panel);
      let cat = null;
      box.innerHTML = '';
      items.forEach((t, i) => {
        if (t.category !== cat) { cat = t.category; box.append(h(`<div class="cat">${esc(cat)}</div>`)); }
        const el = h(`<div class="citem ${i === active ? 'on' : ''}"><div class="ic" style="color:${inkOf(t.color)}">${icon(t.icon)}</div><div><b>${esc(t.displayName)}</b><small>${esc(t.description || '')}</small></div></div>`);
        el.onclick = () => Creator.add(t.name, opts);
        box.append(el);
      });
      if (!items.length) box.append(h('<div class="empty muted">No nodes found. The HTTP Request node can talk to almost any API.</div>'));
      if (triggersFirst && !input.value) {
        const more = h(`<div class="citem"><div class="ic">${icon('arrow')}</div><div><b>Show all nodes</b><small>Add an action without a trigger</small></div></div>`);
        more.onclick = () => { input.value = ' '; render(); };
        box.append(more);
      }
    };
    input.oninput = () => { active = 0; render(); };
    input.onkeydown = (e) => {
      const items = list();
      if (e.key === 'ArrowDown') { active = Math.min(items.length - 1, active + 1); render(); e.preventDefault(); }
      if (e.key === 'ArrowUp') { active = Math.max(0, active - 1); render(); e.preventDefault(); }
      if (e.key === 'Enter' && items[active]) Creator.add(items[active].name, opts);
      if (e.key === 'Escape') panel.remove();
    };
    $('[data-x]', panel).onclick = () => panel.remove();
    render();
    input.focus();
  },
  add(type, opts) {
    E.root.querySelector('.creator')?.remove();
    remember();
    let pos = opts.pos;
    const src = opts.from ? nodeBy(opts.from) : null;
    const root = opts.subFor ? nodeBy(opts.subFor) : null;
    if (root) {
      // Below the root, next to the sub-nodes it already has.
      const taken = Object.entries(E.wf.connections).filter(([, m]) => Object.entries(m).some(([k, l]) => k.startsWith('ai_') && (l || []).flat().some((c) => c?.node === root.name))).length;
      pos = snapPos(root.position[0] - 140 + taken * 140, root.position[1] + 220);
      while (E.wf.nodes.some((x) => Math.abs(x.position[0] - pos[0]) < 60 && Math.abs(x.position[1] - pos[1]) < 60)) pos = [pos[0] + 140, pos[1]];
    }
    if (!pos && src) {
      const outs = outputsOf(src).length;
      pos = snapPos(src.position[0] + 240, src.position[1] + (outs > 1 ? (opts.out - (outs - 1) / 2) * 140 : 0));
      while (E.wf.nodes.some((n) => Math.abs(n.position[0] - pos[0]) < 60 && Math.abs(n.position[1] - pos[1]) < 60)) pos = [pos[0], pos[1] + 140];
    }
    if (!pos) {
      const last = E.wf.nodes.filter((n) => n.type !== 'n8n-nodes-base.stickyNote').sort((a, b) => b.position[0] - a.position[0])[0];
      const r = E.canvas.getBoundingClientRect();
      pos = last ? snapPos(last.position[0] + 240, last.position[1]) : snapPos((r.width / 2 - E.view.x) / E.view.k - NODE / 2, (r.height / 2 - E.view.y) / E.view.k - NODE / 2);
    }
    const n = newNode(type, pos);
    E.wf.nodes.push(n);
    if (src && inputsOf(n)) connect(src.name, opts.out || 0, n.name, 0);
    if (root) {
      (E.wf.connections[n.name] ||= {})[opts.subType] = [[{ node: root.name, type: opts.subType, index: 0 }]];
      // A vector store below an agent works as a tool, below a retriever as a store.
      if (/\.vectorStore[A-Z]/.test(type)) n.parameters.mode = opts.subType === 'ai_tool' ? 'retrieve-as-tool' : 'retrieve';
    }
    E.sel = new Set([n.name]);
    dirty();
    if (!['n8n-nodes-base.manualTrigger', 'n8n-nodes-base.noOp', 'n8n-nodes-base.stickyNote', 'n8n-nodes-base.errorTrigger'].includes(type)) NDV.open(n.name);
  },
};

// =====================================================================
// Node details view
// =====================================================================
const NDV = {
  el: null, name: null, tab: 'params', parent: null, outIdx: 0, runIdx: null, before: null,

  open(name) {
    NDV.close(true);
    NDV.name = name;
    NDV.tab = 'params';
    NDV.parent = null;
    NDV.outIdx = 0;
    NDV.runIdx = null;
    NDV.before = snapshot();
    const n = nodeBy(name);
    const d = typeOf(n.type);
    const noInput = !inputsOf(n);
    NDV.el = h(`<div class="ndv"><div class="ndv-inner ${noInput ? 'no-input' : ''}">
      ${noInput ? '' : '<div class="ndv-col data" data-in></div>'}
      <div class="ndv-col params"><div class="ndv-head"><span style="color:${inkOf(d.color)}">${icon(d.icon)}</span><input class="ndv-name" ${E.readOnly ? 'disabled' : ''}>
        ${E.readOnly ? '' : `<button class="btn primary sm" data-test>${icon('play')}${isHookType(n.type) ? 'Listen for test event' : 'Test step'}</button>`}
        <button class="btn ghost sm icon" data-close title="Close (Esc)">${icon('x')}</button></div>
        <div style="padding:10px 16px 0"><div class="tabs"><button class="tab on" data-t="params">Parameters</button><button class="tab" data-t="settings">Settings</button></div></div>
        <div class="ndv-body" data-params></div></div>
      <div class="ndv-col data" data-out></div></div></div>`);
    document.body.append(NDV.el);
    const nm = $('.ndv-name', NDV.el);
    nm.value = name;
    nm.onchange = () => { NDV.name = renameNode(NDV.name, nm.value); nm.value = NDV.name; };
    nm.onkeydown = (e) => { if (e.key === 'Enter') nm.blur(); };
    $('[data-close]', NDV.el).onclick = () => NDV.close();
    $('[data-test]', NDV.el)?.addEventListener('click', () => Editor.run(NDV.name));
    NDV.el.addEventListener('mousedown', (e) => { if (e.target === NDV.el) NDV.close(); });
    NDV.el.querySelectorAll('[data-t]').forEach((b) => (b.onclick = () => { NDV.tab = b.dataset.t; NDV.el.querySelectorAll('[data-t]').forEach((x) => x.classList.toggle('on', x === b)); NDV.renderParams(); }));
    NDV.key = (e) => { if (e.key === 'Escape' && !$('.overlay')) NDV.close(); };
    document.addEventListener('keydown', NDV.key);
    NDV.renderParams();
    NDV.renderData();
  },

  close(silent) {
    if (!NDV.el) return;
    NDV.el.remove();
    NDV.el = null;
    document.removeEventListener('keydown', NDV.key);
    if (!silent && E && NDV.before !== null) {
      if (snapshot() !== NDV.before) { E.history.push(NDV.before); E.future = []; }
      // Compare with the last saved state: "Test step" may have saved meanwhile.
      if (snapshot() !== E.savedSnap) dirty(); else draw();
    }
  },

  refresh() { if (NDV.el && nodeBy(NDV.name)) NDV.renderData(); },

  renderParams() {
    const n = nodeBy(NDV.name);
    const d = typeOf(n.type);
    const box = $('[data-params]', NDV.el);
    box.innerHTML = '';
    n.parameters ||= {};
    const ctx = { root: n.parameters, rootProps: d.properties || [], readOnly: E.readOnly, change: (re) => { if (re) NDV.renderParams(); } };
    const form = h(`<fieldset style="border:0;margin:0;padding:0" ${E.readOnly ? 'disabled' : ''}></fieldset>`);
    if (NDV.tab === 'settings') {
      const props = [
        { displayName: 'Always Output Data', name: 'alwaysOutputData', type: 'boolean', default: false, description: 'Output an empty item if the node returns nothing' },
        { displayName: 'Execute Once', name: 'executeOnce', type: 'boolean', default: false, description: 'Only process the first input item' },
        { displayName: 'Retry On Fail', name: 'retryOnFail', type: 'boolean', default: false },
        { displayName: 'Max. Tries', name: 'maxTries', type: 'number', default: 3, displayOptions: { show: { retryOnFail: [true] } } },
        { displayName: 'Wait Between Tries (ms)', name: 'waitBetweenTries', type: 'number', default: 1000, displayOptions: { show: { retryOnFail: [true] } } },
        { displayName: 'On Error', name: 'onError', type: 'options', default: 'stopWorkflow', options: [{ name: 'Stop Workflow', value: 'stopWorkflow' }, { name: 'Continue', value: 'continueRegularOutput' }, { name: 'Continue (using error output)', value: 'continueErrorOutput' }] },
        { displayName: 'Notes', name: 'notes', type: 'string', default: '', typeOptions: { rows: 3 }, noExpression: true },
        { displayName: 'Display Note in Flow?', name: 'notesInFlow', type: 'boolean', default: false },
      ];
      Params.render(form, props, n, { root: n, rootProps: props, readOnly: E.readOnly, change: (re) => { if (re) NDV.renderParams(); } });
      form.append(h(`<div class="hint" style="margin-top:18px">Node type <span class="mono">${esc(n.type)}</span> · version ${esc(n.typeVersion)}</div>`));
    } else {
      if (d.description) form.append(h(`<p class="muted" style="margin:0 0 16px;font-size:13px">${esc(d.description)}</p>`));
      if (isHookType(n.type)) {
        const prodBase = hookBase(n.type, false);
        const testBase = hookBase(n.type, true);
        const urls = h(`<div class="p"><div class="lbl">URLs</div><div class="tabs" style="margin-bottom:8px"><button class="tab on" data-u="test">Test URL</button><button class="tab" data-u="prod">Production URL</button></div>
          <div class="keybox"><span class="mono" style="flex:1" data-url></span><button class="btn sm" data-copy>Copy</button></div></div>`);
        const show = (which) => { $('[data-url]', urls).textContent = `${which === 'prod' ? prodBase : testBase}/${hookPath(n)}`; urls.querySelectorAll('[data-u]').forEach((b) => b.classList.toggle('on', b.dataset.u === which)); };
        urls.querySelectorAll('[data-u]').forEach((b) => (b.onclick = () => show(b.dataset.u)));
        $('[data-copy]', urls).onclick = () => copyText($('[data-url]', urls).textContent);
        show('test');
        form.append(urls);
      }
      NDV.credentials(form, n, d);
      Params.render(form, d.properties || [], n.parameters, ctx);
      if (!(d.properties || []).length) form.append(h('<p class="muted">This node has no parameters.</p>'));
    }
    box.append(form);
  },

  credentials(form, n, d) {
    for (const c of d.credentials || []) {
      if (!Params.visible({ displayOptions: c.displayOptions }, n.parameters, d.properties || [], n.parameters, d.properties || [])) continue;
      const type = c.name === '$predefined' ? n.parameters.nodeCredentialType : c.name;
      if (!type) continue;
      const ct = credTypeOf(type);
      const field = h(`<div class="p"><div class="lbl">Credential to connect with${c.required ? '<span class="req">*</span>' : ''}</div><div class="cred-row"><select class="input"><option value="">Loading…</option></select><button class="btn icon" title="Edit credential">${icon('edit')}</button></div></div>`);
      const sel = $('select', field);
      const load = async (selectId) => {
        const all = (await api('GET', '/credentials').catch(() => [])).filter((x) => x.type === type);
        const cur = n.credentials?.[type];
        sel.innerHTML = `<option value="">Select ${esc(ct.displayName)} credential…</option>${all.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}<option value="__new">+ Create new credential</option>`;
        const match = all.find((x) => x.id === (selectId || cur?.id)) || all.find((x) => x.name === cur?.name);
        sel.value = match ? match.id : '';
        if (selectId && match) { n.credentials = { ...(n.credentials || {}), [type]: { id: match.id, name: match.name } }; }
        if (!match && all.length === 1 && !cur) { sel.value = all[0].id; n.credentials = { ...(n.credentials || {}), [type]: { id: all[0].id, name: all[0].name } }; }
      };
      sel.onchange = async () => {
        if (sel.value === '__new') {
          const r = await credentialModal(null, type);
          return load(r?.id);
        }
        if (!sel.value) { if (n.credentials) delete n.credentials[type]; return; }
        n.credentials = { ...(n.credentials || {}), [type]: { id: sel.value, name: sel.selectedOptions[0].textContent } };
      };
      $('button', field).onclick = async () => {
        if (!sel.value || sel.value === '__new') return;
        if (await credentialModal({ id: sel.value, type })) load(sel.value);
      };
      load();
      form.append(field);
    }
  },

  renderData() {
    const n = nodeBy(NDV.name);
    const inBox = $('[data-in]', NDV.el);
    if (inBox) NDV.renderInput(inBox, n);
    NDV.renderOutput($('[data-out]', NDV.el), n);
  },

  modeTabs(onChange) {
    const t = h(`<div class="tabs">${['schema', 'table', 'json'].map((m) => `<button class="tab ${E.dataMode === m ? 'on' : ''}" data-m="${m}">${m[0].toUpperCase() + m.slice(1)}</button>`).join('')}</div>`);
    t.querySelectorAll('[data-m]').forEach((b) => (b.onclick = () => { E.dataMode = b.dataset.m; localStorage.setItem('fw-dm', E.dataMode); onChange(); }));
    return t;
  },

  renderInput(box, n) {
    const parents = parentsOf(n.name);
    box.innerHTML = '';
    const head = h(`<div class="ndv-head"><span class="ttl">Input</span><span class="grow"></span></div>`);
    box.append(head);
    const body = h('<div class="ndv-body"></div>');
    box.append(body);
    if (!parents.length) { body.append(h('<div class="data-empty"><div><b>No input connected</b>Connect a node to this one to see its data here.</div></div>')); return; }
    const withData = parents.filter((p) => lastRun(p.src));
    let p = parents.find((x) => x.src === NDV.parent) || withData[0] || parents[0];
    NDV.parent = p.src;
    if (parents.length > 1) {
      const s = h(`<select class="input" style="width:auto;height:30px;font-size:13px">${parents.map((x) => `<option>${esc(x.src)}</option>`).join('')}</select>`);
      s.value = p.src;
      s.onchange = () => { NDV.parent = s.value; NDV.renderData(); };
      head.insertBefore(s, head.children[1]);
    }
    const t = lastRun(p.src);
    if (!t) {
      const em = h(`<div class="data-empty"><div><b>No input data yet</b>Execute the previous nodes to see what flows into this node.${E.readOnly ? '' : `<br><button class="btn sm" style="margin-top:12px">${icon('play')}Execute previous nodes</button>`}</div></div>`);
      $('button', em)?.addEventListener('click', () => Editor.run(p.src));
      body.append(em);
      return;
    }
    const items = t.data?.main?.[p.out] || [];
    head.append(h(`<span class="muted" style="font-size:12.5px">${items.length} item${items.length === 1 ? '' : 's'}</span>`), NDV.modeTabs(() => NDV.renderData()));
    if (!E.readOnly) body.append(h('<div class="drag-hint">Drag a field onto a parameter to map it.</div>'));
    body.append(dataView(items, E.dataMode, !E.readOnly));
  },

  renderOutput(box, n) {
    box.innerHTML = '';
    const head = h(`<div class="ndv-head"><span class="ttl">Output</span><span class="grow"></span></div>`);
    box.append(head);
    const pinned = E.wf.pinData?.[n.name];
    const runs = E.runData?.[n.name] || [];
    const t = runs.length ? runs[NDV.runIdx ?? runs.length - 1] : null;
    if (!E.readOnly) {
      const pinBtn = h(`<button class="btn sm ${pinned ? 'primary' : ''}" title="Pin data (P)">${icon('pin')}${pinned ? 'Unpin' : 'Pin'}</button>`);
      pinBtn.onclick = () => NDV.togglePin(n.name);
      const editBtn = h(`<button class="btn sm icon" title="Edit output / set mock data">${icon('edit')}</button>`);
      editBtn.onclick = () => NDV.editPin(n.name);
      head.append(pinBtn, editBtn);
    }
    if (runs.length > 1 && !pinned) {
      const rs = h(`<select class="input" style="width:auto;height:30px;font-size:12.5px">${runs.map((_, i) => `<option value="${i}">Run ${i + 1} of ${runs.length}</option>`).join('')}</select>`);
      rs.value = String(NDV.runIdx ?? runs.length - 1);
      rs.onchange = () => { NDV.runIdx = Number(rs.value); NDV.renderData(); };
      head.insertBefore(rs, head.children[1]);
    }
    const body = h('<div class="ndv-body"></div>');
    if (pinned) {
      head.append(NDV.modeTabs(() => NDV.renderData()));
      body.append(h(`<div class="notice" style="margin-bottom:12px">${icon('pin')} This data is pinned. Test runs use it instead of executing the node.</div>`));
      body.append(dataView(pinned.map((j) => (j && j.json ? j : { json: j })), E.dataMode, false));
      box.append(body);
      return;
    }
    if (!t) {
      body.append(h(`<div class="data-empty"><div><b>No output data</b>${E.readOnly ? 'This node did not run in this execution.' : 'Click “Test step” to run the workflow up to this node.'}</div></div>`));
      box.append(body);
      return;
    }
    if (t.error) body.append(h(`<div class="err-box" style="margin-bottom:12px"><b>${t.executionStatus === 'error' ? 'Node failed' : 'Error (continued)'}</b>\n${esc(t.error.message)}</div>`));
    const outs = t.data?.main || [];
    if (outs.length > 1) {
      const labels = outputsOf(n);
      const tabs = h('<div class="out-tabs"></div>');
      outs.forEach((o, i) => {
        const b = h(`<button class="chip ${i === NDV.outIdx ? 'on' : ''}">${esc(labels[i] || 'Output ' + i)} · ${o?.length || 0}</button>`);
        b.onclick = () => { NDV.outIdx = i; NDV.renderData(); };
        tabs.append(b);
      });
      box.append(tabs);
    }
    const items = outs[Math.min(NDV.outIdx, Math.max(0, outs.length - 1))] || [];
    head.insertBefore(h(`<span class="muted" style="font-size:12.5px">${items.length} item${items.length === 1 ? '' : 's'} · ${t.executionTime} ms</span>`), head.children[1]);
    head.append(NDV.modeTabs(() => NDV.renderData()));
    if (t.executionStatus !== 'error' || items.length) body.append(dataView(items, E.dataMode, false));
    box.append(body);
  },

  togglePin(name) {
    if (E.readOnly) return;
    remember();
    if (E.wf.pinData[name]) delete E.wf.pinData[name];
    else {
      const t = lastRun(name);
      const items = t?.data?.main?.[0];
      if (!items) { E.history.pop(); return toast('Nothing to pin', 'Run the node first or set data with the edit button.', true); }
      E.wf.pinData[name] = items.map((i) => ({ json: i.json }));
    }
    dirty();
    NDV.refresh();
  },

  async editPin(name) {
    const cur = E.wf.pinData[name] || lastRun(name)?.data?.main?.[0] || [{ json: {} }];
    const body = h('<div><p class="muted" style="margin-top:0">Set the output of this node as JSON (array of objects). It will be pinned.</p><textarea class="input code" rows="16"></textarea></div>');
    $('textarea', body).value = JSON.stringify(cur.map((i) => (i && i.json ? i.json : i)), null, 2);
    const r = await modal('Edit output', body, [{ label: 'Cancel', value: null }, { label: 'Save & pin', cls: 'primary', action: () => { const v = JSON.parse($('textarea', body).value); return Array.isArray(v) ? v : [v]; } }], { width: 640 });
    if (!r) return;
    remember();
    E.wf.pinData[name] = r.map((j) => ({ json: j }));
    dirty();
    NDV.refresh();
  },
};

// ---------- data views ----------
function exprPath(path) {
  return '$json' + path.map((k) => (typeof k === 'number' ? `[${k}]` : /^[A-Za-z_$][\w$]*$/.test(k) ? '.' + k : `['${k.replace(/'/g, "\\'")}']`)).join('');
}
function typeName(v) { return v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v; }
function draggable(el, path, value) {
  el.draggable = true;
  el.addEventListener('dragstart', (e) => {
    const expr = exprPath(path);
    const t = typeName(value);
    e.dataTransfer.setData('text/plain', `{{ ${expr} }}`);
    e.dataTransfer.setData('application/x-fw-field', JSON.stringify({ expr, name: String(path[path.length - 1] ?? 'field'), type: ['string', 'number', 'boolean', 'array', 'object'].includes(t) ? t : 'string' }));
  });
}

function dataView(items, mode, drag) {
  const box = h('<div></div>');
  if (!items.length) { box.append(h('<div class="data-empty"><div><b>No items</b>The node returned no data on this output.</div></div>')); return box; }
  if (mode === 'json') {
    let s = JSON.stringify(items.map((i) => i.json), null, 2);
    if (s.length > 300000) s = s.slice(0, 300000) + '\n… (truncated)';
    box.append(h(`<pre class="json"></pre>`));
    box.firstChild.textContent = s;
  } else if (mode === 'table') {
    const cols = [];
    for (const it of items.slice(0, 500)) for (const k of Object.keys(it.json || {})) if (!cols.includes(k) && cols.length < 60) cols.push(k);
    const tbl = h(`<table class="dtable"><thead><tr><th>#</th>${cols.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody></tbody></table>`);
    if (drag) tbl.querySelectorAll('th').forEach((th, i) => { if (i > 0) draggable(th, [cols[i - 1]], items[0].json?.[cols[i - 1]]); });
    const tb = $('tbody', tbl);
    items.slice(0, 250).forEach((it, i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="n">${i}</td>` + cols.map((c) => {
        const v = it.json?.[c];
        const s = v === undefined ? '' : v === null ? 'null' : typeof v === 'object' ? JSON.stringify(v) : String(v);
        return `<td title="${esc(s.slice(0, 500))}">${esc(s.slice(0, 200))}</td>`;
      }).join('');
      tb.append(tr);
    });
    const wrap = h('<div style="overflow:auto"></div>');
    wrap.append(tbl);
    box.append(wrap);
    if (items.length > 250) box.append(h(`<div class="hint">Showing 250 of ${items.length} items. Switch to JSON to see everything.</div>`));
  } else {
    const first = items[0].json || {};
    const tree = (obj, path) => {
      const ul = document.createElement('ul');
      const entries = Array.isArray(obj) ? (obj.length ? [[0, obj[0]]] : []) : Object.entries(obj);
      for (const [k, v] of entries.slice(0, 200)) {
        const li = document.createElement('li');
        const p = [...path, Array.isArray(obj) ? 0 : k];
        const leaf = v === null || typeof v !== 'object';
        const chip = h(`<span class="sk"><b>${esc(Array.isArray(obj) ? '[0]' : k)}</b><span class="ty">${typeName(v)}${Array.isArray(v) ? `[${v.length}]` : ''}</span>${leaf ? `<span class="v">${esc(String(v).slice(0, 80))}</span>` : ''}</span>`);
        if (drag) draggable(chip, p, v);
        li.append(chip);
        if (!leaf) li.append(tree(v, p));
        ul.append(li);
      }
      return ul;
    };
    const s = h('<div class="schema"></div>');
    if (!Object.keys(first).length) s.append(h(`<div class="hint">${items.length === 1 ? 'The item' : 'The first item'} has no fields yet.</div>`));
    else s.append(tree(first, []));
    box.append(s);
    if (items.length > 1) box.append(h(`<div class="hint" style="margin-top:10px">Schema of the first of ${items.length} items.</div>`));
  }
  const bin = items.find((i) => i.binary && Object.keys(i.binary).length);
  if (bin) {
    const b = h('<div style="margin-top:12px"></div>');
    for (const [k, v] of Object.entries(bin.binary)) {
      const a = h(`<a class="chip" style="display:inline-flex;gap:6px;text-decoration:none;margin:0 6px 6px 0" download="${esc(v.fileName || k)}">${icon('file')} ${esc(k)}: ${esc(v.fileName || '')} · ${esc(v.mimeType || '')} · ${esc(v.fileSize || '')}</a>`);
      a.href = `data:${v.mimeType || 'application/octet-stream'};base64,${v.data}`;
      b.append(a);
    }
    box.append(b);
  }
  return box;
}
